# INDX Cold Pull

A single static page that generates a guided cold-pull procedure for the
**Prusa CORE One INDX**, to clear a partially clogged nozzle.

**→ [Open the tool](https://hyiger.github.io/indx-cold-pull/)**

No install. Pick a nozzle, download the `.gcode`, run it from a USB drive like
any other print. On Chrome or Edge it can also drive the printer directly over
USB and show live progress.

## Why this exists

Prusa's built-in cold-pull wizard (`M1702`) is **not enabled for INDX** —
`HAS_COLDPULL` covers MK3.5, MK4, XL, COREONE and COREONEL only, so on INDX the
handler compiles to an empty stub and the menu entry does not exist. This fills
that gap without requiring a custom firmware build.

## Before you run it

These are **menu-only settings on the printer**. Nothing here can read or change
them for you, and each one fails *silently* rather than loudly. The buttons stay
disabled until you confirm each item, because a paragraph of warnings gets
skimmed and the consequences here are not recoverable by reading further:

| Do this | Why |
|---|---|
| **Filament Sensor → OFF** | Otherwise the printer grabs and autoloads the filament while you are inserting it by hand. |
| **Auto Retract → OFF** | Otherwise the printer can treat the filament as retracted and discard the extrusion and pull moves — the procedure appears to run while nothing happens. |
| **Remove the PTFE tube from this tool** | The pulled plug travels up and out of the top port. With the tube fitted there is nowhere for it to go. |
| **Light-coloured PLA, and stay at the printer** | PLA shows the debris clearly. Do **not** load it beforehand — a prompt on the printer's screen says when to insert it. The procedure waits for knob presses at six points, and the heaters switch off after ~30 minutes unattended. |

Serial mode additionally needs **Settings → Hardware → Experimental Settings →
"Serial Printing Screen" → OFF** (then reboot). See [the firmware bug](#firmware-bug)
below for why.

## The procedure

Pick tool → hot flush → pack while cooling → deep cool → motorized pull →
restore → warm dock. Prompts appear on the **printer's** screen and wait
for a knob press, so your hands are at the machine where the work happens.

The six knob-press points, in order: confirm the setup, **insert the PLA**
(this is when the filament goes in, not before), **start the purge** — from
here, watch the nozzle tip for melted PLA flowing out — confirm what came out,
start the pull, and remove the pulled strand. Everything between the prompts,
including the tool pick and the final warm dock, is automatic. The nozzle is
rewarmed before docking so residue releases instead of dragging cold strings.
The closing brush wipe over the wastebin is end-of-print machinery, so **only
the G-code-file route gets it**; a serial run warms the nozzle and docks
without a wipe.

Success looks like **three thin strands with visible dark debris**. Repeat until
the tip comes out clean, typically one to three cycles.

**Then put the settings back.** Filament Sensor on, Auto Retract on, PTFE tube
refitted. The page shows this reminder once you download a file or finish a run,
because the settings you turned off stay off, and a printer left that way will
not detect a runout and will not retract at the end of a print, which invites the
next clog.

If nothing extrudes during the purge, the blockage is *above* the melt zone and
a cold pull cannot reach it. Stop there.

## Safety

The sequence sends toolchanger picks, 290 °C targets and INDX-specific motor
currents. On an MK4, XL or MINI those are wrong and potentially damaging, so:

- The generated file opens with `M862.3 P"COREONEINDX"`, which makes the
  printer's own print preview refuse it on a mismatched model.
- Serial mode queries `M115` and refuses to send anything unless the printer
  reports an INDX.

Temperatures are INDX-frame values. Its sensor sits 17 mm above the tip and only
15 % of the gradient is compensated, so the displayed value reads higher than the
true melt zone — **do not substitute numbers from MK4 or XL cold-pull guides.**

<a name="firmware-bug"></a>
## Firmware bug worked around

Buddy treats any `G` command (or `M73`/`M74`/`M109`/`M190`) arriving over serial
as the start of a print, but the inactivity timestamp is only refreshed once the
state machine reaches `Printing` — which cannot happen until the arming command
finishes. Arming with a long-blocking `M109` therefore trips a 5-second timeout
the instant it completes, running the **end-of-print teardown** (nozzle wipe,
tool dock, steppers off) mid-procedure. The next extrusion command then hits
`bsod("E move without tool")`.

This crashed a printer during development. Serial mode works around it by arming
with an instant `G4 P1` and re-asserting the tool before each extrusion block.
Turning off *Serial Printing Screen* removes the hazard at its source.

Reported upstream: [prusa3d/Prusa-Firmware-Buddy#5399](https://github.com/prusa3d/Prusa-Firmware-Buddy/issues/5399).

**The G-code download is unaffected** — a print job is driven by the media queue
and the normal print state machine, so this timeout never applies to it.

## Related

- [PrusaSlicer Filament Edition](https://github.com/hyiger/PrusaSlicer) — the same
  procedure as a built-in **Maintenance → Cold Pull (INDX)** menu item
- [Full guide](https://github.com/hyiger/PrusaSlicer/blob/master/doc/Cold_Pull_Guide.md) —
  prompt-by-prompt walkthrough, how to read the extracted tip, troubleshooting

## Keeping the two in sync

`generateGcode()` in `index.html` is a port of `generate_cold_pull_gcode()` in the
PrusaSlicer fork (`src/slic3r/Utils/MaintenanceSerial.cpp`) and is normally kept
**byte-identical** to it. If you change the procedure, change it in both places
and re-check:

```bash
diff <(node -e 'eval(require("fs").readFileSync("index.html","utf8").match(/<script>([\s\S]*?)<\/script>/)[1].match(/function generateGcode[\s\S]*?\n}/)[0]); process.stdout.write(generateGcode(0,290,80))') reference.gcode
```

This check currently fails against a fork-generated `reference.gcode`: the
issue #3 changes are ahead of the fork until they are ported back — see
[Status](#status).

## Status

Verified against Prusa-Firmware-Buddy `6.6.3+15625` (`ff6658da4`).

The serial path of this procedure has been run successfully on a CORE One INDX
via the PrusaSlicer implementation. This page's own serial mode is a faithful
port but has not itself been run end-to-end on hardware — treat a first run as a
test.

The changes from
[issue #3](https://github.com/hyiger/indx-cold-pull/issues/3) — the pre-purge
briefing prompt, the two-stage warm-up to the pull temperature, the warm wipe,
and the automatic dock at the end of a serial run — are newer than that
hardware run and have not yet been tested on a printer, nor ported back to the
PrusaSlicer fork, so the two implementations are temporarily out of sync.

**Use at your own risk.** This drives a hot nozzle and a high-current motor.

## Licence

AGPL-3.0-or-later, matching the PrusaSlicer fork the procedure came from.
