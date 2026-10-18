"""Worked example: the rp2350-devboard schematic layout (RP2350A + RT6150B buck-boost + USB-C + Pico 2 headers).

    PYTHONPATH=<plugin>/tools python3 rp2350_devboard_layout.py SRC.kicad_sch DST.kicad_sch --expect nets.json

SRC is the label-only schematic the KiCad MCP produced (every pin has a label, no wires, no power symbols).
nets.json comes from `python3 -m kicad_sch nets SRC --json nets.json` and guards connectivity.

Conventions worth copying for other boards:
  * Local connections are wires (QSPI bundle, crystal, regulator parts, LED chains); rails use power symbols;
    only nets that cross blocks or fan out (GPIO to headers, USB, SWD) keep stub+label.
  * Mirror a part (mirror='y') so its pins line up with a neighbour and the bus becomes straight wires.
  * Dense 2.54 mm headers: signal = short stub + label, power = long stub + power symbol (see pin_or_power).
  * All coordinates are grid units (1.27 mm); `r.pin(ref, num)` returns mm.
"""
from kicad_sch.core import g
from kicad_sch.relayout import run

# part numbers: ref -> (LCSC, MPN) groups
PARTS = {
    ('U1',): ('C42411118', 'RP2350A'), ('U2',): ('C147972', 'RT6150BGQW'), ('U3',): ('C179173', 'W25Q32JVSSIQ'),
    ('J1',): ('C165948', 'TYPE-C-31-M-12'), ('J2', 'J3'): ('C22465880', 'PZ254V-11-20P'),
    ('J4',): ('C7430445', 'ZX-SH1.0-3PWT'), ('X1',): ('C20625731', 'ABM8-272-T3'),
    ('L1',): ('C43389', 'SWPA3015S2R2MT'), ('L2',): ('C42411119', 'AOTA-B201610S3R3-101-T'),
    ('D1',): ('C2480', 'SS14'), ('SW1', 'SW2'): ('C318884', 'TS-1187A-B-A-B'),
    ('LED1',): ('C2286', 'KT-0603R'), ('LED2',): ('C12624', 'KT-0603G'),
    ('R1', 'R2'): ('C25905', '0402WGF5101TCE'), ('R3', 'R13'): ('C25744', '0402WGF1002TCE'),
    ('R4', 'R5', 'R7', 'R18'): ('C25741', '0402WGF1003TCE'), ('R6',): ('C25764', '0402WGF2003TCE'),
    ('R8', 'R9'): ('C25156', '0402WGJ0270TCE'), ('R10',): ('C25105', '0402WGF330JTCE'),
    ('R11', 'R12', 'R16'): ('C11702', '0402WGF1001TCE'), ('R14',): ('C25087', '0402WGF2000TCE'),
    ('R15',): ('C17168', '0402WGF0000TCE'), ('R17',): ('C25117', '0402WGF4700TCE'),
    ('C1', 'C2', 'C3', 'C4'): ('C19702', 'CL10A106KP8NNNC'),
    ('C5', 'C9', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16', 'C17', 'C18', 'C19', 'C20', 'C22'): ('C1525', 'CL05B104KO5NNNC'),
    ('C6', 'C7', 'C8'): ('C307423', 'CL05A475MQ5NRNC'), ('C21',): ('C12530', 'CL05A225MQ5NSNC'),
    ('C23', 'C24'): ('C1548', '0402CG150J500NT'),
}

# (x, y, rot[, mirror]) in grid units
PLACE = {
    'J1': (30, 45, 0), 'D1': (70, 33, 180), 'R3': (56, 36, 0), 'R4': (56, 42, 0),
    'R1': (60, 52, 0), 'R2': (70, 52, 0), 'FLG1': (62, 33, 0), 'FLG2': (83, 33, 0),
    'U2': (124, 39, 0), 'C1': (78, 36, 0), 'C2': (88, 36, 0), 'R5': (110, 36, 0), 'R18': (110, 44, 0),
    'C3': (152, 38, 0), 'C4': (161, 38, 0), 'L1': (141, 42, 0),
    'R6': (110, 62, 0), 'R7': (110, 68, 0), 'C5': (121, 68, 0),
    'U1': (200, 125, 0), 'U3': (150, 127, 0, 'y'), 'C20': (138, 121, 0),
    'X1': (168, 140, 270), 'C23': (160, 140, 0), 'C24': (168, 148, 0), 'R11': (177, 145, 90),
    'R8': (177, 113, 270), 'R9': (177, 115, 270),
    'R10': (180, 80, 0), 'C8': (176, 89, 0), 'L2': (204, 82, 180), 'C7': (214, 82, 0),
    'C15': (223, 82, 0), 'C16': (232, 82, 0), 'C17': (241, 82, 0), 'FLG4': (244, 79, 0), 'FLG5': (183, 86, 0),
    'R13': (40, 186, 0), 'R12': (52, 186, 0), 'SW1': (60, 191, 0), 'SW2': (80, 205, 0),
    'J4': (110, 201, 0), 'R16': (130, 200, 0), 'LED1': (130, 206, 90), 'R17': (140, 200, 0),
    'LED2': (140, 206, 90), 'R15': (150, 200, 0), 'R14': (160, 200, 0), 'C21': (164, 206, 0),
    'C22': (173, 206, 0), 'FLG6': (175, 203, 0), 'J2': (262, 50, 0), 'J3': (300, 50, 0), 'FLG3': (27, 63, 0),
}
DECOUPLING = ['C6', 'C9', 'C10', 'C11', 'C12', 'C13', 'C14', 'C18', 'C19']  # 100nF / 4.7uF row on +3V3

# Reference/Value text: ref -> {field: (x, y, justify, hide)}
TEXT = {
    'R8': {'Reference': (177, 110.5, None, False), 'Value': (177, 110.5, None, True)},
    'R9': {'Reference': (177, 118.5, None, False), 'Value': (177, 118.5, None, True)},
    'J1': {'Reference': (30, 21, None, False), 'Value': (30, 23, None, False)},
    'R5': {'Reference': (111, 29.5, 'left', False), 'Value': (111, 31, 'left', False)},
    'D1': {'Reference': (70, 30, None, False), 'Value': (70, 36, None, False)},
    'R3': {'Reference': (59, 34.5, 'left', False), 'Value': (59, 36.5, 'left', False)},
    'R18': {'Reference': (107, 43, 'right', False), 'Value': (107, 45, 'right', False)},
    'U2': {'Reference': (124, 31, None, False), 'Value': (126, 52, None, False)},
    'U3': {'Reference': (150, 110.5, None, False), 'Value': (150, 144, None, False)},
    'U1': {'Reference': (200, 112, None, False), 'Value': (200, 114, None, False)},
    'LED1': {'Reference': (135, 205, None, False), 'Value': (135, 207, None, False)},
    'LED2': {'Reference': (145, 205, None, False), 'Value': (145, 207, None, False)},
    'X1': {'Reference': (168, 134, None, False), 'Value': (171, 140, 'left', False)},
    'C23': {'Reference': (157.5, 139, 'right', False), 'Value': (157.5, 141, 'right', False)},
    'R10': {'Reference': (178, 79, 'right', False), 'Value': (178, 81, 'right', False)},
    'L2': {'Reference': (206, 75, 'left', False), 'Value': (206, 77, 'left', False)},
}

HEADERS = {
    'J2': ['GP0', 'GP1', 'GND', 'GP2', 'GP3', 'GP4', 'GP5', 'GND', 'GP6', 'GP7', 'GP8', 'GP9', 'GND', 'GP10', 'GP11',
           'GP12', 'GP13', 'GND', 'GP14', 'GP15'],
    'J3': ['GP16', 'GP17', 'GND', 'GP18', 'GP19', 'GP20', 'GP21', 'GND', 'GP22', 'RUN', 'GP26', 'GP27', 'AGND', 'GP28',
           'ADC_AVDD', '+3V3', '3V3_EN', 'GND', 'VSYS', 'VBUS'],
}
# U1 GPIO pins (right side) -> net label
GPIO = {'2': 'GP0', '3': 'GP1', '4': 'GP2', '5': 'GP3', '7': 'GP4', '8': 'GP5', '9': 'GP6', '10': 'GP7', '12': 'GP8',
        '13': 'GP9', '14': 'GP10', '15': 'GP11', '16': 'GP12', '17': 'GP13', '18': 'GP14', '19': 'GP15', '27': 'GP16',
        '28': 'GP17', '29': 'GP18', '31': 'GP19', '32': 'GP20', '33': 'GP21', '34': 'GP22', '35': 'SMPS_PS',
        '36': 'VBUS_SENSE', '37': 'USER_LED', '40': 'GP26', '41': 'GP27', '42': 'GP28', '43': 'VSYS_SENSE'}


def layout(r):
    at = r.at
    pin = r.pin
    for ref, v in PLACE.items():
        r.place(ref, *v)
    for i, ref in enumerate(DECOUPLING):
        r.place(ref, 150 + 8 * i, 65)
    for ref, fields in TEXT.items():
        for field, (x, y, just, hide) in fields.items():
            r.set_text(ref, field, x, y, just, hide)
    for refs, (lcsc, mpn) in PARTS.items():
        for ref in refs:
            r.set_part(ref, lcsc, mpn)
    r.set_footprint('L2', 'rp2350-devboard:L_Abracon_AOTA-B201610S3R3_2.0x1.6mm')
    r.set_dnp('R13')
    r.set_paper('A3')
    r.set_title_block('RP2350 Devboard', '2026-10-18', 'A', 'RP2350A, USB-C powered, Pico 2 pinout')

    # ---- USB-C -> VBUS -> D1
    r.seg(pin('J1', 'A4'), pin('D1', '2'))
    r.pwr('VBUS', at(52, 33))
    r.seg(pin('J1', 'SH'), pin('J1', 'A1'))
    r.pwr('GND', pin('J1', 'A1'))
    for n in ('A8', 'B8'):
        r.no_connect(pin('J1', n))
    r.stub_label('J1', 'A5', 'CC1', 'r', 2)
    r.stub_label('J1', 'B5', 'CC2', 'r', 2)
    for a, b, name in (('A7', 'B7', 'USB_D-'), ('A6', 'B6', 'USB_D+')):     # the connector repeats D+/D-: join them
        pa, pb = pin('J1', a), pin('J1', b)
        jx = round(pa[0] + g(3), 4)
        r.seg(pa, (jx, pa[1])); r.seg(pb, (jx, pb[1])); r.seg((jx, pa[1]), (jx, pb[1]))
        e = (round(jx + g(2), 4), pa[1])
        r.seg((jx, pa[1]), e)
        r.label(name, e, 0)
    r.vstub('R1', '1', 'CC1', 'u'); r.pwr('GND', pin('R1', '2'))
    r.vstub('R2', '1', 'CC2', 'u'); r.pwr('GND', pin('R2', '2'))
    r.seg(pin('R3', '2'), pin('R4', '1'))
    r.pwr('GND', pin('R4', '2'))
    r.stub_label('R3', '2', 'VBUS_SENSE', 'r', 2)

    # ---- D1 -> VSYS rail -> RT6150B
    rail_y = g(33)
    r.seg(pin('D1', '1'), (g(112), rail_y))
    r.label('VSYS', at(100, 33), 0)
    r.seg((g(112), rail_y), (g(112), pin('U2', '8')[1]))
    r.seg(pin('U2', '5'), (g(112), pin('U2', '5')[1]))
    r.seg(pin('U2', '8'), (g(112), pin('U2', '8')[1]))
    for c in ('C1', 'C2'):
        r.pwr('GND', pin(c, '2'))
    r.seg(pin('U2', '6'), pin('R5', '2'))
    r.seg(pin('R5', '2'), at(106, 39))
    r.label('3V3_EN', at(106, 39), 180)
    r.seg(pin('U2', '7'), pin('R18', '1'))
    r.seg(pin('R18', '1'), at(106, 41))
    r.label('SMPS_PS', at(106, 41), 180)
    r.pwr('GND', pin('R18', '2'))
    r.pwr('GND', pin('U2', '3'))
    r.seg(pin('U2', '1'), (g(136), pin('U2', '1')[1]))
    r.seg(pin('U2', '10'), (g(136), pin('U2', '10')[1]))
    r.seg((g(136), pin('U2', '1')[1]), (g(136), pin('U2', '10')[1]))
    r.seg((g(136), pin('U2', '1')[1]), (g(165), pin('U2', '1')[1]))
    r.pwr('+3V3', (g(165), pin('U2', '1')[1]))
    for c in ('C3', 'C4'):
        r.pwr('GND', pin(c, '2'))
    lx1, lx2 = pin('U2', '4'), pin('U2', '2')                              # inductor between LX1 and LX2
    for lx, lp in ((lx1, '1'), (lx2, '2')):
        r.path(lx, (g(135), lx[1]), (g(135), pin('L1', lp)[1]), pin('L1', lp))

    # VSYS divider (labelled cluster)
    r.vstub('R6', '1', 'VSYS', 'u')
    r.seg(pin('R6', '2'), pin('R7', '1'))
    r.seg(pin('R6', '2'), pin('C5', '1'))
    r.stub_label('R6', '2', 'VSYS_SENSE', 'l', 3)
    r.pwr('GND', pin('R7', '2')); r.pwr('GND', pin('C5', '2'))

    # ---- RP2350A
    U = lambda n: pin('U1', n)
    for u1pin, u3pin, nm in (('60', '1', 'QSPI_SS'), ('56', '6', 'QSPI_SCLK'), ('57', '5', 'QSPI_SD0'),
                             ('59', '2', 'QSPI_SD1'), ('58', '3', 'QSPI_SD2'), ('55', '7', 'QSPI_SD3')):
        a, b = U(u1pin), pin('U3', u3pin)
        assert abs(a[1] - b[1]) < 0.01, (nm, a, b)                         # U3 is placed so the bus is straight
        r.seg(a, b)
        r.label(nm, (g(170), a[1]), 0)
    r.pwr('+3V3', pin('U3', '8')); r.pwr('GND', pin('U3', '4'))
    r.pwr('+3V3', pin('C20', '1')); r.pwr('GND', pin('C20', '2'))
    # crystal
    xin = U('21')
    r.seg(xin, (g(160), xin[1]))
    r.label('XIN', at(173, 137), 0)
    r.pwr('GND', pin('C23', '2')); r.pwr('GND', pin('X1', '2'))
    x3 = pin('X1', '3')
    r.seg(x3, (x3[0], pin('R11', '1')[1])); r.seg((x3[0], pin('R11', '1')[1]), pin('R11', '1'))
    r.label('XTAL_B', at(170, 145), 0)
    r.pwr('GND', pin('C24', '2'))
    r.stub_label('U1', '24', 'SWCLK', 'l', 2)
    r.stub_label('U1', '25', 'SWDIO', 'l', 2)
    r.stub_label('U1', '26', 'RUN', 'l', 4)
    r.stub_label('R8', '2', 'USB_D-', 'l', 3)                              # R8/R9 pin 1 coincides with the U1 pin
    r.stub_label('R9', '2', 'USB_D+', 'l', 3)
    for n, nm in GPIO.items():
        r.stub_label('U1', n, nm, 'r', 3)
    r.seg(U('47'), U('61'))
    r.pwr('GND', U('61'))
    # +3V3 bus above the chip (QSPI_IOVDD, IOVDD stack, VREG_VIN)
    bus_y = U('1')[1] - g(6)
    for n in ('54', '1', '49'):
        r.seg(U(n), (U(n)[0], bus_y))
    r.seg((U('54')[0], bus_y), (U('49')[0], bus_y))
    r.pwr('+3V3', (U('1')[0], bus_y))
    otp = (U('53')[0], U('53')[1] - g(3))                                  # USB_OTP_VDD: own stub + symbol
    r.seg(U('53'), otp)
    r.pwr('+3V3', otp, hide_value=True)
    adc = U('44')
    r.seg(adc, (adc[0], adc[1] - g(2)))
    r.label('ADC_AVDD', (adc[0], adc[1] - g(2)), 90)
    va = U('46')                                                           # VREG_AVDD RC filter
    r.seg(va, (va[0], g(86)))
    r.seg((va[0], g(86)), pin('C8', '1'))
    r.seg(pin('R10', '2'), (pin('R10', '2')[0], g(86)))
    r.pwr('+3V3', pin('R10', '1')); r.pwr('GND', pin('C8', '2'))
    r.seg(U('48'), pin('L2', '1'))                                         # VREG_LX -> L2 (pin 1 = LX) -> +1V1
    rail1 = pin('L2', '2')[1]
    r.seg(pin('L2', '2'), (pin('C17', '1')[0] + g(3), rail1))
    r.seg(U('50'), (U('50')[0], rail1))
    r.seg(U('6'), (U('6')[0], rail1))
    for c in ('C7', 'C15', 'C16', 'C17'):
        r.pwr('GND', pin(c, '2'))
    r.pwr('+1V1', (g(218), rail1))
    for c in DECOUPLING:
        r.pwr('+3V3', pin(c, '1')); r.pwr('GND', pin(c, '2'))

    # ---- bottom clusters: BOOTSEL / RESET / SWD / LEDs / AGND / ADC supply
    r.pwr('+3V3', pin('R13', '1'))
    r.vstub('R12', '1', 'QSPI_SS', 'u')
    r.seg(pin('R13', '2'), (pin('R13', '2')[0], pin('R13', '2')[1] + g(2)))
    r.label('QSPI_SS', (pin('R13', '2')[0], pin('R13', '2')[1] + g(2)), 270)
    sw1a = pin('SW1', '1')
    r.seg(pin('R12', '2'), (pin('R12', '2')[0], sw1a[1])); r.seg((pin('R12', '2')[0], sw1a[1]), sw1a)
    r.pwr('GND', pin('SW1', '2'))
    r.stub_label('SW2', '1', 'RUN', 'l', 2); r.pwr('GND', pin('SW2', '2'))
    r.stub_label('J4', '1', 'SWCLK', 'l', 3); r.pin_or_power('J4', '2', 'GND', 10); r.stub_label('J4', '3', 'SWDIO', 'l', 3)
    r.pwr('+3V3', pin('R16', '1'))
    r.seg(pin('R16', '2'), pin('LED1', '2')); r.pwr('GND', pin('LED1', '1'))
    r.vstub('R17', '1', 'USER_LED', 'u')
    r.seg(pin('R17', '2'), pin('LED2', '2')); r.pwr('GND', pin('LED2', '1'))
    r.vstub('R15', '1', 'AGND', 'u'); r.pwr('GND', pin('R15', '2'))
    r.pwr('+3V3', pin('R14', '1'))
    n_adc = pin('R14', '2')
    r.seg(n_adc, (pin('C22', '1')[0], n_adc[1]))
    r.seg(pin('C22', '1'), (pin('C22', '1')[0], n_adc[1]))
    r.seg((pin('C22', '1')[0], n_adc[1]), (pin('C22', '1')[0] + g(4), n_adc[1]))
    r.label('ADC_AVDD', (pin('C22', '1')[0] + g(4), n_adc[1]), 0)
    r.pwr('GND', pin('C21', '2')); r.pwr('GND', pin('C22', '2'))

    # ---- Pico 2 pin-order headers
    for ref, nets in HEADERS.items():
        for i, nm in enumerate(nets, 1):
            r.pin_or_power(ref, str(i), nm, 13)

    for text, (x, y) in (('USB-C input', (22, 20)), ('3.3 V buck-boost (RT6150B)', (90, 20)), ('QSPI flash', (128, 106)),
                         ('Core supply 1.1 V', (222, 73)), ('Crystal', (140, 149)), ('Pico 2 pin 1-20', (246, 24)),
                         ('Pico 2 pin 21-40', (284, 24)), ('BOOTSEL / RESET / SWD', (38, 178)),
                         ('LEDs / AGND', (126, 184)), ('ADC supply', (156, 192)), ('VSYS sense', (94, 56)),
                         ('3V3 decoupling', (148, 56))):
        r.title(text, x, y)


if __name__ == '__main__':
    run(layout)
