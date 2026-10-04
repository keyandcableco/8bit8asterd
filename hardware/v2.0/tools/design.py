"""8b8 v2.0 -- the netlist, as data.

Single source of truth: gen_sch.py and gen_pcb.py both read PARTS from here, so
the schematic and the board cannot drift apart.

Each part:  ref -> dict(sym, fp, value, lcsc, pins={pin_number: net}, note)
Power nets are GND, +5V, +3V3, +1V1.  Everything else is a signal net.
"""

# ---- footprints -----------------------------------------------------------
R0402 = 'Resistor_SMD:R_0402_1005Metric'
C0402 = 'Capacitor_SMD:C_0402_1005Metric'
C0603 = 'Capacitor_SMD:C_0603_1608Metric'
LED0603 = 'LED_SMD:LED_0603_1608Metric'
SOD123 = 'Diode_SMD:D_SOD-123'
TP = 'TestPoint:TestPoint_Pad_D1.0mm'
FP_TERM10 = 'Connector_Phoenix_MC:PhoenixContact_MC_1,5_10-G-3.5_1x10_P3.50mm_Horizontal'
FP_TERM2 = 'Connector_Phoenix_MC:PhoenixContact_MC_1,5_2-G-3.5_1x02_P3.50mm_Horizontal'

# ---- LCSC part numbers (JLC assembly) -------------------------------------
LC = dict(
    rp2040='C2040', flash='C179173', buf245='C5979', mux='C9386', sreg='C5613',
    ldo='C51118', xtal='C9002', usbc='C165948', esd='C7519', opto='C16496',
    d4148='C81598', fuse='C70078', led_g='C12624', led_r='C2286', tact='C318884',
    term10='C430562', term2='C395830', dip40='C2332',
    r10k='C25744', r1k='C11702', r5k1='C25905', r27='C25156', r100k='C25741',
    r220='C25091', r75='C25133',
    c100n='C1525', c1u='C52923', c10n='C15195', c15p='C1548', c10u3v3='C19702',
    c10u5v='C96446', c1u_0603='C106248', c47n='C272875',
)

PARTS = {}
_order = []


def add(ref, sym, fp, value, lcsc, pins, note='', dnp=False, section=''):
    assert ref not in PARTS, ref
    PARTS[ref] = dict(sym=sym, fp=fp, value=value, lcsc=lcsc, pins=dict(pins),
                      note=note, dnp=dnp, section=section)
    _order.append(ref)


_counters = {}


def _ref(prefix):
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f'{prefix}{_counters[prefix]}'


def R(value, a, b, lcsc, section='', ref=None):
    add(ref or _ref('R'), 'Device:R', R0402, value, lcsc, {'1': a, '2': b}, section=section)


def C(value, a, b, lcsc, fp=C0402, section='', ref=None):
    add(ref or _ref('C'), 'Device:C', fp, value, lcsc, {'1': a, '2': b}, section=section)


def bypass(net, n=1, section=''):
    for _ in range(n):
        C('100nF', net, 'GND', LC['c100n'], section=section)


# ===========================================================================
# POWER IN: USB-C -> fuse -> +5V -> AP2112K -> +3V3
# ===========================================================================
S = 'power'
add('J1', 'Connector:USB_C_Receptacle_USB2.0_16P',
    'Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12', 'USB-C', LC['usbc'],
    {'A1': 'GND', 'B12': 'GND', 'A12': 'GND', 'B1': 'GND',
     'A4': 'VBUS', 'B9': 'VBUS', 'A9': 'VBUS', 'B4': 'VBUS',
     'A5': 'CC1', 'B5': 'CC2',
     'A6': 'USB_DP_C', 'B6': 'USB_DP_C', 'A7': 'USB_DM_C', 'B7': 'USB_DM_C',
     'A8': 'SBU1', 'B8': 'SBU2', 'S1': 'GND'},
    note='USB power, MIDI and programming', section=S)
R('5.1k', 'CC1', 'GND', LC['r5k1'], section=S)
R('5.1k', 'CC2', 'GND', LC['r5k1'], section=S)
add('F1', 'Device:Polyfuse', 'Fuse:Fuse_1206_3216Metric', '0.75A', LC['fuse'],
    {'1': 'VBUS', '2': '+5V'}, note='resettable, protects the USB port', section=S)
C('10uF', '+5V', 'GND', LC['c10u5v'], fp=C0603, section=S)
C('10uF', '+5V', 'GND', LC['c10u5v'], fp=C0603, section=S)
C('10uF', '+5V', 'GND', LC['c10u5v'], fp=C0603, section=S)
add('U1', 'Regulator_Linear:AP2112K-3.3', 'Package_TO_SOT_SMD:SOT-23-5', 'AP2112K-3.3',
    LC['ldo'], {'1': '+5V', '3': '+5V', '2': 'GND', '4': 'NC_LDO', '5': '+3V3'},
    note='3.3V for the RP2040, shift registers and mux', section=S)
C('1uF', '+5V', 'GND', LC['c1u'], section=S)
C('10uF', '+3V3', 'GND', LC['c10u3v3'], fp=C0603, section=S)
add('D1', 'Device:LED', LED0603, 'PWR', LC['led_g'], {'2': 'LED_PWR_A', '1': 'GND'}, section=S)
R('1k', '+5V', 'LED_PWR_A', LC['r1k'], section=S)

# ===========================================================================
# RP2040 core
# ===========================================================================
S = 'mcu'
GP = {n: f'GP{n}' for n in range(30)}
# Net name for a GPIO that carries a named signal (so the schematic reads well):
SIG = {
    0: 'D0_MCU', 1: 'D1_MCU', 2: 'D2_MCU', 3: 'D3_MCU', 4: 'D4_MCU', 5: 'D5_MCU', 6: 'D6_MCU', 7: 'D7_MCU',
    8: 'BDIR_A_MCU', 9: 'BC2_A_MCU', 10: 'BDIR_B_MCU', 11: 'BC2_B_MCU', 12: 'BDIR_C_MCU', 13: 'BC2_C_MCU',
    14: 'RESET_MCU', 15: 'LED_MIDI_MCU', 16: 'GP16', 17: 'MIDI_RX', 18: 'MUX_S0', 19: 'MUX_S1',
    20: 'MUX_S2', 21: 'CLK_MCU', 22: 'SR_PL', 23: 'SR_CP', 24: 'SR_Q7', 25: 'LED_STAT_MCU',
    26: 'MUX_Z', 27: 'GP27', 28: 'GP28', 29: 'GP29',
}
pins = {
    '1': '+3V3', '10': '+3V3', '22': '+3V3', '33': '+3V3', '42': '+3V3', '49': '+3V3',
    '23': '+1V1', '50': '+1V1', '45': '+1V1', '44': '+3V3', '43': '+3V3', '48': '+3V3',
    '19': 'GND', '57': 'GND',
    '20': 'XIN', '21': 'XOUT_MCU', '24': 'SWCLK', '25': 'SWD', '26': 'RUN',
    '46': 'USB_DM', '47': 'USB_DP',
    '51': 'QSPI_SD3', '52': 'QSPI_SCLK', '53': 'QSPI_SD0', '54': 'QSPI_SD2', '55': 'QSPI_SD1',
    '56': 'QSPI_SS',
}
gpio_pin = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 8, 7: 9, 8: 11, 9: 12, 10: 13, 11: 14, 12: 15,
            13: 16, 14: 17, 15: 18, 16: 27, 17: 28, 18: 29, 19: 30, 20: 31, 21: 32, 22: 34,
            23: 35, 24: 36, 25: 37, 26: 38, 27: 39, 28: 40, 29: 41}
for g, p in gpio_pin.items():
    pins[str(p)] = SIG[g]
add('U2', 'MCU_RaspberryPi:RP2040', 'Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm',
    'RP2040', LC['rp2040'], pins, section=S)
# decoupling: six IOVDD, two DVDD, USB_VDD, ADC_AVDD at 100nF; VREG in/out 1uF
bypass('+3V3', 8, S)          # 6 IOVDD + USB_VDD + ADC_AVDD
bypass('+1V1', 2, S)          # DVDD
C('1uF', '+3V3', 'GND', LC['c1u'], section=S)   # VREG_VIN
C('1uF', '+1V1', 'GND', LC['c1u'], section=S)   # VREG_VOUT
# crystal: 12MHz, 15pF load caps, 1k series on XOUT per the RP2040 hardware guide
add('Y1', 'Device:Crystal_GND24', 'Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm', '12MHz', LC['xtal'],
    {'1': 'XIN', '3': 'XOUT_X', '2': 'GND', '4': 'GND'}, section=S)
C('15pF', 'XIN', 'GND', LC['c15p'], section=S)
C('15pF', 'XOUT_X', 'GND', LC['c15p'], section=S)
R('1k', 'XOUT_MCU', 'XOUT_X', LC['r1k'], section=S)
# QSPI flash
add('U3', 'Memory_Flash:W25Q32JVSS', 'Package_SO:SOIC-8_5.3x5.3mm_P1.27mm', 'W25Q32JVSSIQ',
    LC['flash'],
    {'1': 'QSPI_SS', '2': 'QSPI_SD1', '3': 'QSPI_SD2', '4': 'GND', '5': 'QSPI_SD0',
     '6': 'QSPI_SCLK', '7': 'QSPI_SD3', '8': '+3V3'}, note='4MB program flash', section=S)
bypass('+3V3', 1, S)
# BOOTSEL (hold while plugging in USB to get the RP2040 mass-storage bootloader) and RESET
add('SW1', 'Switch:SW_Push', 'Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2', 'BOOT', LC['tact'],
    {'1': 'BOOTSEL', '2': 'GND'}, note='hold while plugging in USB to flash', section=S)
R('1k', 'QSPI_SS', 'BOOTSEL', LC['r1k'], section=S)
add('SW2', 'Switch:SW_Push', 'Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2', 'RESET', LC['tact'],
    {'1': 'RUN', '2': 'GND'}, section=S)
# USB data: ESD diode on the connector side, 27R series on the chip side
add('U4', 'Power_Protection:USBLC6-2SC6', 'Package_TO_SOT_SMD:SOT-23-6', 'USBLC6-2SC6',
    LC['esd'], {'1': 'USB_DP_C', '6': 'USB_DP_C', '3': 'USB_DM_C', '4': 'USB_DM_C',
                '2': 'GND', '5': 'VBUS'}, section=S)
R('27', 'USB_DP_C', 'USB_DP', LC['r27'], section=S)
R('27', 'USB_DM_C', 'USB_DM', LC['r27'], section=S)
# status LEDs (GP15 blinks on MIDI, GP25 is a general status LED)
add('D2', 'Device:LED', LED0603, 'MIDI', LC['led_g'], {'2': 'LED_MIDI_A', '1': 'GND'}, section=S)
R('1k', 'LED_MIDI_MCU', 'LED_MIDI_A', LC['r1k'], section=S)
add('D3', 'Device:LED', LED0603, 'STATUS', LC['led_r'], {'2': 'LED_STAT_A', '1': 'GND'}, section=S)
R('1k', 'LED_STAT_MCU', 'LED_STAT_A', LC['r1k'], section=S)
# test points: SWD, spare GPIOs, rails
for name, net in [('SWCLK', 'SWCLK'), ('SWD', 'SWD'), ('GP16', 'GP16'), ('GP27', 'GP27'),
                  ('GP28', 'GP28'), ('GP29', 'GP29'), ('3V3', '+3V3'), ('5V', '+5V'),
                  ('GND', 'GND')]:
    add(f'TP_{name}', 'Connector:TestPoint', TP, f'TP_{name}', '', {'1': net}, section=S)

# ===========================================================================
# 5V LEVEL BUFFERS between the 3.3V RP2040 and the 5V sound chips
# ===========================================================================
S = 'buffers'
# U5: data bus.  A (3.3V side, from GPIO) -> B (5V side, to the three YM2149s)
add('U5', '74xx:74HC245',
    'Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm', '74HCT245', LC['buf245'],
    {'1': '+5V', '19': 'GND', '20': '+5V', '10': 'GND',
     # Channel k carries bit 7-k: U5 sits rotated 180 degrees, so this keeps the
     # bus in the same top-to-bottom order as the RP2040 pins and the chips'
     # DA pins -- no crossings.  GPIOn still drives DAn.
     **{str(2 + k): f'D{7 - k}_MCU' for k in range(8)},
     **{str(18 - k): f'DA{7 - k}' for k in range(8)}},
    note='HCT: 5V supply, accepts 3.3V logic', section=S)
bypass('+5V', 1, S)
# U6: control lines.  /OE tied low (always enabled), DIR tied high (A->B).
CTRL_MCU = ['BDIR_A_MCU', 'BC2_A_MCU', 'BDIR_B_MCU', 'BC2_B_MCU', 'BDIR_C_MCU', 'BC2_C_MCU',
            'RESET_MCU', 'CLK_MCU']
CTRL_OUT = ['BDIR_A', 'BC2_A', 'BDIR_B', 'BC2_B', 'BDIR_C', 'BC2_C', 'AY_RESET', 'CLK_BUF']
add('U6', '74xx:74HC245', 'Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm', '74HCT245', LC['buf245'],
    {'1': '+5V', '19': 'GND', '20': '+5V', '10': 'GND',
     # reversed for the same reason as U5
     **{str(2 + k): CTRL_MCU[7 - k] for k in range(8)},
     **{str(18 - k): CTRL_OUT[7 - k] for k in range(8)}},
    note='HCT: 5V supply, accepts 3.3V logic', section=S)
bypass('+5V', 1, S)
# pull-downs so the chips sit in reset / bus-idle until the firmware takes over
for n in CTRL_MCU:
    R('100k', n, 'GND', LC['r100k'], section=S)
# 75R series damping on the clock, as on v1.0 (R3 there)
R('75', 'CLK_BUF', 'AY_CLK', LC['r75'], section=S)

# ===========================================================================
# THREE YM2149 / AY-3-8910 -- sockets, 100nF each
# ===========================================================================
S = 'ay'
for idx, (ref, tag) in enumerate([('U7', 'A'), ('U8', 'B'), ('U9', 'C')]):
    p = {'1': 'GND', '3': 'AUDIO', '4': 'AUDIO', '38': 'AUDIO',
         '22': 'AY_CLK', '23': 'AY_RESET',
         '27': f'BDIR_{tag}', '28': f'BC2_{tag}', '29': 'GND',
         '40': '+5V'}
    for i in range(8):
        p[str(37 - i)] = f'DA{i}'    # DA0 on pin 37 ... DA7 on pin 30
    # 24 (/A9) and 25 (A8) are left open, as on v1.0 (the chip pulls them to the
    # right state); 26 (/SEL) is left open too -- internally high = full-speed
    # clock.  v1.0 brought /SEL to a terminal for the "clock bend".
    add(ref, 'Audio:YM2149', 'Package_DIP:DIP-40_W15.24mm_Socket',
        'YM2149 / AY-3-8910', LC['dip40'], p, note=f'chip {tag}', section=S)
    C('100nF', '+5V', 'GND', LC['c100n'], section=S)

# analog out: same network as v1.0.  C2 there is labelled "47uF" but drawn as a
# 5mm disc ceramic, so it is treated as 47nF here (3.4kHz low-pass with R2).
S = 'audio'
R('1k', 'AUDIO', 'GND', LC['r1k'], section=S)                   # R2 on v1.0
C('47nF', 'AUDIO', 'GND', LC['c47n'], section=S)                # C2 on v1.0
C('1uF', 'AUDIO', 'AUDIO_OUT', LC['c1u_0603'], fp=C0603, section=S)   # C1 on v1.0
R('100k', 'AUDIO_OUT', 'GND', LC['r100k'], section=S)           # R1 on v1.0
add('J2', 'Connector:Screw_Terminal_01x02', FP_TERM2, 'AUDIO OUT', LC['term2'],
    {'1': 'AUDIO_OUT', '2': 'GND'}, note='1 = signal, 2 = ground', section=S)

# ===========================================================================
# MIDI IN (opto-isolated, as v1.0 but 3.3V output)
# ===========================================================================
S = 'midi'
add('J3', 'Connector:Screw_Terminal_01x02', FP_TERM2, 'MIDI IN', LC['term2'],
    {'1': 'MIDI_5', '2': 'MIDI_4'}, note='DIN pin 5 / DIN pin 4', section=S)
R('220', 'MIDI_4', 'OPTO_A', LC['r220'], section=S)
add('U10', 'Isolator:6N137', 'Package_DIP:SMDIP-8_W9.53mm', '6N137S', LC['opto'],
    {'1': 'NC_O1', '2': 'OPTO_A', '3': 'MIDI_5', '4': 'NC_O4', '5': 'GND', '6': 'MIDI_RX_OC',
     '7': '+5V', '8': '+5V'}, note='Vcc 5V, open-collector output pulled up to 3.3V',
    section=S)
add('D4', 'Device:D', SOD123, '1N4148W', LC['d4148'], {'1': 'OPTO_A', '2': 'MIDI_5'},
    note='reverse-polarity protection across the opto LED', section=S)
R('10k', 'MIDI_RX_OC', '+3V3', LC['r10k'], section=S)
bypass('+5V', 1, S)
# MIDI_RX_OC is the open-collector node; MIDI_RX is the same signal at the MCU pin.
# They are one net: rename so the schematic shows a single net.
PARTS['U10']['pins']['6'] = 'MIDI_RX'
for ref in list(PARTS):
    for k, v in PARTS[ref]['pins'].items():
        if v == 'MIDI_RX_OC':
            PARTS[ref]['pins'][k] = 'MIDI_RX'

# ===========================================================================
# INPUTS: 8 analog (74HC4051) and 8 digital (74HC165) on pluggable terminals
# ===========================================================================
S = 'inputs'
add('J4', 'Connector:Screw_Terminal_01x10', FP_TERM10, 'ANALOG IN', LC['term10'],
    {**{str(i + 1): f'IN_A{i + 1}' for i in range(8)}, '9': '+3V3', '10': 'GND'},
    note='1-8 wipers; 9 = 3.3V (pot top), 10 = GND (pot bottom)', section=S)
add('U11', '74xx:74HC4051', 'Package_SO:SOIC-16_3.9x9.9mm_P1.27mm', '74HC4051', LC['mux'],
    {'13': 'MUX_Y0', '14': 'MUX_Y1', '15': 'MUX_Y2', '12': 'MUX_Y3', '1': 'MUX_Y4',
     '5': 'MUX_Y5', '2': 'MUX_Y6', '4': 'MUX_Y7', '3': 'MUX_Z', '6': 'GND', '7': 'GND',
     '8': 'GND', '9': 'MUX_S2', '10': 'MUX_S1', '11': 'MUX_S0', '16': '+3V3'},
    note='8:1 analog mux into ADC0', section=S)
bypass('+3V3', 1, S)
for i in range(8):
    R('1k', f'IN_A{i + 1}', f'MUX_Y{i}', LC['r1k'], section=S)      # series protection
    C('10nF', f'MUX_Y{i}', 'GND', LC['c10n'], section=S)           # filter / sample hold
add('J5', 'Connector:Screw_Terminal_01x10', FP_TERM10, 'SWITCH IN', LC['term10'],
    {**{str(i + 1): f'IN_D{i + 1}' for i in range(8)}, '9': 'GND', '10': '+3V3'},
    note='1-8 switch/button to GND (active low); 9 = GND, 10 = 3.3V', section=S)
add('U12', '74xx:74HC165', 'Package_SO:SOIC-16_3.9x9.9mm_P1.27mm', '74HC165', LC['sreg'],
    {'1': 'SR_PL', '2': 'SR_CP', '9': 'SR_Q7', '7': 'NC_165', '8': 'GND', '10': 'GND',
     '15': 'GND', '16': '+3V3',
     '11': 'SW_D0', '12': 'SW_D1', '13': 'SW_D2', '14': 'SW_D3',
     '3': 'SW_D4', '4': 'SW_D5', '5': 'SW_D6', '6': 'SW_D7'},
    note='8 digital inputs, read serially', section=S)
bypass('+3V3', 1, S)
for i in range(8):
    R('1k', f'IN_D{i + 1}', f'SW_D{i}', LC['r1k'], section=S)       # series protection
    R('10k', f'SW_D{i}', '+3V3', LC['r10k'], section=S)            # pull-up
    C('100nF', f'SW_D{i}', 'GND', LC['c100n'], section=S)          # contact debounce

# ---- mounting holes -------------------------------------------------------
for i in range(4):
    add(f'H{i + 1}', 'Mechanical:MountingHole', 'MountingHole:MountingHole_3.2mm_M3',
        'M3', '', {}, section='mech')


def ordered():
    return [(r, PARTS[r]) for r in _order]


def nets():
    """net name -> sorted list of (ref, pin)"""
    n = {}
    for ref, p in PARTS.items():
        for pin, net in p['pins'].items():
            n.setdefault(net, []).append((ref, pin))
    return n


if __name__ == '__main__':
    ns = nets()
    print(len(PARTS), 'parts,', len(ns), 'nets')
    single = sorted(k for k, v in ns.items() if len(v) == 1)
    print('single-pin nets:', single)
