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

Most of these are **menu-only settings on the printer** that nothing here can read
or change for you, and each one fails *silently* rather than loudly. The last row
is the exception — it is a change the procedure makes on your behalf, and you need
to know about it. The buttons stay disabled until you confirm every item, because a
paragraph of warnings gets skimmed and the consequences here are not recoverable by
reading further:

| Item | Why |
|---|---|
| **Filament Sensor → OFF** | Otherwise the printer grabs and autoloads the filament while you are inserting it by hand. |
| **Remove the PTFE tube from this tool** | The pulled plug travels 80 mm up and out of the top port. With the tube fitted there is nowhere for it to go. |
| **Light-coloured PLA, and stay at the printer** | PLA shows the debris clearly. Do **not** load it beforehand — a prompt on the printer's screen says when to insert it. The procedure waits for knob presses at six points, and the heaters switch off after ~30 minutes unattended. |
| **This nozzle's filament type gets set to FLEX — note what it is now** | Firmware 6.9.0 removed the Auto Retract switch on INDX, and FLEX is the only remaining way to suppress it; see [Auto retract](#auto-retract) below. This is a *persistent* change to the printer's settings that you have to put back. USB Serial reads the current type and restores that exact value for you, except for names it cannot send back over G-code, where it writes nothing and tells you what to set by hand. The downloaded file cannot read anything, so **write down what Settings → Filament shows for this nozzle before you start.** |

Serial mode additionally needs **Settings → Hardware → Experimental Settings →
"Serial Printing Screen" → OFF** (then reboot). See [the firmware bug](#firmware-bug)
below for why.

## The procedure

Pick tool → mark FLEX → hot flush → pack while cooling → deep cool → 80 mm
motorized pull → restore → warm dock. Prompts appear on the **printer's** screen and wait
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

**Then put the settings back.** Filament Sensor on, PTFE tube refitted, and the
nozzle's filament type back off FLEX — to whatever it was before, not to PLA. The page shows this reminder once you
download a file or finish a run, because the settings you turned off stay off: a
printer left that way will not detect a runout, and a nozzle left marked FLEX
will not auto-retract at the end of a print, which invites the next clog.

If nothing extrudes during the purge, the blockage is *above* the melt zone and
a cold pull cannot reach it. Stop there.

<a name="auto-retract"></a>
## Auto retract

Firmware **6.9.0** removed the Auto Retract switch on INDX
([`0dfee5ef1`](https://github.com/prusa3d/Prusa-Firmware-Buddy/commit/0dfee5ef1),
*"INDX: Remove Auto Retract menu switch (it's not permitted to switch off)"*,
BFW-8589). The toggle moved behind a new `HAS_SWITCHABLE_AUTO_RETRACT` option,
which lists `COREONE COREONEL MK4 iX XL` — neither INDX variant. On INDX the menu
item, the global-disable check in `maybe_retract_from_nozzle()` and the
`auto_retract_enabled` config-store item are all compiled out, so there is no
menu, G-code or config route to it any more.

What survives is a **filament-type escape hatch**, and it is deliberate, not a
side effect. `src/feature/auto_retract/auto_retract.cpp` returns early, before any
retraction, for anything the firmware considers flexible:

```cpp
// Do not auto retract flexible filaments, they might get tangled in the extruder (BFW-6953)
if (filament_parameters.is_flexible) {
    return;
}
```

The same test guards `prepare_for_nozzle_cleaning()` in `probe.cpp`, and
`standard_ramming_sequence_indx.cpp` states it outright: *"auto_retract is never
called for flexible filaments (filtered in auto_retract.cpp)"*. `FLEX` is the only
preset with `is_flexible = true`, and the type is read per tool out of the config
store, so **marking the nozzle FLEX genuinely does disable auto retract.** The
early return is not behind any `HAS_*` guard and is present in 6.6.x as well as
6.9.0.

The procedure sets it over G-code rather than through the menu:

```gcode
M865 S"FLEX" L<n>     ; n = 0-based tool index
```

`M865` cannot rewrite a preset's parameters (`is_customizable()` is false for
presets), so this only changes which type the tool is recorded as holding. It also
works on 6.6.x, where the flexible check runs after the global one and wins either
way.

Side effects of the choice, all checked:

- FLEX's ⅙ feedrate factor applies to firmware-internal load/unload/purge helpers
  (`standard_feedrates::extruder`), **not** to the raw `G1 E… F…` moves this
  procedure uses. The pull runs at the speed the file asks for.
- Every temperature in the procedure is set explicitly, so FLEX's 240 °C nozzle and
  170 °C preheat never come into play.
- FLEX has `requires_filtration = true` and PLA does not, so the **chamber
  filtration fan will run** while the nozzle is hot. Harmless, but expected.
- The chamber target moves 20 °C → 25 °C, which only feeds ventilation state for
  extruders the G-code declares. This file declares none.

**Putting it back is on you.** The write lands in the printer's persistent settings
and a power cycle does not undo it.

- **USB Serial** reads the nozzle's real filament type first (`M865 I<n>`) and
  restores exactly that value — as the very last command of the run, after the warm
  dock, so nothing in between can auto-retract — and in its cleanup path. The one
  exception is a name it cannot quote back into `M865`, such as one containing a
  space or punctuation. The firmware will store one: an NFC abbreviation that fails
  its own name validation is kept anyway, with the dataset merely flagged unsafe.
  Rather than overwrite it with a value nobody chose, the run writes **nothing**
  back — the nozzle is left marked FLEX and the page hands you the exact name to
  restore by hand. Watch for that message; a nozzle left FLEX will not auto-retract
  at the end of your next print.
- **The downloaded file does not**, on purpose. The end-of-print sequence runs
  after the last line of the file; with the nozzle already back on PLA, that
  sequence would auto-retract — reheating to 215 °C, overriding the 170 °C the file
  sets for the warm wipe, and ramming the melt zone you just cleared. Once the
  print has **finished**, send `M865 S"<TYPE>" L<n>` with the type you noted before
  starting, or set it from the printer's Filament menu. **Do not assume it was
  PLA** — sending PLA to a nozzle configured as PETG or ASA silently overwrites
  that, which is worse than the state you were trying to restore.

One thing FLEX does *not* fix: a retraction already banked in persistent storage.
While the firmware believes a nozzle is retracted, `planner.cpp` silently swallows
negative-E printing moves, and `G0_G1.cpp` marks every `G1` as a printing move — so
a stale retracted state really would eat the pull. It is cleared by the first hot
positive-E move, which is what the purge block at the start of the procedure is.

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

The two are in sync again as of
[hyiger/PrusaSlicer#55](https://github.com/hyiger/PrusaSlicer/pull/55), which
ports the issue #3 changes and the 6.9.0 auto-retract work back to the fork;
byte-identity was verified across several `(tool, flush, pull)` combinations.
The check fails against a `reference.gcode` generated before that lands.

## Status

Verified against Prusa-Firmware-Buddy `6.6.3+15625` (`ff6658da4`) and `6.9.0`
(`00ae96876`).

The serial path of this procedure has been run successfully on a CORE One INDX
via the PrusaSlicer implementation. This page's own serial mode is a faithful
port but has not itself been run end-to-end on hardware — treat a first run as a
test.

The changes from
[issue #3](https://github.com/hyiger/indx-cold-pull/issues/3) — the pre-purge
briefing prompt, the two-stage warm-up to the pull temperature, the warm wipe,
and the automatic dock at the end of a serial run — plus the 6.9.0 auto-retract
workaround and the 80 mm pull are all newer than that hardware run and **have
not yet been tested on a printer**. Treat a first run as a test.

**Use at your own risk.** This drives a hot nozzle and a high-current motor.

## Licence

AGPL-3.0-or-later, matching the PrusaSlicer fork the procedure came from.
