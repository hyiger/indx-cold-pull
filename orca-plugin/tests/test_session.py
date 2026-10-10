"""The guarded serial session against a scripted fake printer: every exit path the page handles."""
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import cold_pull_core as core  # noqa: E402

M115_INDX = "FIRMWARE_NAME:Prusa-Firmware-Buddy 6.9.1+16319 MACHINE_TYPE:COREONEINDX EXTRUDER_COUNT:8\nok\n"


class FakePrinter:
    """A pyserial stand-in. `responder(line, port)` returns the reply text for a command, or None to
    never acknowledge it."""

    def __init__(self, responder, read_timeout=0.002):
        self.responder = responder
        self.timeout = read_timeout
        self.sent = []
        self.closed = False
        self.fail_reads = False
        self.fail_writes = False
        self._rx = bytearray()
        self._lock = threading.Lock()

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._rx)

    def write(self, data):
        if self.fail_writes or self.closed:
            raise OSError("write failed")
        for line in data.decode("utf-8").splitlines():
            self.sent.append(line)
            reply = self.responder(line, self)
            if reply is not None:
                with self._lock:
                    self._rx += reply.encode("utf-8")

    def read(self, size):
        if self.fail_reads:
            raise OSError("device reports readiness to read but returned no data")
        with self._lock:
            if self._rx:
                out = bytes(self._rx[:size])
                del self._rx[:size]
                return out
        time.sleep(self.timeout)
        return b""

    def close(self):
        self.closed = True


def printer(filament="name:PETG\n", model=M115_INDX, hang_on=None, hook=None):
    def responder(line, port):
        if hook is not None:
            hook(line, port)
        if hang_on is not None and line == hang_on:
            return None
        if line == "M115":
            return model
        if line.startswith("M865 I"):
            return f"{filament}ok\n"
        return "ok\n"
    return FakePrinter(responder)


def run(port, nozzle=3, timeout_scale=1.0, during=None):
    session = core.ColdPullSession(timeout_scale=timeout_scale)
    settings = core.clamp_settings(nozzle, 290, 80)
    session.begin_attempt(settings)
    session.start_run(port, settings)
    if during is not None:
        during(session)
    session.join(10)
    assert not session.running, "run did not finish"
    return session, settings


def texts(session):
    return [line["text"] for line in session.snapshot()["lines"]]


class SessionTests(unittest.TestCase):
    def test_completed_run_restores_the_original_type_last(self):
        port = printer()
        session, s = run(port)
        steps = [cmd for _, cmd, _ in core.build_steps(2, 290, 80, "PETG")]
        self.assertEqual(port.sent, ["M115", "M865 I2", *steps])
        self.assertEqual(port.sent[-1], 'M865 S"PETG" L2')
        self.assertEqual(session.last_outcome, "completed")
        self.assertTrue(texts(session)[-1].startswith("Cold pull complete."))
        self.assertEqual(session.snapshot()["restore"], {"after_run": True, "fil_name": "PETG", "fil_changed": True,
                                                         "tool": 2, "auto_restored": True})
        self.assertTrue(port.closed)

    def test_non_indx_is_refused_before_anything_is_sent(self):
        port = printer(model="FIRMWARE_NAME:Prusa-Firmware-Buddy MACHINE_TYPE:MK4S\nok\n")
        session, _ = run(port)
        self.assertEqual(port.sent, ["M115"])
        self.assertEqual(session.last_outcome, "rejected")
        self.assertIn('Refusing to continue: this port reports "MK4S", not an INDX.', texts(session))
        self.assertFalse(session.snapshot()["restore"]["fil_changed"])

    def test_unsendable_name_is_never_overwritten(self):
        port = printer(filament="name:PLA Silk\n")
        session, _ = run(port)
        self.assertEqual(session.last_outcome, "completed")
        self.assertEqual([c for c in port.sent if c.startswith("M865 S")], ['M865 S"FLEX" L2'])
        restore = session.snapshot()["restore"]
        self.assertEqual((restore["fil_name"], restore["auto_restored"]), ("PLA Silk", False))

    def test_missing_name_ends_as_pla(self):
        port = printer(filament="")
        session, _ = run(port, nozzle=1)
        self.assertEqual(port.sent[-1], 'M865 S"PLA" L0')
        self.assertTrue(any("has no filament type set" in t for t in texts(session)))

    def test_timeout_runs_the_cleanup_and_reports_the_picked_tool(self):
        port = printer(hang_on="M109 S290")
        session, _ = run(port, timeout_scale=0.0005)
        self.assertEqual(session.last_outcome, "failed")
        hung = port.sent.index("M109 S290")
        self.assertEqual(port.sent[hung + 1:], core.cleanup_commands(2, "PETG", True))
        self.assertIn("Failed: timed out waiting for the printer", texts(session))
        self.assertEqual(texts(session)[-1], "Printer state restored." + core.PARK_NOTE)

    def test_cancel_takes_effect_at_the_next_step(self):
        holder = {}

        def hook(line, _port):
            if line == "M83":
                holder["session"].request_cancel()

        port = printer(hook=hook)
        session = core.ColdPullSession()
        holder["session"] = session
        settings = core.clamp_settings(3, 290, 80)
        session.begin_attempt(settings)
        session.start_run(port, settings)
        session.join(10)
        self.assertEqual(session.last_outcome, "canceled")
        self.assertEqual(port.sent[port.sent.index("M83") + 1:], core.cleanup_commands(2, "PETG", True))
        self.assertEqual(texts(session)[-1], "Canceled. Printer state restored." + core.PARK_NOTE)

    def test_force_stop_interrupts_a_silent_wait(self):
        port = printer(hang_on="M109 S290")

        def force_soon(session):
            time.sleep(0.2)
            session.request_force_stop()

        session, _ = run(port, during=force_soon)
        self.assertEqual(session.last_outcome, "force-stopped")
        self.assertEqual(port.sent[-1], "M112")
        self.assertEqual(port.sent[port.sent.index("M109 S290") + 1:], ["M112"])

    def test_shutdown_queues_the_cleanup_without_waiting(self):
        port = printer(hang_on="M0 Insert light PLA into the top port until it stops at the drive gears. Do not force it")

        def unload_soon(session):
            time.sleep(0.2)
            session.shutdown(timeout=2)

        session, _ = run(port, during=unload_soon)
        self.assertEqual(session.last_outcome, "interrupted")
        self.assertEqual(port.sent[-len(core.cleanup_commands(2, "PETG", True)):],
                         core.cleanup_commands(2, "PETG", True))

    def test_disconnect_reports_unconfirmed_cleanup(self):
        def hook(line, port):
            if line == "G1 E20 F120":
                port.fail_reads = True
                port.fail_writes = True

        session, _ = run(printer(hook=hook))
        self.assertEqual(session.last_outcome, "failed")
        self.assertIn("Failed: serial port closed", texts(session))
        self.assertTrue(texts(session)[-1].startswith("The printer did not confirm cleanup"))

    def test_port_that_never_opened_owes_only_the_printer_settings(self):
        session = core.ColdPullSession()
        settings = core.clamp_settings(5, 290, 80)
        session.begin_attempt(settings)
        session.fail_attempt(settings, "could not open port")
        self.assertEqual(texts(session), ["Error: could not open port"])
        self.assertEqual(session.snapshot()["restore"]["fil_changed"], False)

    def test_second_run_is_refused_while_one_is_live(self):
        port = printer(hang_on="M109 S290")
        session = core.ColdPullSession()
        settings = core.clamp_settings(1, 290, 80)
        session.begin_attempt(settings)
        session.start_run(port, settings)
        with self.assertRaises(RuntimeError):
            session.begin_attempt(settings)
        session.request_force_stop()
        session.join(5)


class SnapshotTests(unittest.TestCase):
    def test_incremental_then_full_after_clear(self):
        session = core.ColdPullSession()
        for n in range(3):
            session.say(f"line {n}")
        first = session.snapshot()
        self.assertTrue(first["full"])
        session.say("line 3")
        second = session.snapshot(first["epoch"], first["count"])
        self.assertFalse(second["full"])
        self.assertEqual([l["text"] for l in second["lines"]], ["line 3"])
        session.clear_log()
        third = session.snapshot(second["epoch"], second["count"])
        self.assertTrue(third["full"])
        self.assertEqual(third["lines"], [])


class SettingsAndFiles(unittest.TestCase):
    def test_clamps_like_the_page(self):
        self.assertEqual(core.clamp_settings("9", "0", "85.5", tool_count=8),
                         {"tool": 7, "nozzle": 8, "flush": 290, "pull": 85})
        self.assertEqual(core.clamp_settings(2, "150", "", tool_count=4),
                         {"tool": 1, "nozzle": 2, "flush": 200, "pull": 80})
        self.assertEqual(core.clamp_settings(6, 290, 80, tool_count=4)["nozzle"], 4)

    def test_files(self):
        with tempfile.TemporaryDirectory() as folder:
            s = core.clamp_settings(2, 285, 75)
            path = core.write_gcode(folder, s)
            self.assertEqual(os.path.basename(path), "cold_pull_nozzle2.gcode")
            with open(path, encoding="utf-8", newline="") as handle:
                self.assertEqual(handle.read(), core.generate_gcode(1, 285, 75))
            zpath = core.write_all_zip(folder, s, tool_count=4)
            with zipfile.ZipFile(zpath) as archive:
                self.assertEqual(archive.namelist(), [f"cold_pull_nozzle{n}.gcode" for n in range(1, 5)])
                self.assertEqual(archive.read("cold_pull_nozzle4.gcode").decode(), core.generate_gcode(3, 285, 75))


if __name__ == "__main__":
    unittest.main()
