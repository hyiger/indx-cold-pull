"""The built plugin (dist/indx_cold_pull.py) against stand-ins for OrcaSlicer's `orca` module and pyserial.

Covers what only the glue does: registration, the window and its message protocol, printer detection
from the preset, saving to an offered destination only, opening the port, and the close/unload paths.
"""
import importlib.util
import os
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import build  # noqa: E402
from test_session import M115_INDX, printer  # noqa: E402


class FakePreset:
    def __init__(self, name, config):
        self.name = name
        self._config = config

    def config_value(self, key):
        return self._config.get(key)


class FakeWindow:
    def __init__(self, html, title, width, height, on_message, on_close):
        self.html, self.title = html, title
        self.on_message, self.on_close = on_message, on_close
        self.posted = []
        self.open = True

    def post(self, data):
        self.posted.append(data)

    def is_open(self):
        return self.open

    def close(self):
        self.open = False
        self.on_close()

    def ask(self, **message):
        self.on_message(message)
        return self.posted[-1]


def fake_orca(preset):
    orca = types.ModuleType("orca")
    orca.registered = []
    orca.windows = []
    orca.notifications = []

    class ScriptBase:
        def __init__(self):
            pass

    class Base:
        pass

    class ExecutionResult:
        @staticmethod
        def success(message="", data=""):
            return ("success", message)

    def create_window(html, title, width, height, on_message, on_close):
        window = FakeWindow(html, title, width, height, on_message, on_close)
        orca.windows.append(window)
        return window

    orca.script = types.SimpleNamespace(ScriptPluginCapabilityBase=ScriptBase)
    orca.base = Base
    orca.plugin = lambda cls: cls
    orca.register_capability = orca.registered.append
    orca.ExecutionResult = ExecutionResult
    orca.preset = preset  # tests switch it to change the printer selected in OrcaSlicer
    bundle = types.SimpleNamespace(current_printer_preset=lambda: orca.preset)
    levels = types.SimpleNamespace(RegularNotificationLevel="regular", WarningNotificationLevel="warning")
    orca.host = types.SimpleNamespace(
        preset_bundle=lambda: bundle,
        ui=types.SimpleNamespace(create_window=create_window, NotificationLevel=levels,
                                 push_notification=lambda level, text: orca.notifications.append((level, text))))
    return orca


def fake_serial(port_factory):
    serial = types.ModuleType("serial")
    tools = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    info = types.SimpleNamespace(device="/dev/cu.usbmodem1101", vid=0x2C99, product="Original Prusa CORE One INDX",
                                 description="n/a", serial_number="CZPX0001")
    list_ports.ports = [info]  # tests swap it to simulate a replug
    list_ports.comports = lambda: list_ports.ports
    serial.Serial = lambda device, baud, timeout, write_timeout: port_factory()
    serial.tools = tools
    tools.list_ports = list_ports
    return {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports}


INDX_8T = FakePreset("Prusa CORE One INDX 8T HF 0.4 nozzle",
                     {"printer_model": "Prusa CORE One INDX 8T", "nozzle_diameter": ",".join(["0.4"] * 8)})
INDX_4T = FakePreset("Prusa CORE One INDX 4T HF 0.4 nozzle",
                     {"printer_model": "Prusa CORE One INDX 4T", "nozzle_diameter": "0.4,0.4,0.4,0.4"})
MK4 = FakePreset("Prusa MK4S HF0.4", {"printer_model": "MK4S", "nozzle_diameter": "0.4"})


class PluginGlue(unittest.TestCase):
    def load(self, preset=INDX_8T, port_factory=None):
        built = build.build()
        modules = {"orca": fake_orca(preset)}
        if port_factory is not None:
            modules.update(fake_serial(port_factory))
        saved = {name: sys.modules.get(name) for name in [*modules, "serial", "serial.tools", "serial.tools.list_ports"]}
        sys.modules.update(modules)
        if port_factory is None:
            for name in ("serial", "serial.tools", "serial.tools.list_ports"):
                sys.modules[name] = None  # import fails, as with pyserial missing

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.addCleanup(restore)
        spec = importlib.util.spec_from_file_location("indx_cold_pull_under_test", built)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        orca = modules["orca"]
        self.assertEqual(len(orca.registered), 0)
        module.IndxColdPullPlugin().register_capabilities()
        self.assertEqual(orca.registered, [module.IndxColdPull])
        capability = module.IndxColdPull()
        self.assertEqual(capability.get_name(), "INDX Cold Pull")
        self.assertEqual(capability.execute(), ("success", "INDX Cold Pull opened."))
        return module, orca, capability, orca.windows[-1]

    def test_window_init_reflects_the_orca_printer(self):
        module, orca, capability, window = self.load(preset=INDX_4T)
        self.assertEqual((window.title, window.html), ("INDX Cold Pull", module.PAGE))
        init = window.ask(cmd="hello")
        self.assertEqual(init["type"], "init")
        self.assertEqual(init["context"], {"printer_name": "Prusa CORE One INDX 4T HF 0.4 nozzle", "is_indx": True,
                                           "tool_count": 4})
        self.assertEqual(init["serial"]["available"], False)
        self.assertEqual(init["ports"], [])

    def test_non_indx_printer_falls_back_to_eight_nozzles(self):
        _, _, _, window = self.load(preset=MK4)
        self.assertEqual(window.ask(cmd="context")["context"],
                         {"printer_name": "Prusa MK4S HF0.4", "is_indx": False, "tool_count": 8})

    def test_save_writes_only_to_an_offered_destination(self):
        module, _, _, window = self.load()
        with tempfile.TemporaryDirectory() as folder:
            module.list_destinations = lambda: [{"id": folder, "label": "PRUSA", "kind": "drive"}]
            state = window.ask(cmd="save", all=False, nozzle=3, flush=290, pull=80, dest=folder)["state"]
            self.assertEqual(os.listdir(folder), ["cold_pull_nozzle3.gcode"])
            self.assertEqual(state["lines"][-1]["text"],
                             "Saved cold_pull_nozzle3.gcode to PRUSA (drive). Eject the drive and start the file on the printer.")
            self.assertEqual(state["restore"]["tool"], 2)
            state = window.ask(cmd="save", all=False, nozzle=3, flush=290, pull=80, dest="/etc",
                               epoch=state["epoch"], count=state["count"])["state"]
            self.assertTrue(state["lines"][-1]["text"].startswith("Not saved: that destination is no longer available"))
            self.assertEqual(sorted(os.listdir(folder)), ["cold_pull_nozzle3.gcode"])

    def test_serial_run_through_the_window_then_notifies_when_closed(self):
        port = printer()
        module, orca, capability, window = self.load(port_factory=lambda: port)
        init = window.ask(cmd="hello")
        self.assertEqual(init["ports"][0]["device"], "/dev/cu.usbmodem1101")
        self.assertEqual(init["ports"][0]["serial"], "CZPX0001")
        self.assertTrue(init["ports"][0]["prusa"])
        window.ask(cmd="run", nozzle=2, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        capability.session.join(10)
        state = window.ask(cmd="poll", epoch=-1, count=0)["state"]
        self.assertFalse(state["running"])
        self.assertEqual(state["outcome"], "completed")
        self.assertEqual(port.sent[-1], 'M865 S"PETG" L1')
        self.assertEqual(orca.notifications, [])  # the window was open, so the page reported it

    def test_closing_mid_run_keeps_the_run_and_reopening_reattaches(self):
        port = printer(hang_on="M109 S290")
        module, orca, capability, window = self.load(port_factory=lambda: port)
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        time.sleep(0.1)
        window.close()
        self.assertTrue(capability.session.running)
        self.assertEqual(orca.notifications[0][0], "regular")
        capability.execute()
        reopened = orca.windows[-1]
        self.assertIsNot(reopened, window)
        snapshot = reopened.ask(cmd="hello")["state"]
        self.assertTrue(snapshot["running"])
        self.assertEqual(snapshot["run_settings"]["nozzle"], 1)
        reopened.close()
        capability.session.request_force_stop()
        capability.session.join(5)
        self.assertEqual(orca.notifications[-1][0], "warning")
        self.assertIn("force-stopped", orca.notifications[-1][1])

    def test_refusal_notification_names_the_actual_reason(self):
        def slow_identify(line, _port):
            if line == "M115":
                time.sleep(0.3)  # long enough to close the window before the gate decides

        port = printer(hook=slow_identify, disabled_docks=(0,))
        _, orca, capability, window = self.load(port_factory=lambda: port)
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        window.close()
        capability.session.join(5)
        self.assertEqual(capability.session.last_outcome, "rejected")
        self.assertEqual(capability.session.last_reason, "nozzle 1's dock is not enabled on this printer.")
        self.assertEqual(orca.notifications[-1][0], "warning")
        self.assertTrue(orca.notifications[-1][1].startswith(
            "INDX cold pull refused: nozzle 1's dock is not enabled on this printer."))
        self.assertNotIn("not an INDX", orca.notifications[-1][1])

    def test_result_finished_between_polls_is_notified_when_the_window_closes(self):
        port = printer(hook=lambda line, _port: time.sleep(0.2) if line == "M115" else None)
        _, orca, capability, window = self.load(port_factory=lambda: port)
        state = window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        self.assertTrue(state["state"]["running"])
        capability.session.join(5)  # finishes while the window is open, before any poll
        self.assertEqual(orca.notifications, [])
        window.close()
        self.assertEqual(orca.notifications, [("regular", orca.notifications[0][1])])
        self.assertTrue(orca.notifications[0][1].startswith("INDX cold pull complete."))

    def test_result_already_shown_by_the_page_is_not_notified_again(self):
        port = printer()
        _, orca, capability, window = self.load(port_factory=lambda: port)
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        capability.session.join(5)
        self.assertFalse(window.ask(cmd="poll", epoch=-1, count=0)["state"]["running"])
        window.close()
        self.assertEqual(orca.notifications, [])

    def test_result_shown_by_a_reopened_window_is_not_notified_again(self):
        port = printer(hook=lambda line, _port: time.sleep(0.2) if line == "M115" else None)
        _, orca, capability, window = self.load(port_factory=lambda: port)
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        capability.session.join(5)  # finishes between polls
        capability.execute()        # reopening replaces the old window without a poll
        reopened = orca.windows[-1]
        self.assertFalse(reopened.ask(cmd="hello")["state"]["running"])
        reopened.close()
        self.assertEqual(orca.notifications, [])

    def test_no_host_call_is_made_while_holding_the_result_lock(self):
        port = printer(hook=lambda line, _port: time.sleep(0.2) if line == "M115" else None)
        _, orca, capability, window = self.load(port_factory=lambda: port)
        held = []
        original = window.is_open
        window.is_open = lambda: (held.append(capability._result_lock.locked()), original())[1]
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        capability.session.join(5)
        self.assertTrue(held)
        self.assertNotIn(True, held)
        window.close()
        self.assertTrue(orca.notifications[-1][1].startswith("INDX cold pull complete."))

    def test_unload_mid_run_queues_cleanup_and_stays_quiet(self):
        port = printer(hang_on="M109 S290")
        module, orca, capability, window = self.load(port_factory=lambda: port)
        window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101", serial="CZPX0001")
        time.sleep(0.1)
        capability.on_unload()
        self.assertFalse(capability.session.running)
        self.assertEqual(port.sent[-7:], module.cleanup_commands(0, "PETG", True))
        self.assertEqual(orca.notifications, [])

    def test_poll_follows_a_printer_change(self):
        _, orca, _, window = self.load(preset=INDX_8T)
        self.assertEqual(window.ask(cmd="hello")["context"]["tool_count"], 8)
        orca.preset = INDX_4T
        self.assertEqual(window.ask(cmd="poll", epoch=-1, count=0)["context"]["tool_count"], 4)

    def test_nozzle_off_the_selected_printer_is_refused_not_clamped(self):
        opened = []
        module, orca, capability, window = self.load(preset=INDX_8T, port_factory=lambda: opened.append(1))
        orca.preset = INDX_4T  # the user switched printers after picking nozzle 8 on the 8T
        with tempfile.TemporaryDirectory() as folder:
            module.list_destinations = lambda: [{"id": folder, "label": "PRUSA", "kind": "drive"}]
            state = window.ask(cmd="save", all=False, nozzle=8, flush=290, pull=80, dest=folder)["state"]
            self.assertEqual(os.listdir(folder), [])
            self.assertTrue(state["lines"][-1]["text"].startswith("Not saved: nozzle 8 is not on the printer"))
        state = window.ask(cmd="run", nozzle=8, flush=290, pull=80, port="/dev/cu.usbmodem1101")["state"]
        self.assertEqual(opened, [])
        self.assertFalse(capability.session.running)
        self.assertTrue(state["lines"][-1]["text"].startswith("Not started: nozzle 8 is not on the printer"))

    def test_replugged_printer_on_the_same_path_is_refused(self):
        opened = []
        _, _, capability, window = self.load(port_factory=lambda: opened.append(1))
        window.ask(cmd="hello")
        list_ports = sys.modules["serial.tools.list_ports"]
        list_ports.ports = [types.SimpleNamespace(device="/dev/cu.usbmodem1101", vid=0x2C99, description="n/a",
                                                  product="Original Prusa CORE One INDX", serial_number="CZPX0002")]
        state = window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101",
                           serial="CZPX0001")["state"]
        self.assertEqual(opened, [])
        self.assertFalse(capability.session.running)
        self.assertIn("is not the one the checklist was confirmed for", state["lines"][-1]["text"])

    def test_port_without_a_serial_number_is_not_run(self):
        opened = []
        _, _, capability, window = self.load(port_factory=lambda: opened.append(1))
        sys.modules["serial.tools.list_ports"].ports = [
            types.SimpleNamespace(device="/dev/ttyACM0", vid=0x1A86, description="USB Serial", product=None,
                                  serial_number=None)]
        state = window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/ttyACM0", serial="")["state"]
        self.assertEqual(opened, [])
        self.assertIn("does not report a USB serial number", state["lines"][-1]["text"])

    def test_declined_port_permission_is_reported(self):
        def denied():
            try:
                raise PermissionError("audit permission required")
            except PermissionError:
                raise OSError("could not open port /dev/cu.usbmodem1101: audit permission required")

        module, orca, capability, window = self.load(port_factory=denied)
        state = window.ask(cmd="run", nozzle=1, flush=290, pull=80, port="/dev/cu.usbmodem1101",
                           serial="CZPX0001")["state"]
        self.assertIn("permission prompt for /dev/cu.usbmodem1101 was declined", state["lines"][-1]["text"])
        self.assertFalse(state["restore"]["fil_changed"])


if __name__ == "__main__":
    unittest.main()
