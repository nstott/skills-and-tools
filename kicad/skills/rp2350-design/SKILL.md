---
name: rp2350-design
description: Reference for designing boards around the Raspberry Pi RP2350 (RP2350A QFN-60 / RP2350B) following the official hardware design guide and Pico 2 - supply network, crystal, flash, USB, debug, Pico 2 pinout. Use when drawing or reviewing an RP2350 schematic or layout.
---

# RP2350 design notes

Sources: "Hardware design with RP2350" (Minimal board, release 3) and the Pico 2 datasheet, both from
datasheets.raspberrypi.com. The guide's Minimal board is 2-layer with an NCP1117 LDO; Pico 2 uses an RT6150B
buck-boost. Departing from the guide's regulator/crystal circuits is "at your own risk" per Raspberry Pi.

## Supply network (RP2350A)
- IOVDD: six pins (1, 11, 20, 30, 38, 45) at 3.3 V, 100 nF each. USB_OTP_VDD (53), QSPI_IOVDD (54): 3.3 V, 100 nF.
  The KiCad symbol stacks all IOVDD pins (and DVDD pins 6, 23, 39) at **one point**: wire/label that point once.
- Core regulator is an on-chip switcher: VREG_VIN (49) 3.3 V with 4.7 uF; VREG_LX (48) -> 3.3 uH inductor ->
  DVDD net (1.1 V) with 4.7 uF; VREG_FB (50) to the 1.1 V node; VREG_PGND (47) to GND with its own return.
  DVDD pins 100 nF each (3). VREG_AVDD (46): 33 ohm from 3.3 V + 4.7 uF (RC filter).
- Inductor: Abracon AOTA-B201610S3R3-101-T (0806, 3.3 uH, polarity dot). Orientation matters: the dot is the
  current-in terminal = LX side. Layout of regulator, inductor and caps should mirror Pico 2 / the Minimal board.
- ADC_AVDD (44): Pico 2 uses 200 ohm + 2.2 uF from 3.3 V (ADC_VREF header pin). AGND joins GND (0 ohm link is fine).
- Guide shares one 100 nF between pins 53/54 only for lack of space; with 4 layers give each its own.

## Clock, flash, USB, debug
- 12 MHz ABM8-272-T3 crystal, 2x 15 pF C0G load caps, **1 k series resistor on XOUT**. Any deviation needs testing.
- QSPI flash directly on the six QSPI pins, short traces. QSPI_SS: optional 10 k pull-up (DNP on the reference), and
  **1 k to a BOOTSEL switch to GND**. W25Q128 on the guide; 16 MB is the maximum RP2350 supports.
- USB_DP/DM: **27 ohm series resistors** close to the chip, 90 ohm differential pair over solid ground; no external
  pull-ups. RUN pin: reset button to GND. SWCLK/SWDIO to a 3-pin debug connector (Pico: JST SH, SWCLK-GND-SWDIO).
- Pico 2 board functions on GPIO: GPIO23 = RT6150 PS pin (add a pull-down so PSM is defined before boot),
  GPIO24 = VBUS sense, GPIO25 = LED, GPIO29 = VSYS/3 (ADC3).

## Pico 2 header pinout (40 pins, 2.54 mm, two 1x20 columns 17.78 mm apart)
Left 1-20: GP0 GP1 GND GP2 GP3 GP4 GP5 GND GP6 GP7 GP8 GP9 GND GP10 GP11 GP12 GP13 GND GP14 GP15.
Right 21-40: GP16 GP17 GND GP18 GP19 GP20 GP21 GND GP22 RUN GP26 GP27 AGND GP28 ADC_VREF 3V3(OUT) 3V3_EN GND VSYS VBUS.
(Right column is numbered bottom-to-top on the board: rotate that header 180 degrees.)

## RT6150B-33 buck-boost (Pico 2 powerchain)
VIN/VINA = VSYS (1.8-5.5 V), VOUT = 3.3 V and FB tied to VOUT, 2.2 uH between LX1 and LX2, CIN >= 10 uF, COUT about
20 uF, EN pulled to VSYS by 100 k (exposed as 3V3_EN), PS low = power-save, high = PWM. VBUS -> Schottky -> VSYS
(Pico 2 adds the diode so other sources can be OR-ed in). Keep CIN/COUT right at the pins, tiny LX loop, exposed pad to
a solid ground plane.

## Pitfalls seen
- Per the Pico 2 datasheet: ADC-capable GPIO26-29 have a reverse diode to IOVDD (input must not exceed IOVDD + ~0.3 V,
  and voltage applied while unpowered leaks into the 3.3 V rail); GPIO0-25 and the debug pins have no such restriction.
  Check the RP2350 datasheet before relying on 5 V tolerance for anything else.
- Don't forget PWR_FLAGs on nets driven only through passives (VBUS, VSYS, +1V1, filtered supplies) or ERC reports
  "power pin not driven".
