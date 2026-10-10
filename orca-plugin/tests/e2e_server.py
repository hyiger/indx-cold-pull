"""End-to-end harness: the real page and the real built plugin, against a simulated INDX.

    python3 tests/e2e_server.py [--port 8765]

Serves the page at http://localhost:<port>/ with window.orca bridged over HTTP to the plugin's
on_message handler, running against stand-ins for OrcaSlicer (fake `orca`) and pyserial (a printer
that acts like Buddy firmware: M0 waits for a knob press and sends busy keepalives, M112 halts it).

  POST /msg     a page message; returns the replies the plugin posted
  POST /knob    press the knob on the simulated printer
  POST /reset   reset the simulated printer after an M112
  POST /printer?model=4t|8t|8tcopy|mk4   change the printer selected in the fake OrcaSlicer
  POST /ports?set=one|two|second|replaced|noserial   which USB serial ports exist
  GET  /sent    every command the printer has received
"""
import argparse
import http.server
import json
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import build  # noqa: E402
import test_plugin_glue as glue  # noqa: E402


class SimulatedIndx:
    """Buddy-like enough for the plugin: acks commands, holds M0 until the knob, halts on M112."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sent = []
        self.rx = bytearray()
        self.waiting_m0 = False
        self.halted = False
        self.closed = True
        self.timeout = 0.25
        threading.Thread(target=self._keepalive, daemon=True).start()

    # pyserial surface
    def open(self):
        with self.lock:
            self.closed = False
            self.rx.clear()
        return self

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.rx)

    def write(self, data):
        if self.closed:
            raise OSError("port not open")
        for line in data.decode().splitlines():
            with self.lock:
                self.sent.append(line)
                if self.halted:
                    continue
                if line == "M112":
                    self.halted = True
                    self.waiting_m0 = False
                elif line == "M115":
                    self.rx += b"FIRMWARE_NAME:Prusa-Firmware-Buddy 6.9.1+16319 MACHINE_TYPE:Prusa-COREONEINDX\nok\n"
                elif line.startswith("M865 I"):
                    self.rx += b"name:PCTG\nok\n"
                elif line.startswith("M0 "):
                    self.waiting_m0 = True
                else:
                    self.rx += b"ok\n"
        return len(data)

    def read(self, size):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.rx:
                    out = bytes(self.rx[:size])
                    del self.rx[:size]
                    return out
            time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True

    # test controls
    def knob(self):
        with self.lock:
            if self.waiting_m0:
                self.waiting_m0 = False
                self.rx += b"ok\n"

    def reset(self):
        with self.lock:
            self.halted = False
            self.waiting_m0 = False
            self.rx.clear()

    def _keepalive(self):
        while True:
            time.sleep(2)
            with self.lock:
                if self.waiting_m0 and not self.closed:
                    self.rx += b"echo:busy: paused for user\n"


def page_html():
    page = build.read(build.SRC, "page.html")
    head = build.read(HERE, "preview_head.html")
    bridge = """<script>
(function () {
  let handler = null;
  window.orca = {
    onMessage(cb) { handler = cb; },
    postMessage(m) {
      fetch("/msg", { method: "POST", body: JSON.stringify(m) }).then((r) => r.json())
        .then((replies) => replies.forEach((reply) => handler && handler(reply)));
    },
  };
})();
</script>"""
    return page.replace("<head>\n", "<head>\n" + head + "\n" + bridge + "\n", 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    printer = SimulatedIndx()
    harness = glue.PluginGlue("test_window_init_reflects_the_orca_printer")
    harness.addCleanup = lambda fn: None
    module, orca, capability, window = harness.load(preset=glue.INDX_8T, port_factory=printer.open)
    module.list_destinations = lambda: [{"id": "/tmp", "label": "PRO-G40", "kind": "drive"}]
    def indx(device, serial):
        return types.SimpleNamespace(device=device, vid=0x2C99, product="Original Prusa COREONEINDX",
                                     description="n/a", serial_number=serial)
    first, second = indx("/dev/cu.usbmodem1101", "CZPX0001"), indx("/dev/cu.usbmodem2201", "CZPX0002")
    port_sets = {"one": [first], "two": [first, second], "second": [second],
                 "replaced": [indx("/dev/cu.usbmodem1101", "CZPX0003")],
                 "noserial": [indx("/dev/cu.usbmodem1101", None)]}
    current_ports = {"list": port_sets["one"]}
    sys.modules["serial.tools.list_ports"].comports = lambda: current_ports["list"]
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/sent"):
                return self._send(200, json.dumps(printer.sent))
            return self._send(200, page_html(), "text/html; charset=utf-8")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            if self.path == "/msg":
                with lock:  # on_message runs on one (UI) thread in OrcaSlicer
                    start = len(window.posted)
                    window.on_message(json.loads(body))
                    replies = window.posted[start:]
                return self._send(200, json.dumps(replies))
            if self.path == "/knob":
                printer.knob()
                return self._send(200, "{}")
            if self.path == "/reset":
                printer.reset()
                return self._send(200, "{}")
            if self.path.startswith("/ports?set="):
                current_ports["list"] = port_sets[self.path.split("=", 1)[1]]
                return self._send(200, "{}")
            if self.path.startswith("/printer?model="):
                copy = glue.FakePreset("Prusa CORE One INDX 8T HF 0.4 nozzle - Copy", glue.INDX_8T._config)
                presets = {"4t": glue.INDX_4T, "8t": glue.INDX_8T, "8tcopy": copy, "mk4": glue.MK4}
                orca.preset = presets[self.path.split("=", 1)[1]]
                return self._send(200, "{}")
            return self._send(404, "{}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"e2e harness on http://localhost:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
