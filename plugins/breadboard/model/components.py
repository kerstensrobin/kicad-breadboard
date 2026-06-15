"""
Component type definitions: pin counts, hole offsets, and physical layout.

Placement convention
--------------------
Every component has an "anchor" — the hole where pin 1 lands when the user drops it.

For single-bank components (R, C, L, POT, TO-92 transistors):
  All pins are in the same bank (top or bottom) and the same row as the anchor.
  Pins are at anchor_col + col_delta, same row.

For DIP ICs (TL081, RC4558, TL084):
  The IC straddles the center gap.  Anchor = pin 1, always placed in row 'e'.
  Top-side pins land in row 'e', bottom-side pins land in row 'f'.

Pin numbering follows the standard KiCad symbol convention for each part.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .breadboard import (
    TOP_ROWS, BOT_ROWS, ALL_ROWS, COLUMNS,
    TieHole, RailHole, Terminal, Hole,
)


@dataclass(frozen=True)
class PinOffset:
    """
    Offset of a single pin from the anchor hole.

    col_delta   : column shift from anchor column (may be negative)
    cross_gap   : True for bottom-side pins of DIP ICs (land in row 'f' always)
    row_delta   : additional row shift within the same bank (0 for most parts)
    """
    col_delta: int
    cross_gap: bool = False
    row_delta: int = 0

    def resolve(self, anchor: TieHole, flipped: bool = False,
                cross_flip: bool = True) -> TieHole:
        col = anchor.col + (-self.col_delta if flipped else self.col_delta)
        # cross_gap inversion on flip only applies to DIP ICs (top↔bottom side swap).
        # Single-row components (POT, TO-92, axial) pass cross_flip=False so their
        # pins stay in the anchor row instead of jumping to row 'f'.
        cross = (not self.cross_gap) if (flipped and cross_flip) else self.cross_gap
        if cross:
            row = 'f'   # DIP bottom side always in row f (closest to gap)
        else:
            bank = TOP_ROWS if anchor.row in TOP_ROWS else BOT_ROWS
            idx = bank.index(anchor.row) + self.row_delta
            idx = max(0, min(len(bank) - 1, idx))
            row = bank[idx]
        return TieHole(col, row, anchor.section)


@dataclass
class ComponentDef:
    type_id: str                        # internal identifier, e.g. 'R', 'NPN', 'TL081'
    display_name: str
    ref_prefix: str                     # KiCad ref prefix: R, C, Q, U …
    pin_offsets: Dict[int, PinOffset]   # pin_number → offset from anchor
    pin_names: Dict[int, str]           # pin_number → net-facing name (B, C, E, IN+, …)
    color: str = '#888888'              # body fill color for canvas
    is_dip: bool = False                # True → anchor forced to row 'e'
    symmetric: bool = False             # True → non-polar; validator accepts either pin order
    is_module: bool = False             # True → draw as PCB board (Arduino/RPi style)

    @property
    def pin_count(self) -> int:
        return len(self.pin_offsets)

    def place(self, anchor: TieHole, flipped: bool = False) -> Dict[int, Hole]:
        """
        Resolve all pin holes given an anchor hole.
        For DIP ICs the anchor row is forced to 'e'.
        When flipped=True the component is mirrored horizontally (col_deltas negated).
        Returns {pin_number: TieHole}.
        """
        if self.is_dip:
            anchor = TieHole(anchor.col, 'e', anchor.section)
        return {pin: offset.resolve(anchor, flipped, cross_flip=self.is_dip)
                for pin, offset in self.pin_offsets.items()}

    def footprint_cols(self) -> int:
        """Number of breadboard columns the component occupies."""
        deltas = [o.col_delta for o in self.pin_offsets.values()]
        return max(deltas) - min(deltas) + 1


# ---------------------------------------------------------------------------
# Passive 2-pin components (R, C, L)
# Default span: 5 holes between the two leads.
# ---------------------------------------------------------------------------

RESISTOR = ComponentDef(
    type_id='R',
    display_name='Resistor',
    ref_prefix='R',
    pin_offsets={1: PinOffset(0), 2: PinOffset(5)},
    pin_names={1: '1', 2: '2'},
    color='#e8e4cc',   # ceramic cream — good contrast for colour bands
    symmetric=True,
)

CAPACITOR = ComponentDef(
    type_id='C',
    display_name='Capacitor',
    ref_prefix='C',
    pin_offsets={1: PinOffset(0), 2: PinOffset(3)},
    pin_names={1: '1', 2: '2'},
    color='#4080c0',
    symmetric=True,
)

CAPACITOR_ELECTROLYTIC = ComponentDef(
    type_id='C_POL',
    display_name='Capacitor (electrolytic)',
    ref_prefix='C',
    pin_offsets={1: PinOffset(0), 2: PinOffset(2)},
    pin_names={1: '+', 2: '-'},
    color='#4060a0',
)

INDUCTOR = ComponentDef(
    type_id='L',
    display_name='Inductor',
    ref_prefix='L',
    pin_offsets={1: PinOffset(0), 2: PinOffset(5)},
    pin_names={1: '1', 2: '2'},
    color='#60a080',
    symmetric=True,
)

# ---------------------------------------------------------------------------
# Diodes (axial, 4-hole span — roughly 1N4001 body length)
# Pin 1 = Anode (A), Pin 2 = Cathode (K)
# ---------------------------------------------------------------------------

DIODE = ComponentDef(
    type_id='D',
    display_name='Diode',
    ref_prefix='D',
    pin_offsets={1: PinOffset(0), 2: PinOffset(4)},
    pin_names={1: 'K', 2: 'A'},   # KiCad Device:D convention: pin 1=K, pin 2=A
    color='#222222',   # black body like 1N4001
)

ZENER = ComponentDef(
    type_id='D_Zener',
    display_name='Zener Diode',
    ref_prefix='D',
    pin_offsets={1: PinOffset(0), 2: PinOffset(4)},
    pin_names={1: 'K', 2: 'A'},   # KiCad Device:D_Zener convention: pin 1=K, pin 2=A
    color='#1a1a2a',   # near-black, slightly blue-tinted
)

# ---------------------------------------------------------------------------
# LED (5mm round package, 2-hole pin span)
# Pin 1 = Anode (A), Pin 2 = Cathode (K, shorter lead / flat side)
# ---------------------------------------------------------------------------

LED = ComponentDef(
    type_id='LED',
    display_name='LED (5mm)',
    ref_prefix='D',
    pin_offsets={1: PinOffset(0), 2: PinOffset(2)},
    pin_names={1: 'K', 2: 'A'},   # KiCad Device:LED convention: pin 1=K, pin 2=A
    color='#e84040',   # default red; actual colour unknown from netlist
)

# Ordered colour palette for the LED colour-cycle button in the tray.
# Each entry is (hex_color, display_name).  The first entry must match LED.color.
LED_COLORS: list = [
    ('#e84040', 'Red'),
    ('#40c840', 'Green'),
    ('#e8a020', 'Yellow'),
    ('#4040e8', 'Blue'),
]

# ---------------------------------------------------------------------------
# Potentiometer (3-pin, 3 consecutive holes)
# Pin 1 = CCW terminal, Pin 2 = Wiper, Pin 3 = CW terminal
# ---------------------------------------------------------------------------

POTENTIOMETER = ComponentDef(
    type_id='POT',
    display_name='Potentiometer',
    ref_prefix='RV',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: '1', 2: 'W', 3: '3'},
    color='#2255bb',
)

# ---------------------------------------------------------------------------
# TO-92 transistors (3 consecutive holes in the same bank/row)
#
# Physical pin order (flat face toward viewer, left to right):
#   NPN/PNP generic (e.g. BC547/BC557): C – B – E
#   JFET N-ch (e.g. 2N5457):            D – G – S   (varies; use BF245: S – G – D)
#   BS170 MOSFET:                        S – G – D
#
# KiCad schematic pin names and the breadboard pin offsets must agree so that
# the validator can match nets to holes.
# ---------------------------------------------------------------------------

NPN_BJT = ComponentDef(
    type_id='NPN',
    display_name='NPN BJT',
    ref_prefix='Q',
    # Physical: C(pin1)–B(pin2)–E(pin3), left to right
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'C', 2: 'B', 3: 'E'},
    color='#404040',
)

PNP_BJT = ComponentDef(
    type_id='PNP',
    display_name='PNP BJT',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'C', 2: 'B', 3: 'E'},
    color='#404040',
)

JFET_N = ComponentDef(
    type_id='JFET_N',
    display_name='N-ch JFET',
    ref_prefix='Q',
    # Physical (BF245 / 2N5457): S(pin1)–G(pin2)–D(pin3)
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#404040',
)

JFET_P = ComponentDef(
    type_id='JFET_P',
    display_name='P-ch JFET',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#404040',
)

BS170 = ComponentDef(
    type_id='BS170',
    display_name='BS170 MOSFET',
    ref_prefix='Q',
    # Physical (BS170 TO-92): S(pin1)–G(pin2)–D(pin3)
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#404040',
)

# Generic N-channel MOSFET (any symbol containing NMOS/MOSFET that isn't BS170)
# Matches KiCad Transistor_FET convention for modern parts (2N7002, AO3400…): pin 1=G, 2=S, 3=D
NMOS = ComponentDef(
    type_id='NMOS',
    display_name='N-ch MOSFET',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'G', 2: 'S', 3: 'D'},
    color='#404040',
)

# Generic P-channel MOSFET (AO3401A, BS250…): pin 1=G, 2=S, 3=D
PMOS = ComponentDef(
    type_id='PMOS',
    display_name='P-ch MOSFET',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'G', 2: 'S', 3: 'D'},
    color='#404040',
)

# ---------------------------------------------------------------------------
# TO-92 pinout variants.
#
# Different real-world parts share the same schematic symbol but have a
# different physical leg order (flat face toward viewer, left → right).
# Each entry is (display_label, {schematic_pin_num: PinOffset}).
# The first entry is the default and must match the ComponentDef above.
# BS170 has a single standard pinout and is omitted.
# ---------------------------------------------------------------------------
TO92_PINOUT_VARIANTS: Dict[str, List[Tuple[str, Dict[int, PinOffset]]]] = {
    'NPN': [
        ('C-B-E', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # BC547, BC337 …
        ('E-B-C', {3: PinOffset(0), 2: PinOffset(1), 1: PinOffset(2)}),  # 2N3904, 2N2222 …
    ],
    'PNP': [
        ('C-B-E', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # BC557, BC327 …
        ('E-B-C', {3: PinOffset(0), 2: PinOffset(1), 1: PinOffset(2)}),  # 2N3906 …
    ],
    'JFET_N': [
        ('S-G-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # BF245, 2N5459 …
        ('D-G-S', {3: PinOffset(0), 2: PinOffset(1), 1: PinOffset(2)}),  # 2N5457 …
        ('G-S-D', {2: PinOffset(0), 1: PinOffset(1), 3: PinOffset(2)}),
        ('G-D-S', {2: PinOffset(0), 3: PinOffset(1), 1: PinOffset(2)}),
    ],
    'JFET_P': [
        ('S-G-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),
        ('D-G-S', {3: PinOffset(0), 2: PinOffset(1), 1: PinOffset(2)}),
        ('G-S-D', {2: PinOffset(0), 1: PinOffset(1), 3: PinOffset(2)}),
        ('G-D-S', {2: PinOffset(0), 3: PinOffset(1), 1: PinOffset(2)}),
    ],
    # MOSFET pinouts: pin 1=G, 2=S, 3=D (KiCad Transistor_FET convention: 2N7002, AO3401A…)
    'NMOS': [
        ('G-S-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # 2N7002 …
        ('G-D-S', {1: PinOffset(0), 3: PinOffset(1), 2: PinOffset(2)}),
        ('S-G-D', {2: PinOffset(0), 1: PinOffset(1), 3: PinOffset(2)}),
        ('D-G-S', {3: PinOffset(0), 1: PinOffset(1), 2: PinOffset(2)}),
    ],
    'PMOS': [
        ('G-S-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # AO3401A …
        ('G-D-S', {1: PinOffset(0), 3: PinOffset(1), 2: PinOffset(2)}),
        ('S-G-D', {2: PinOffset(0), 1: PinOffset(1), 3: PinOffset(2)}),
        ('D-G-S', {3: PinOffset(0), 1: PinOffset(1), 2: PinOffset(2)}),
    ],
}

# ---------------------------------------------------------------------------
# DIP op-amps — anchor always in row 'e', bottom side in row 'f'
#
# 8-DIP pin layout (counterclockwise from notch, viewed from top):
#   Pins 1–4 along the bottom side (row f, cols anchor … anchor+3)
#   Pins 5–8 along the top side    (row e, cols anchor+3 … anchor)
#
#   This matches the physical lab convention: pin 1 is at the lower-left when
#   the board is viewed with top rails at the top of the screen.
#
# 14-DIP:
#   Pins 1–7  bottom side (row f, cols anchor … anchor+6)
#   Pins 8–14 top side    (row e, cols anchor+6 … anchor)
# ---------------------------------------------------------------------------

def _dip_offsets(n: int) -> Dict[int, PinOffset]:
    """Generic DIP offset generator for any even pin count n.
    Pins 1..n/2 → bottom side (row f, cross_gap=True),  col = pin-1.
    Pins n/2+1..n → top side   (row e, cross_gap=False), col = n-pin.
    """
    half = n // 2
    bot = {i + 1:        PinOffset(i,            cross_gap=True)  for i in range(half)}
    top = {i + half + 1: PinOffset(half - 1 - i, cross_gap=False) for i in range(half)}
    return {**bot, **top}

def _dip8_offsets()  -> Dict[int, PinOffset]: return _dip_offsets(8)
def _dip14_offsets() -> Dict[int, PinOffset]: return _dip_offsets(14)


def _make_dip(n: int) -> 'ComponentDef':
    """Create a generic n-pin DIP ComponentDef. Used for unrecognised ICs."""
    return ComponentDef(
        type_id=f'DIP{n}',
        display_name=f'{n}-pin DIP IC',
        ref_prefix='U',
        pin_offsets=_dip_offsets(n),
        pin_names={i: str(i) for i in range(1, n + 1)},
        color='#1a1a2e',
        is_dip=True,
    )

# TL081 — single op-amp, 8-DIP
# Pinout: 1=Offset_N1, 2=IN-, 3=IN+, 4=V-, 5=Offset_N2, 6=OUT, 7=V+, 8=NC
TL081 = ComponentDef(
    type_id='TL081',
    display_name='TL081 (single op-amp)',
    ref_prefix='U',
    pin_offsets=_dip8_offsets(),
    pin_names={
        1: 'N1', 2: 'IN-', 3: 'IN+', 4: 'V-',
        5: 'N2', 6: 'OUT', 7: 'V+', 8: 'NC',
    },
    color='#303080',
    is_dip=True,
)

# RC4558 — dual op-amp, 8-DIP
# Pinout: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V-, 5=IN+_B, 6=IN-_B, 7=OUT_B, 8=V+
RC4558 = ComponentDef(
    type_id='RC4558',
    display_name='RC4558 (dual op-amp)',
    ref_prefix='U',
    pin_offsets=_dip8_offsets(),
    pin_names={
        1: 'OUT_A', 2: 'IN-_A', 3: 'IN+_A', 4: 'V-',
        5: 'IN+_B', 6: 'IN-_B', 7: 'OUT_B', 8: 'V+',
    },
    color='#303080',
    is_dip=True,
)

# TL084 — quad op-amp, 14-DIP
# Pinout: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V+, 5=IN+_B, 6=IN-_B, 7=OUT_B,
#         8=OUT_C, 9=IN-_C, 10=IN+_C, 11=V-, 12=IN+_D, 13=IN-_D, 14=OUT_D
TL084 = ComponentDef(
    type_id='TL084',
    display_name='TL084 (quad op-amp)',
    ref_prefix='U',
    pin_offsets=_dip14_offsets(),
    pin_names={
        1: 'OUT_A', 2: 'IN-_A', 3: 'IN+_A', 4:  'V+',
        5: 'IN+_B', 6: 'IN-_B', 7: 'OUT_B',
        8: 'OUT_C', 9: 'IN-_C', 10: 'IN+_C', 11: 'V-',
        12: 'IN+_D', 13: 'IN-_D', 14: 'OUT_D',
    },
    color='#303080',
    is_dip=True,
)

# OPAMP_SPICE — KiCad Simulation_SPICE:OPAMP (kicad_builtin_opamp)
# Logical 5-pin opamp symbol: 1=IN+, 2=IN-, 3=V+, 4=V-, 5=OUT.
# Rendered as a DIP-6 (3-column) body so students can tell it is a simulation
# model rather than a real IC.  One dummy pin (6) fills the remaining leg.
# Pin layout (standard DIP counterclockwise from pin 1):
#   Bottom (cross_gap=True):  1=IN+, 2=IN-, 3=V+   (cols 0-2)
#   Top    (cross_gap=False): 4=V-,  5=OUT, 6=NC   (cols 2-0)
OPAMP_SPICE = ComponentDef(
    type_id='OPAMP_SPICE',
    display_name='OPAMP (SPICE)',
    ref_prefix='U',
    pin_offsets={
        1: PinOffset(0, cross_gap=True),   # IN+ → row f, col+0
        2: PinOffset(1, cross_gap=True),   # IN- → row f, col+1
        3: PinOffset(2, cross_gap=True),   # V+  → row f, col+2
        4: PinOffset(2, cross_gap=False),  # V-  → row e, col+2
        5: PinOffset(1, cross_gap=False),  # OUT → row e, col+1
        6: PinOffset(0, cross_gap=False),  # NC  → row e, col+0  (dummy leg)
    },
    pin_names={
        1: 'IN+', 2: 'IN-', 3: 'V+', 4: 'V-', 5: 'OUT',
    },
    color='#303080',
    is_dip=True,
)

# ---------------------------------------------------------------------------
# Board modules — DIP-style (straddle center gap), rendered as PCB boards.
#
# Pin layout convention (same as DIP):
#   Pins 1..N/2  → left/top side,  cross_gap=False (row e), col_delta = pin-1
#   Pins N/2+1..N → right/bottom side, cross_gap=True  (row f), col_delta = N-pin
#
# Arduino Nano (KiCad Module.pretty/Arduino_Nano.kicad_mod):
#   30 pins, 15 per side, 2.54mm pitch → 15 breadboard columns.
#   Physical width: 15.24mm (6×2.54mm) between the two header rows.
#
# Pin names: standard Arduino Nano pinout, USB-end = pin 1 side.
# Left  (1-15): TX, RX, RST, GND, D2, D3, D4, D5, D6, D7, D8, D9, D10, D11, D12
# Right (30→16, col 0→14): VIN, GND, RST, 5V, A7, A6, A5, A4, A3, A2, A1, A0, AREF, 3V3, D13
# ---------------------------------------------------------------------------

def _nano_offsets() -> Dict[int, PinOffset]:
    left  = {n:      PinOffset(n - 1,      cross_gap=False) for n in range(1,  16)}
    right = {n:      PinOffset(30 - n,     cross_gap=True)  for n in range(16, 31)}
    return {**left, **right}

ARDUINO_NANO = ComponentDef(
    type_id='Arduino_Nano',
    display_name='Arduino Nano',
    ref_prefix='MCU',
    pin_offsets=_nano_offsets(),
    pin_names={
        # Left side (USB end → far end), pins 1-15
        1:  'TX',   2:  'RX',   3:  'RST',  4:  'GND',  5:  'D2',
        6:  'D3',   7:  'D4',   8:  'D5',   9:  'D6',   10: 'D7',
        11: 'D8',   12: 'D9',   13: 'D10',  14: 'D11',  15: 'D12',
        # Right side (USB end → far end), pins 30→16 (col 0→14)
        30: 'VIN',  29: 'GND',  28: 'RST',  27: '5V',   26: 'A7',
        25: 'A6',   24: 'A5',   23: 'A4',   22: 'A3',   21: 'A2',
        20: 'A1',   19: 'A0',   18: 'AREF', 17: '3V3',  16: 'D13',
    },
    color='#1a3a8f',   # Arduino blue
    is_dip=False,
    is_module=True,
)

# ---------------------------------------------------------------------------
# Arduino Uno R3 (KiCad MCU_Module:Arduino_UNO_R3)
#   32 pins.  Two sides of unequal length:
#     Digital side  (18 pins): D0/RX … D7, D8 … D13, GND, AREF, SDA, SCL
#     Power/Analog  (14 pins): SCL/A5 … A0, VIN, GND, GND, 5V, 3V3, RST, IOREF, NC
#
#   Represented as 18 columns.  The power/analog side has no legs at cols 6-9,
#   which corresponds to the physical gap between the analog header and the power
#   header on the board.  The row is reversed (SCL/A5 at col 0) so that SCL/A5
#   lines up with D0/RX across the board centre gap.
#
#   KiCad pin numbers preserved verbatim so validation works against the netlist.
#   Digital side → cross_gap=False (row e); Power/Analog → cross_gap=True (row f).
# ---------------------------------------------------------------------------

ARDUINO_UNO = ComponentDef(
    type_id='Arduino_Uno',
    display_name='Arduino Uno R3',
    ref_prefix='MCU',
    pin_offsets={
        # --- Digital side (cross_gap=False, cols 0-17) ---
        # Col 0 = USB/BJ end; pins read from USB side inward.
        # Physical order from USB end: SCL, SDA, AREF, GND, D13-D8,
        #   [notch between col 9 and 10], D7-D2, TX, RX (D0 at col 17).
        32: PinOffset(0,  cross_gap=False),   # SCL  (nearest USB end)
        31: PinOffset(1,  cross_gap=False),   # SDA
        30: PinOffset(2,  cross_gap=False),   # AREF
        29: PinOffset(3,  cross_gap=False),   # GND
        28: PinOffset(4,  cross_gap=False),   # D13
        27: PinOffset(5,  cross_gap=False),   # D12
        26: PinOffset(6,  cross_gap=False),   # D11
        25: PinOffset(7,  cross_gap=False),   # D10
        24: PinOffset(8,  cross_gap=False),   # D9
        23: PinOffset(9,  cross_gap=False),   # D8
        22: PinOffset(10, cross_gap=False),   # D7
        21: PinOffset(11, cross_gap=False),   # D6
        20: PinOffset(12, cross_gap=False),   # D5
        19: PinOffset(13, cross_gap=False),   # D4
        18: PinOffset(14, cross_gap=False),   # D3
        17: PinOffset(15, cross_gap=False),   # D2
        16: PinOffset(16, cross_gap=False),   # D1/TX
        15: PinOffset(17, cross_gap=False),   # D0/RX (farthest from USB end)
        # --- Power/Analog side (cross_gap=True, cols 3-10 + 12-17) ---
        # Inner row starts at col 3 (NC) — the leftmost 3 cols are left empty
        # so the header strip does not visually overlap the USB/BJ connectors.
        # Gap at col 11 (between VIN at col 10 and A0 at col 12).
        # Analog pins (A0-A5) are on the RIGHT side; power pins toward the left.
        1:  PinOffset(3,  cross_gap=True),    # NC
        2:  PinOffset(4,  cross_gap=True),    # IOREF
        3:  PinOffset(5,  cross_gap=True),    # RST
        4:  PinOffset(6,  cross_gap=True),    # 3V3
        5:  PinOffset(7,  cross_gap=True),    # 5V
        6:  PinOffset(8,  cross_gap=True),    # GND
        7:  PinOffset(9,  cross_gap=True),    # GND
        8:  PinOffset(10, cross_gap=True),    # VIN
        9:  PinOffset(12, cross_gap=True),    # A0
        10: PinOffset(13, cross_gap=True),    # A1
        11: PinOffset(14, cross_gap=True),    # A2
        12: PinOffset(15, cross_gap=True),    # A3
        13: PinOffset(16, cross_gap=True),    # SDA/A4
        14: PinOffset(17, cross_gap=True),    # SCL/A5
    },
    pin_names={
        1:  'NC',      2:  'IOREF',  3:  'RST',   4:  '3V3',   5:  '5V',
        6:  'GND',     7:  'GND',    8:  'VIN',
        9:  'A0',      10: 'A1',     11: 'A2',     12: 'A3',
        13: 'A4',      14: 'A5',
        15: 'RX',      16: 'TX',     17: 'D2',     18: 'D3',    19: 'D4',
        20: 'D5',      21: 'D6',     22: 'D7',     23: 'D8',    24: 'D9',
        25: 'D10',     26: 'D11',    27: 'D12',    28: 'D13',
        29: 'GND',     30: 'AREF',   31: 'SDA',    32: 'SCL',
    },
    color='#1a3a8f',   # Arduino blue
    is_dip=False,
    is_module=True,
)

# Extended pin-function labels for the Arduino Uno R3, shown when the
# "Pin functions" toggle is active.  Only pins with notable alternate
# functions differ from the basic pin_names above.
ARDUINO_UNO_FN_NAMES: Dict[int, str] = {
    1:  'NC',       2:  'IOREF',  3:  'RST',   4:  '3V3',   5:  '5V',
    6:  'GND',      7:  'GND',    8:  'VIN',
    9:  'A0',       10: 'A1',     11: 'A2',     12: 'A3',
    13: 'A4/SDA',   14: 'A5/SCL',                           # I2C on analog pins
    15: 'RX',       16: 'TX',
    17: 'D2/INT0',  18: 'D3~/INT1', 19: 'D4',
    20: 'D5~',      21: 'D6~',    22: 'D7',     23: 'D8',
    24: 'D9~',      25: 'D10~/SS', 26: 'D11~/MOSI', 27: 'D12/MISO', 28: 'D13/SCK',
    29: 'GND',      30: 'AREF',   31: 'SDA',    32: 'SCL',
}

# ---------------------------------------------------------------------------
# Teensy 4.1
#   48 pins, 24 per side, 2.54mm pitch → 24 breadboard columns.
#   USB end = pin 1 side.  Green PCB board.
#   Not in KiCad standard library.
#
# Left  (1-24): 0,1,2,3,4,5,6,7,8,9,10,11,12,3V3,GND,A10,A11,A12,A13,28,29,30,31,32
# Right (48→25): VIN,GND,3V3,A9,A8,A7,A6,A5,A4,A3,A2,A1,A0,13,AGND,38,37,36,35,34,33,41,40,39
# ---------------------------------------------------------------------------

def _teensy41_offsets() -> Dict[int, PinOffset]:
    left  = {n: PinOffset(n - 1,      cross_gap=False) for n in range(1,  25)}
    right = {n: PinOffset(48 - n,     cross_gap=True)  for n in range(25, 49)}
    return {**left, **right}

TEENSY_41 = ComponentDef(
    type_id='Teensy_41',
    display_name='Teensy 4.1',
    ref_prefix='MCU',
    pin_offsets=_teensy41_offsets(),
    pin_names={
        # Left side (USB end → far end), pins 1-24
        1:  '0',    2:  '1',    3:  '2',    4:  '3',    5:  '4',
        6:  '5',    7:  '6',    8:  '7',    9:  '8',    10: '9',
        11: '10',   12: '11',   13: '12',   14: '3V3',  15: 'GND',
        16: 'A10',  17: 'A11',  18: 'A12',  19: 'A13',  20: '28',
        21: '29',   22: '30',   23: '31',   24: '32',
        # Right side (USB end → far end), pins 48→25 (col 0→23)
        48: 'VIN',  47: 'GND',  46: '3V3',  45: 'A9',   44: 'A8',
        43: 'A7',   42: 'A6',   41: 'A5',   40: 'A4',   39: 'A3',
        38: 'A2',   37: 'A1',   36: 'A0',   35: '13',   34: 'AGND',
        33: '38',   32: '37',   31: '36',   30: '35',   29: '34',
        28: '33',   27: '41',   26: '40',   25: '39',
    },
    color='#1a6b2e',   # Teensy green
    is_dip=False,
    is_module=True,
)

# ---------------------------------------------------------------------------
# Raspberry Pi Boards
# ---------------------------------------------------------------------------

# --- Full-Size Raspberry Pi (40-pin GPIO header) ---
# Even pins (2,4,6,…,40): col_delta=(pin-2)//2, cross_gap=False (outer/edge row)
# Odd pins  (1,3,5,…,39): col_delta=(pin-1)//2, cross_gap=True  (inner row)
def _rpi_gpio_offsets() -> Dict[int, PinOffset]:
    odd  = {n: PinOffset((n - 1) // 2, cross_gap=True)  for n in range(1, 41, 2)}
    even = {n: PinOffset((n - 2) // 2, cross_gap=False) for n in range(2, 41, 2)}
    return {**odd, **even}

RASPBERRY_PI_4 = ComponentDef(
    type_id='Raspberry_Pi_4',
    display_name='Raspberry Pi',
    ref_prefix='MCU',
    pin_offsets=_rpi_gpio_offsets(),
    pin_names={
        1:  '3V3',  2:  '5V',   3:  'G2',   4:  '5V',   5:  'G3',   6:  'GND',
        7:  'G4',   8:  'G14',  9:  'GND',  10: 'G15',  11: 'G17',  12: 'G18',
        13: 'G27',  14: 'GND',  15: 'G22',  16: 'G23',  17: '3V3',  18: 'G24',
        19: 'G10',  20: 'GND',  21: 'G9',   22: 'G25',  23: 'G11',  24: 'G8',
        25: 'GND',  26: 'G7',   27: 'G0',   28: 'G1',   29: 'G5',   30: 'GND',
        31: 'G6',   32: 'G12',  33: 'G13',  34: 'GND',  35: 'G19',  36: 'G16',
        37: 'G26',  38: 'G20',  39: 'GND',  40: 'G21',
    },
    color='#388e3c',
    is_dip=False,
    is_module=True,
)

# --- Raspberry Pi Pico (40-pin DIP layout) ---
def _rpi_pico_offsets() -> Dict[int, PinOffset]:
    # Landscape layout (USB on the left).
    # Top row = Right edge of Pico = Pins 40..21 (Pin 40 is Top-Left anchor)
    # Bottom row = Left edge of Pico = Pins 1..20 (Pin 1 is Bottom-Left)
    top = {40 - n: PinOffset(n, cross_gap=False) for n in range(20)}
    bot = {n + 1:  PinOffset(n, cross_gap=True)  for n in range(20)}
    return {**top, **bot}

RASPBERRY_PI_PICO = ComponentDef(
    type_id='RPi_Pico',
    display_name='Raspberry Pi Pico',
    ref_prefix='MCU',
    pin_offsets=_rpi_pico_offsets(),
    pin_names={
        1: 'GP0', 2: 'GP1', 3: 'GND', 4: 'GP2', 5: 'GP3', 6: 'GP4', 7: 'GP5', 8: 'GND',
        9: 'GP6', 10: 'GP7', 11: 'GP8', 12: 'GP9', 13: 'GND', 14: 'GP10', 15: 'GP11',
        16: 'GP12', 17: 'GP13', 18: 'GND', 19: 'GP14', 20: 'GP15',
        40: 'VBUS', 39: 'VSYS', 38: 'GND', 37: '3V3_EN', 36: '3V3(OUT)', 35: 'ADC_VREF',
        34: 'GP28', 33: 'GND', 32: 'GP27', 31: 'GP26', 30: 'RUN', 29: 'GP22', 28: 'GND',
        27: 'GP21', 26: 'GP20', 25: 'GP19', 24: 'GP18', 23: 'GND', 22: 'GP17', 21: 'GP16'
    },
    color='#388e3c',
    is_dip=False,
    is_module=True,
)

# Alternative pin labels showing primary alternate functions (SDA, SCL, CE0…)
RPi_PIN_NAMES_LONG = {
    1:  '3V3',    2:  '5V',
    3:  'SDA1',   4:  '5V',
    5:  'SCL1',   6:  'GND',
    7:  'GPCLK0', 8:  'TXD0',
    9:  'GND',    10: 'RXD0',
    11: 'G17',    12: 'PCM',
    13: 'G27',    14: 'GND',
    15: 'G22',    16: 'G23',
    17: '3V3',    18: 'G24',
    19: 'MOSI0',  20: 'GND',
    21: 'MISO0',  22: 'G25',
    23: 'SCLK0',  24: 'CE0',
    25: 'GND',    26: 'CE1',
    27: 'ID_SD',  28: 'ID_SC',
    29: 'G5',     30: 'GND',
    31: 'G6',     32: 'PWM0',
    33: 'PWM1',   34: 'GND',
    35: 'MISO1',  36: 'CE0_1',
    37: 'G26',    38: 'MOSI1',
    39: 'GND',    40: 'SCLK1',
}

# ---------------------------------------------------------------------------
# Switches
#
# SPST  — momentary push button (2-pin, 4-col span)
# SPDT  — slide/toggle switch   (3-pin: A–COM–B in a row)
# SP3T  — 3-position slide      (4-pin: A–COM–B–C in a row)
#
# KiCad symbol names that map here (see guess_type_id):
#   Switch:SW_Push, SW_SPST, SW_SPDT, SW_SP3T
# ---------------------------------------------------------------------------

SWITCH_SPST = ComponentDef(
    type_id='SPST',
    display_name='Push Button (SPST)',
    ref_prefix='SW',
    pin_offsets={1: PinOffset(0), 2: PinOffset(4)},
    pin_names={1: 'A', 2: 'B'},
    color='#a8a8a8',   # light grey housing
    symmetric=True,
)

SWITCH_SPDT = ComponentDef(
    type_id='SPDT',
    display_name='Slider Switch (SPDT)',
    ref_prefix='SW',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'A', 2: 'COM', 3: 'B'},
    color='#7b5c3a',   # brown housing
)

SWITCH_SP3T = ComponentDef(
    type_id='SP3T',
    display_name='Slider Switch (SP3T)',
    ref_prefix='SW',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2), 4: PinOffset(3)},
    pin_names={1: 'A', 2: 'COM', 3: 'B', 4: 'C'},
    color='#7b5c3a',   # brown housing
)

# ---------------------------------------------------------------------------
# Registry: map type_id → ComponentDef
# Also provides heuristic lookup from KiCad symbol/value strings.
# ---------------------------------------------------------------------------

ALL_DEFS: Dict[str, ComponentDef] = {
    d.type_id: d for d in [
        RESISTOR, CAPACITOR, CAPACITOR_ELECTROLYTIC, INDUCTOR,
        DIODE, ZENER, LED,
        POTENTIOMETER,
        NPN_BJT, PNP_BJT, JFET_N, JFET_P, BS170, NMOS, PMOS,
        TL081, RC4558, TL084, OPAMP_SPICE,
        ARDUINO_NANO, ARDUINO_UNO,
        TEENSY_41,
        RASPBERRY_PI_4, RASPBERRY_PI_PICO,
        SWITCH_SPST, SWITCH_SPDT, SWITCH_SP3T,
    ]
}

# Pre-register generic DIP sizes for all common IC packages.
# Any even pin count not listed here is generated on demand in guess_type_id.
for _n in [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 40]:
    _d = _make_dip(_n)
    ALL_DEFS[_d.type_id] = _d


def guess_type_id(ref: str, value: str, symbol: str, lib: str = '',
                  description: str = '', pin_count: int = 0) -> Optional[str]:
    """
    Heuristically map a KiCad component to a ComponentDef type_id.

    ref         : schematic reference, e.g. 'R1', 'Q3', 'U1'
    value       : component value, e.g. '10k', 'BC547', 'TL081'
    symbol      : KiCad symbol name from the netlist libsource, e.g. 'R', 'NPN', 'TL081'
    lib         : KiCad library name from the netlist libsource, e.g. 'Device', 'Simulation_SPICE'
    description : libsource description, e.g. '0.1A Id, 60V Vds, N-Channel MOSFET, TO-92'
    """
    v = value.upper()
    s = symbol.upper()
    l = lib.upper()
    d = description.upper()

    # Board modules — all KiCad variants map to a single standard layout.
    # Arduino Uno R3: 32-pin symbol; symbol name contains "UNO".
    # Arduino Nano + variants: everything else with "ARDUINO" in name.
    # Catches: Arduino_Nano_v2.x, Arduino_Nano_v3.x, Arduino_Nano_Every,
    #          Arduino_Nano_ESP32, Arduino_Nano_RP2040_Connect, etc.
    if 'ARDUINO' in v or 'ARDUINO' in s:
        if 'UNO' in s or 'UNO' in v:
            return 'Arduino_Uno'
        return 'Arduino_Nano'
    if 'TEENSY' in v or 'TEENSY' in s:
        return 'Teensy_41'
    # Full-size Raspberry Pi (40-pin GPIO header, BCM numbering).
    # Catches: Raspberry_Pi_4B, Raspberry_Pi_3B, Raspberry_Pi_Zero, etc.
    # Must be checked before the Pico branch so that 'RASPBERRY_PI' in a
    # full-size Pi symbol name doesn't fall through to the Pico type_id.
    _rpi_hit = 'RASPBERRYPI' in v or 'RASPBERRYPI' in s or 'RASPBERRY_PI' in v or 'RASPBERRY_PI' in s
    _pico_hit = ('PICO' in v or 'PICO' in s
                 or 'RP2040' in v or 'RP2040' in s
                 or 'RP2350' in v or 'RP2350' in s
                 or 'RP2354' in v or 'RP2354' in s)
    if _rpi_hit and not _pico_hit:
        return 'Raspberry_Pi_4'
    # RPi Pico / RP2040 / RP2350 variants.
    # Catches: RaspberryPi_Pico, RaspberryPi_Pico_W, RaspberryPi_Pico_Debug,
    #          RaspberryPi_Pico_Extensive, RP2040, RP2350A/B, RP2354A/B.
    if _pico_hit or _rpi_hit:
        return 'RPi_Pico'

    # Exact value/symbol matches first
    for key in ('TL084', 'RC4558', 'TL081', 'BS170'):
        if key in v or key in s:
            return key

    # Potentiometer — must precede transistor checks (Simulation_SPICE:Potentiometer
    # has ref prefix 'R' and would otherwise be misidentified as a resistor)
    if 'POTENTIOMETER' in v or 'POTENTIOMETER' in s:
        return 'POT'

    # Transistor types from symbol library name
    if 'NPN' in s:
        return 'NPN'
    if 'PNP' in s:
        return 'PNP'
    if 'PJFE' in s or ('JFET' in s and 'P' in s):
        return 'JFET_P'
    if 'JFET' in s or 'NJFE' in s:
        return 'JFET_N'
    if 'PMOS' in s or ('MOSFET' in s and 'P' in s):
        return 'PMOS'
    if 'NMOS' in s or 'MOSFET' in s:
        return 'NMOS'

    # Switch symbols — must precede prefix fallback (SW_ prefix would default to SPST)
    if 'SW_PUSH' in s or 'PUSHBUTTON' in s or 'TACTILE' in s or 'TACT' in s:
        return 'SPST'
    if 'SW_SP3T' in s or 'SP3T' in s:
        return 'SP3T'
    if 'SW_SPDT' in s or 'SPDT' in s:
        return 'SPDT'
    if 'SW_SPST' in s or ('SW' in s and 'SPST' in v):
        return 'SPST'

    # Reference prefix fallback
    prefix = ''.join(c for c in ref if c.isalpha()).upper()
    if prefix in ('SW', 'S'):
        if '3T' in s or 'SP3T' in v:
            return 'SP3T'
        if 'DT' in s or 'SPDT' in v:
            return 'SPDT'
        return 'SPST'
    if prefix in ('MCU', 'A'):
        return 'RPi_Pico'
    if prefix == 'R':
        return 'C_POL' if ('POL' in s or 'ELEC' in v) else 'R'
    if prefix == 'C':
        return 'C_POL' if ('+' in value or 'POL' in s or 'ELEC' in v) else 'C'
    if prefix == 'L':
        return 'L'
    if prefix in ('RV', 'POT'):
        return 'POT'
    if prefix in ('D', 'LED'):
        if 'LED' in v or 'LED' in s or prefix == 'LED':
            return 'LED'
        if 'ZENER' in s or 'ZEN' in s or 'BZX' in v or 'BZY' in v or 'BZT' in v:
            return 'D_Zener'
        return 'D'
    if prefix == 'U':
        # Quad op-amps (14-DIP)
        for key in ('LM324', 'LM348', 'TL074', 'LM4136'):
            if key in v or key in s:
                return 'TL084'
        # Dual op-amps (8-DIP)
        for key in ('LM358', 'NE5532', 'TL072', 'MC1458', '4558'):
            if key in v or key in s:
                return 'RC4558'
        # Single op-amps and generic op-amp symbols (8-DIP)
        for key in ('TL071', 'LM741', 'UA741', 'LM747', 'AD711'):
            if key in v or key in s:
                return 'TL081'
        if 'OPAMP' in s or 'OP_AMP' in s or 'AMPLIFIER' in s:
            # Simulation_SPICE:OPAMP uses logical pin numbers (1=IN+,2=IN-,3=V+,4=V-,5=OUT)
            # rather than physical DIP-8 numbers — needs its own mapping.
            if 'SPICE' in l or 'SIMULATION' in l:
                return 'OPAMP_SPICE'
            return 'TL081'
        # Generic DIP fallback: any even-pin-count U-prefix IC gets a standard DIP layout.
        if pin_count >= 4 and pin_count % 2 == 0:
            type_id = f'DIP{pin_count}'
            if type_id not in ALL_DEFS:
                ALL_DEFS[type_id] = _make_dip(pin_count)
            return type_id

    # Description-based fallback: works for Transistor_BJT / Transistor_FET library
    # parts whose symbol name is the part number (2N2219, BC807, 2N7002, AO3401A…).
    if 'NPN' in d and 'TRANSISTOR' in d:
        return 'NPN'
    if 'PNP' in d and 'TRANSISTOR' in d:
        return 'PNP'
    if 'P-CHANNEL' in d and 'MOSFET' in d:
        return 'PMOS'
    if ('N-CHANNEL' in d or 'N-CH' in d) and 'MOSFET' in d:
        return 'NMOS'

    return None
