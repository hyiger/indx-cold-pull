/* Preview only (build.py --preview): a stand-in for the plugin behind window.orca, so the page can
   be looked at in a browser. ?scenario=idle|running|done|notindx|noserial picks the starting state;
   Run plays a short scripted log. */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const scenario = params.get("scenario") || "idle";
  let handler = null;
  let epoch = 0;
  let log = [];
  let running = false;
  let runSettings = null;
  let restore = null;
  let timer = null;

  const context = scenario === "notindx"
    ? { printer_name: "Prusa CORE One HF 0.4 nozzle", is_indx: false, tool_count: 8 }
    : { printer_name: "Prusa CORE One INDX 8T HF 0.4 nozzle", is_indx: true, tool_count: 8 };
  const serial = scenario === "noserial"
    ? { available: false, reason: "pyserial could not be loaded (No module named 'serial')" }
    : { available: true, reason: "" };
  const ports = serial.available ? [
    { device: "/dev/cu.usbmodem1101", label: "/dev/cu.usbmodem1101 (Original Prusa COREONEINDX)", prusa: true },
    { device: "/dev/cu.Bluetooth-Incoming-Port", label: "/dev/cu.Bluetooth-Incoming-Port", prusa: false },
  ] : [];
  const destinations = [
    { id: "/Volumes/PRUSA", label: "PRUSA", kind: "drive" },
    { id: "~/Downloads", label: "Downloads folder", kind: "folder" },
  ];
  const script = [
    ["Identifying printer…", "stage"], ["Confirmed COREONEINDX.", "good"],
    ["Reading the tool's filament type…", "stage"], ["Nozzle 3 is set to PETG; it will be put back after the pull.", ""],
    ["Confirm the setup prompt on the printer", "stage"], ["Starting session", "stage"],
    ["Picking nozzle from its dock", "stage"], ["Marking the tool FLEX to suppress auto retract", "stage"],
    ["Allowing cold extrusion", "stage"], ["Switching to relative extrusion", "stage"],
    ["Disabling stuck-filament detection", "stage"], ["Insert filament at the printer, then press the knob", "stage"],
  ];

  if (scenario === "running") {
    running = true;
    runSettings = { tool: 2, nozzle: 3, flush: 290, pull: 80 };
    log = script.map(([text, cls]) => ({ text, cls }));
  } else if (scenario === "done") {
    log = script.map(([text, cls]) => ({ text, cls }));
    log.push({ text: "Cold pull complete. Inspect the tip: three thin strands with debris is a good pull.", cls: "good" });
    restore = { after_run: true, fil_name: "PETG", fil_changed: true, tool: 2, auto_restored: true };
  }

  function snapshot(e, c) {
    const full = e !== epoch || c > log.length;
    const start = full ? 0 : c;
    return { epoch, full, start, lines: log.slice(start), count: log.length, running,
             run_settings: runSettings, restore, outcome: null };
  }
  function reply(message) { setTimeout(() => handler && handler(message), 15); }
  function state(m) { reply({ type: "state", state: snapshot(m.epoch, m.count) }); }

  function play(s) {
    epoch += 1; log = []; running = true; runSettings = s; restore = null;
    let i = 0;
    timer = setInterval(() => {
      if (i < script.length) { const [text, cls] = script[i++]; log.push({ text, cls }); return; }
      clearInterval(timer);
      log.push({ text: "Cold pull complete. Inspect the tip: three thin strands with debris is a good pull.", cls: "good" });
      running = false;
      restore = { after_run: true, fil_name: "PETG", fil_changed: true, tool: s.tool, auto_restored: true };
    }, 350);
  }

  window.orca = {
    onMessage(cb) { handler = cb; },
    postMessage(m) {
      switch (m.cmd) {
        case "hello":
          reply({ type: "init", context, serial, ports, destinations, state: snapshot(-1, 0) });
          break;
        case "poll": state(m); break;
        case "ports": reply({ type: "ports", ports }); break;
        case "destinations": reply({ type: "destinations", destinations }); break;
        case "say": log.push({ text: m.text, cls: m.cls }); state(m); break;
        case "save": {
          const dest = destinations.find((d) => d.id === m.dest);
          if (m.all) {
            log.push({ text: `Saved cold_pull_all_nozzles.zip to ${dest.label} (drive) - one file per nozzle, flush ${m.flush}C, pull ${m.pull}C. The printer cannot open a .zip: unzip it into a folder on the USB drive.`, cls: "good" });
            restore = { after_run: false, fil_name: null, fil_changed: true, tool: null, auto_restored: true };
          } else {
            log.push({ text: `Saved cold_pull_nozzle${m.nozzle}.gcode to ${dest.kind === "drive" ? dest.label + " (drive)" : dest.label}. Eject the drive and start the file on the printer.`, cls: "good" });
            restore = { after_run: false, fil_name: null, fil_changed: true, tool: m.tool, auto_restored: true };
          }
          state(m);
          break;
        }
        case "run": play({ tool: m.tool, nozzle: m.nozzle, flush: m.flush, pull: m.pull }); state(m); break;
        case "cancel": log.push({ text: "Cancel requested: stopping at the next step and restoring state.", cls: "err" }); state(m); break;
        case "force": log.push({ text: "FORCE STOP requested: sending M112.", cls: "err" }); state(m); break;
      }
    },
  };
})();
