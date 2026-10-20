---
name: kicad-schematic-review
description: Review a KiCad schematic for errors, missing design elements and readability (ERC, power and decoupling, pin conflicts, unnamed nets, ratings, notes, footprints and BOM fields), and check every IC's circuit block against its datasheet's application circuit and recommended values (crystal, regulators, MCU core, flash, USB, ESD). Use when asked to review, sanity-check, or sign off a schematic before PCB work.
---

# KiCad schematic review

Work through these in order; report findings with ref/pin, don't just say "looks fine".

## 1. Mechanical checks (run them, show the numbers)
- `kcli sch erc --severity-all -o /tmp/erc.rpt FILE` (run from the project dir so project libraries resolve) and
  summarise by type: `grep -E '^\[' /tmp/erc.rpt | sort | uniq -c`. Also run MCP `run_erc`; they can disagree.
  Triage every violation: fix, or explain why it is acceptable. Never "exclude" to get to zero.
  `kicad-cli` is not on PATH: load `kcli` with `. ${CLAUDE_PLUGIN_ROOT}/tools/_env.sh` (it also drops the Fontconfig noise) or call
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` directly.
- Nets: `PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/tools python3 -m kicad_sch nets FILE`. Look for: single-pin nets,
  accidental merges (two names on one net), unnamed nets on important signals, pins on `GND` that should not be.
- Render and read it: `${CLAUDE_PLUGIN_ROOT}/tools/kicad-render.sh FILE outdir 6800`.
- BOM: `${CLAUDE_PLUGIN_ROOT}/tools/bom-check.sh FILE` - every assembled part has LCSC/MPN, footprints resolve.
- Quality signals: `${CLAUDE_PLUGIN_ROOT}/tools/sch-quality.py FILE` - ERC by type, title block, notes, unnamed nets on IC
  pins, parts drawn with no wires, 4-way junctions, decoupling coverage, cap voltage rating against the rail (it infers a
  filtered rail's voltage through its series R/L/F), missing fields, overlapping text, and the list of active parts to
  check against their datasheets. Confirm each candidate in the render before reporting it.
- Schematic vs PCB (when a `.kicad_pcb` exists): `${CLAUDE_PLUGIN_ROOT}/tools/sch-pcb-diff.py FILE.kicad_sch FILE.kicad_pcb` - parts,
  values, footprints and the net on every pad must agree; any difference means "Update PCB from Schematic" is pending.

## 2. Design review checklist
- **Power**: every supply pin on its rail; each rail has a source (power_out pin or PWR_FLAG); regulator input/output
  caps per datasheet; enable/PS pins defined at power-up (pull-ups/downs), not floating; reverse-polarity/back-power
  paths considered (VBUS to VSYS diode, USB hosts powering the board).
- **Decoupling**: 100 nF per supply pin, bulk caps near regulators, correct voltage/dielectric (C0G for crystal caps,
  DC-bias derating on X5R at the actual rail voltage). Read the voltage rating of every bulk cap from its LCSC/MPN, not
  just the value: a 6.3 V or 10 V X5R loses roughly 40-60 % of its capacitance at 3.3 V to 5 V, a 16 V to 25 V part
  about 20-35 %. Compare the *effective* total against the regulator datasheet minimum. Same footprint, higher voltage
  is usually a free fix (and often a Basic part).
- **Pin types/conflicts**: outputs driving outputs, inputs left floating, strap pins (BOOT, QSPI_SS) in the right state.
- **Interfaces**: USB series resistors and CC resistors (5.1 k each on CC1/CC2 for a device), debug header pinout,
  crystal load capacitance and drive resistor against the datasheet, I2C pull-ups, reset circuit.
  **USB-C device checklist**: 5.1 k on CC1 and CC2 to GND; D+/D- (A6/B6, A7/B7) joined; 27 ohm series resistors if the MCU
  needs them (RP2040/RP2350; not if on a Pico module); SBU unused or no-connected; shield and GND tied; VBUS fuse (PTC) and
  an ESD array on D+/D-/VBUS are optional but cheap; VBUS reverse/back-power diode toward the board supply.
- **Sense dividers**: for every divider into a GPIO/ADC (VBUS, VSYS, battery) compute the output at the *maximum* input
  (e.g. VBUS 5.25 V). It must stay under the pin's limit (IOVDD + 0.3 V on ADC-capable pins) and above its logic-high
  threshold at the minimum input. Check the ratio is the right way up (the large resistor to GND for a step-down).
- **Connectors**: pin 1 and numbering, mating/orientation, ground pins interleaved on fast signals.
- **Footprints**: assigned to every part; pad names match symbol pins; polarised parts marked; DNP parts flagged.
- **Reference design**: compare against the vendor reference (for RP2350 see the `rp2350-design` skill) and list any
  deliberate deviations.

## 3. Readability and information (general schematic quality)
Flag every place the sheet departs from these:
- **Flow**: inputs/connectors left, processing middle, outputs right; positive rails up, GND down.
- **Blocks**: one titled block per function (USB input, regulator, MCU core, flash, clock, debug, IO); hierarchical sheets
  once a sheet is crowded.
- **Wires for local, labels for long**: a part's own decoupling, pull-ups, crystal and feedback network are wired to it;
  labels only between blocks. A sheet that is mostly labels hides topology (`wiring`).
- **Power symbols for rails**, decoupling drawn beside the pins it serves, one cap per pin group so the count is visible.
- **No 4-way junctions**, dots only where wires join, no wire through a pin, everything on grid, no overlapping text:
  text must not touch other text, any symbol body or pin (its own body too, for passives) or a wire (`overlap`).
  **Re-run `sch-quality.py` after every edit that changes text** (values, new fields, moved or added parts, labels) and
  look at a render of the changed area; a longer value overlaps neighbours that were clear before. Put ratings in a
  visible `Voltage` field under the value, not in the value itself (`10uF` + `25V`), so rows of parts stay readable.
- **Names for what you would probe**: every clock, enable, sense, switch node and bus is named (`net-names`); no
  `Net-(U1-Pad58)` on anything you might scope.
- **Values carry what matters**: voltage on bulk caps, tolerance where it matters (dividers, feedback), power on current
  shunts, current on fuses and inductors (`ratings`).
- **Notes** for non-obvious choices: divider maths, strap states, DNP options, current budget, deviations from the
  reference design, links to the datasheet figure used (`notes`).
- **Title block** filled (`title-block`), references annotated and unique, fields complete (`fields`).

## 4. Datasheet conformance (go block by block)
For every IC and the circuit around it, compare the schematic with the manufacturer's application circuit and
recommended values, and say for each block whether it follows the datasheet, deviates deliberately (with a note), or
deviates silently (a finding).
- **Get the documents**: `${CLAUDE_PLUGIN_ROOT}/tools/datasheet-get.py FILE [REF ...] [--render]` downloads each part's
  datasheet into `~/.cache/nstott-tools/datasheets/<MPN>/`, extracts the text and lists the pages with the application
  circuit, component selection, recommended values and layout guidance; `--render` writes those pages as PNG so the
  figures can be read. For MCUs, also use the vendor *hardware design guide* (RP2350: the `rp2350-design` skill, "Hardware
  design with RP2350"), which carries the reference schematic that the main datasheet does not. When a link is missing or
  not a PDF, find the datasheet by hand; don't skip the block.
- **Build a table** per block: datasheet reference (document, page, figure), what it specifies, what the schematic has,
  verdict. Quote numbers, not impressions.
- **What to compare, by block type**:
  - *Crystal*: load capacitance CL from the crystal datasheet; each load cap C = 2 x (CL - Cstray), Cstray about 2-5 pF; C0G
    dielectric; the MCU's required drive/series resistor (RP2350: 1 k on XOUT); ESR and drive level within the MCU's
    oscillator limits; frequency the MCU expects.
  - *Switching regulator (buck, boost, buck-boost)*: inductor value in the recommended range and its saturation current
    above peak switch current; input and output capacitance *effective after DC-bias derating* against the minimums;
    feedback divider or fixed-output variant and the resulting voltage; EN, mode/PS, soft-start and power-good pins
    defined; bootstrap cap if any; output voltage range vs input range (buck-boost needed?).
  - *LDO*: input/output cap values and ESR window for stability, dropout at minimum input, power dissipation.
  - *MCU core*: every supply pin and its decoupling against the hardware guide, internal regulator components (inductor,
    caps, filter R), reset/RUN, boot straps, clock, debug pins, USB pins (series R, pull-ups).
  - *Flash/EEPROM*: CS pull-up, WP/HOLD handling in quad mode, decoupling, voltage range.
  - *USB/ESD/TVS*: standoff voltage above the rail, capacitance low enough for the data rate, VBUS pin connected to the
    right side, CC resistors.
  - *Passives with datasheets* (LEDs, fuses, diodes): LED current from the series resistor vs the rated and visible
    current; fuse hold/trip vs load and rail voltage; diode forward drop and current vs the load.
- **Layout guidance**: note the datasheet's layout rules (loop placement, cap order, keep-outs) for the PCB review
  (`kicad-pcb-review`); a schematic review records them, the PCB review checks them.

## 5. Report
Findings grouped as **must fix** (will not work / unsafe), **should fix** (works but fragile or confusing),
**notes** (decisions to confirm). State what was *not* verified (e.g. pad geometry, component values taken on trust).
