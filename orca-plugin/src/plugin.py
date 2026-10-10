# /// script
# requires-python = ">=3.12"
# dependencies = ["pyserial>=3.5"]
#
# [tool.orcaslicer.plugin]
# name = "INDX Cold Pull"
# description = "Guided cold pull for the Prusa CORE One INDX: save the G-code to a USB drive, or run it over USB serial with live progress and the nozzle's filament type put back afterwards."
# author = "hyiger"
# version = "0.1.0"
# ///
"""INDX Cold Pull for OrcaSlicer.

A script capability that opens an HTML window themed like OrcaSlicer. The procedure itself lives in
the cold_pull_core section below and runs on a worker thread, so a 30-minute pull never blocks the
UI thread and survives the window being closed. The page talks to the plugin only through
on_message (UI thread): it polls for new log lines and state, and sends commands.

  page --{cmd: hello|poll|ports|destinations|context|say|save|run|cancel|force}--> on_message
  page <--{type: init|state|ports|destinations|context|error}-- window.post()

Built by build.py, which inlines src/cold_pull_core.py and src/page.html into this file.
"""
import threading
import traceback

import orca

# @@CORE@@

PAGE = None  # @@PAGE@@

WINDOW_TITLE = "INDX Cold Pull"


def _serial_status():
    try:
        import serial  # noqa: F401
        from serial.tools import list_ports  # noqa: F401
    except Exception as error:
        return False, f"pyserial could not be loaded ({error})"
    return True, ""


def _list_ports():
    if not _serial_status()[0]:
        return []
    from serial.tools import list_ports

    ports = []
    for info in list_ports.comports():
        description = info.product or (info.description if info.description not in (None, "", "n/a") else "")
        # The serial number tells two printers apart when one replaces the other on the same path.
        ports.append({"device": info.device, "serial": info.serial_number or "", "prusa": info.vid == PRUSA_VID,
                      "label": info.device + (f" ({description})" if description else "")})
    ports.sort(key=lambda port: (not port["prusa"], port["device"]))
    return ports


def _printer_context():
    """The printer selected in OrcaSlicer: whether it is an INDX, and its tool count (4T or 8T)."""
    try:
        preset = orca.host.preset_bundle().current_printer_preset()
        name = preset.name
        model = preset.config_value("printer_model") or ""
        notes = preset.config_value("printer_notes") or ""
        nozzles = preset.config_value("nozzle_diameter") or ""
    except Exception as error:
        return {"printer_name": "", "is_indx": False, "tool_count": 8,
                "note": f"Could not read the printer selected in OrcaSlicer ({error})."}
    count = len([value for value in nozzles.split(",") if value.strip()])
    is_indx = "INDX" in model.upper() or "PRINTER_MODEL_COREONE_INDX" in notes
    return {"printer_name": name, "is_indx": is_indx, "tool_count": count if is_indx and 1 <= count <= 8 else 8}


def _declined(error):
    return isinstance(error, PermissionError) or isinstance(error.__context__, PermissionError)


def _notify(text, warning):
    ui = orca.host.ui
    level = ui.NotificationLevel.WarningNotificationLevel if warning else ui.NotificationLevel.RegularNotificationLevel
    ui.push_notification(level, text)


class IndxColdPull(orca.script.ScriptPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self.window = None
        self.unloading = False
        # A finished run's result until the page has been sent it, so closing the window before the
        # next poll does not lose it. Guarded because runs end on the worker thread and windows
        # close on the UI thread.
        self._result_lock = threading.Lock()
        self._pending_result = None
        self.session = ColdPullSession(on_finish=self._on_run_finished)

    def get_name(self):
        return "INDX Cold Pull"

    def execute(self):
        # There is no call to raise an open window, so reopen it: the page picks the session back up.
        old = self.window
        if old is not None and old.is_open():
            self.window = None  # so its on_close does not count as the user closing the tool
            old.close()
        self._open_window()
        return orca.ExecutionResult.success("INDX Cold Pull opened.")

    def on_unload(self):
        # Unload, reload or app exit. The host closes the window itself, without on_close.
        self.unloading = True
        self.window = None
        self.session.shutdown(timeout=3.0)

    # ---- window ----

    def _open_window(self):
        holder = {}

        def on_message(message):
            self._handle_message(message, holder.get("window"))

        def on_close():
            with self._result_lock:
                if self.window is not holder.get("window"):
                    return
                self.window = None
                undelivered, self._pending_result = self._pending_result, None
            if self.unloading:
                return
            if undelivered is not None:
                self._notify_result(*undelivered)
            elif self.session.running:
                _notify("INDX cold pull is still running. Open INDX Cold Pull again from the Plugins "
                        "dialog or the Speed Dial to follow it or cancel it.", warning=False)

        window = orca.host.ui.create_window(html=PAGE, title=WINDOW_TITLE, width=640, height=820,
                                            on_message=on_message, on_close=on_close)
        holder["window"] = window
        self.window = window

    def _handle_message(self, message, window):
        try:
            reply = self._dispatch(message if isinstance(message, dict) else {})
        except Exception as error:
            traceback.print_exc()
            reply = {"type": "error", "message": f"{type(error).__name__}: {error}"}
        if reply is not None and window is not None and window.is_open():
            window.post(reply)

    def _snapshot(self, epoch=-1, count=0):
        """The session state for a reply to the page. Any reply showing the run finished delivers its
        result, so the pending notification for it is dropped."""
        snapshot = self.session.snapshot(epoch, count)
        if not snapshot["running"]:
            with self._result_lock:
                self._pending_result = None
        return snapshot

    def _state(self, message):
        # The printer context rides along with every poll, so the page follows a printer change
        # in OrcaSlicer however it happens, without depending on a particular notification.
        return {"type": "state", "state": self._snapshot(message.get("epoch", -1), message.get("count", 0)),
                "context": _printer_context()}

    @staticmethod
    def _requested_settings(message, context):
        """The settings to act on, or (None, reason) when the page asked for a nozzle the selected
        printer does not have. Never clamped: the filament-type confirmation belongs to the nozzle
        the user picked, so quietly moving to another one could mark an unconfirmed nozzle FLEX."""
        try:
            nozzle = int(message.get("nozzle"))
        except (TypeError, ValueError):
            nozzle = 0
        count = context["tool_count"]
        if not 1 <= nozzle <= count:
            return None, (f"nozzle {message.get('nozzle')} is not on the printer selected in OrcaSlicer, which has "
                          f"{count} nozzles. Pick the nozzle again and confirm its filament type.")
        return clamp_settings(nozzle, message.get("flush"), message.get("pull"), count), None

    def _dispatch(self, message):
        command = message.get("cmd")
        if command == "hello":
            available, reason = _serial_status()
            return {"type": "init", "context": _printer_context(),
                    "serial": {"available": available, "reason": reason},
                    "ports": _list_ports(), "destinations": list_destinations(),
                    "state": self._snapshot()}
        if command == "poll":
            return self._state(message)
        if command == "ports":
            return {"type": "ports", "ports": _list_ports()}
        if command == "destinations":
            return {"type": "destinations", "destinations": list_destinations()}
        if command == "context":
            return {"type": "context", "context": _printer_context()}
        if command == "say":
            self.session.say(str(message.get("text", "")), str(message.get("cls", "")))
            return self._state(message)
        if command == "save":
            self._save(message)
            return self._state(message)
        if command == "run":
            self._start(message)
            return self._state(message)
        if command == "cancel":
            self.session.request_cancel()
            return self._state(message)
        if command == "force":
            self.session.request_force_stop()
            return self._state(message)
        return {"type": "error", "message": f"unknown command {command!r}"}

    # ---- G-code file ----

    def _save(self, message):
        if self.session.running:
            self.session.say("A serial run is in progress; save the file once it has finished.", "err")
            return
        context = _printer_context()
        tool_count = context["tool_count"]
        s, refused = self._requested_settings(message, context)
        if s is None:
            self.session.say(f"Not saved: {refused}", "err")
            return
        # Only a folder the plugin itself offered: the page names a destination, never a path.
        dest = {d["id"]: d for d in list_destinations()}.get(message.get("dest"))
        if dest is None:
            self.session.say("Not saved: that destination is no longer available. Refresh the list and pick another.", "err")
            return
        save_all = bool(message.get("all"))
        name = ALL_ZIP_NAME if save_all else gcode_filename(s["nozzle"])
        where = f"{dest['label']} (drive)" if dest["kind"] == "drive" else dest["label"]
        try:
            if save_all:
                write_all_zip(dest["id"], s, tool_count)
            else:
                write_gcode(dest["id"], s)
        except Exception as error:
            if _declined(error):
                self.session.say(f"Not saved: OrcaSlicer's permission prompt for {name} in {where} was declined.", "err")
            else:
                self.session.say(f"Not saved: {getattr(error, 'strerror', None) or error}", "err")
            return
        if save_all:
            self.session.say(f"Saved {name} to {where} - one file per nozzle, flush {s['flush']}C, pull {s['pull']}C. "
                             "The printer cannot open a .zip: unzip it into a folder on the USB drive.", "good")
            self.session.say("Each file marks its own nozzle FLEX. Note every nozzle's current filament type before you "
                             "run any of them: the confirmation above was for one nozzle.", "err")
            self.session.set_restore(False, None, True, None)
        else:
            follow = ("Eject the drive and start the file on the printer." if dest["kind"] == "drive"
                      else "Copy it to a USB drive and start it on the printer.")
            self.session.say(f"Saved {name} to {where}. {follow}", "good")
            self.session.set_restore(False, None, True, s["tool"])

    # ---- USB serial ----

    def _start(self, message):
        if self.session.running:
            return
        s, refused = self._requested_settings(message, _printer_context())
        if s is None:
            self.session.say(f"Not started: {refused}", "err")
            return
        device = message.get("port") or ""
        self.session.begin_attempt(s)
        available, reason = _serial_status()
        if not available:
            self.session.fail_attempt(s, f"USB Serial is unavailable: {reason}")
            return
        current = {port["device"]: port for port in _list_ports()}.get(device)
        if current is None:
            self.session.fail_attempt(s, f"{device or 'No port'} is not connected. Refresh the port list and pick the printer.")
            return
        # The checklist was confirmed for the printer the page saw on this path. Without a USB serial
        # number a replacement on the same path cannot be told apart, so such a port is not used;
        # Prusa printers always report one. A different serial number means the printer was
        # replaced or reconnected since, so that confirmation no longer holds.
        if not current["serial"]:
            self.session.fail_attempt(s, f"{device} does not report a USB serial number, so the plugin cannot tell "
                                         "whether it is the printer the checklist was confirmed for. Pick the printer's port.")
            return
        if current["serial"] != (message.get("serial") or ""):
            self.session.fail_attempt(s, f"The printer on {device} is not the one the checklist was confirmed for: it was "
                                         "replaced or reconnected. Refresh the port list and confirm the checklist again.")
            return
        # Opened here, on the UI thread, so OrcaSlicer's permission prompt can show; the worker
        # thread only reads and writes the open port.
        try:
            import serial
            port = serial.Serial(device, BAUD, timeout=SLICE_S, write_timeout=5)
        except Exception as error:
            if _declined(error):
                self.session.fail_attempt(s, f"OrcaSlicer's permission prompt for {device} was declined, so the port "
                                             "was not opened. Run again and answer Yes to allow it.")
            else:
                self.session.fail_attempt(s, str(error))
            return
        self.session.start_run(port, s)

    def _on_run_finished(self, outcome, restore, reason=None):
        # Worker thread, before the run reads as finished. With the window open the page shows the
        # result at its next poll, unless the window closes first; closed, say it now.
        if self.unloading:
            return
        with self._result_lock:
            if self.window is not None and self.window.is_open():
                self._pending_result = (outcome, restore, reason)
                return
        self._notify_result(outcome, restore, reason)

    def _notify_result(self, outcome, restore, reason):
        tool = restore["tool"]
        headline = {"completed": "INDX cold pull complete.", "canceled": "INDX cold pull canceled.",
                    "force-stopped": "INDX cold pull force-stopped: reset the printer before continuing.",
                    "rejected": (f"INDX cold pull refused: {reason}" if reason else
                                 "INDX cold pull did not start. Open INDX Cold Pull for the reason.")
                    }.get(outcome, "INDX cold pull failed.")
        owed = "Before your next print: Filament Sensor on, PTFE tube refitted"
        if restore["fil_changed"]:
            if not restore["auto_restored"]:
                owed += f", nozzle {tool + 1} filament type set back to {restore['fil_name']} by hand"
            elif outcome != "completed":
                owed += f", and check nozzle {tool + 1}'s filament type (it may still be FLEX)"
        try:
            _notify(f"{headline} {owed}.", warning=outcome != "completed")
        except Exception:
            traceback.print_exc()


@orca.plugin
class IndxColdPullPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(IndxColdPull)
