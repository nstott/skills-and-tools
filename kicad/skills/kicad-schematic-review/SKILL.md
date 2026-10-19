---
name: kicad-schematic-review
description: Review a KiCad schematic for errors and missing design elements (ERC, power and decoupling, pin conflicts, unnamed/dangling nets, footprints and BOM fields) using kicad-cli and the kicad_sch netlist tools. Use when asked to review, sanity-check, or sign off a schematic before PCB work.
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

## 3. Report
Findings grouped as **must fix** (will not work / unsafe), **should fix** (works but fragile or confusing),
**notes** (decisions to confirm). State what was *not* verified (e.g. pad geometry, component values taken on trust).
