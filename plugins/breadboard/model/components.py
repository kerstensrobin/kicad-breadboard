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
    ],
    'JFET_P': [
        ('S-G-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),
        ('D-G-S', {3: PinOffset(0), 2: PinOffset(1), 1: PinOffset(2)}),
    ],
    # MOSFET pinouts: pin 1=G, 2=S, 3=D (KiCad Transistor_FET convention: 2N7002, AO3401A…)
    'NMOS': [
        ('G-S-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # 2N7002 …
        ('S-G-D', {2: PinOffset(0), 1: PinOffset(1), 3: PinOffset(2)}),
        ('D-G-S', {3: PinOffset(0), 1: PinOffset(1), 2: PinOffset(2)}),
    ],
    'PMOS': [
        ('G-S-D', {1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)}),  # AO3401A …
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

def _dip8_offsets() -> Dict[int, PinOffset]:
    bot = {i + 1: PinOffset(i,     cross_gap=True)  for i in range(4)}    # pins 1-4, row f
    top = {i + 5: PinOffset(3 - i, cross_gap=False) for i in range(4)}    # pins 5-8, row e
    return {**bot, **top}

def _dip14_offsets() -> Dict[int, PinOffset]:
    bot = {i + 1: PinOffset(i,     cross_gap=True)  for i in range(7)}    # pins 1-7,  row f
    top = {i + 8: PinOffset(6 - i, cross_gap=False) for i in range(7)}    # pins 8-14, row e
    return {**bot, **top}

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
# Pins are placed at the standard DIP-8 positions matching their pin numbers.
# Dummy pins 6-8 fill the remaining DIP-8 leg positions so the body draws
# at the correct width and flipping works correctly.
OPAMP_SPICE = ComponentDef(
    type_id='OPAMP_SPICE',
    display_name='OPAMP (SPICE)',
    ref_prefix='U',
    pin_offsets={
        1: PinOffset(0, cross_gap=True),   # IN+  → row f, col+0  (DIP-8 pos 1)
        2: PinOffset(1, cross_gap=True),   # IN-  → row f, col+1  (DIP-8 pos 2)
        3: PinOffset(2, cross_gap=True),   # V+   → row f, col+2  (DIP-8 pos 3)
        4: PinOffset(3, cross_gap=True),   # V-   → row f, col+3  (DIP-8 pos 4)
        5: PinOffset(3, cross_gap=False),  # OUT  → row e, col+3  (DIP-8 pos 5)
        # Unused legs — keep DIP-8 body width and flip calculation correct
        6: PinOffset(2, cross_gap=False),  # NC   → row e, col+2  (DIP-8 pos 6)
        7: PinOffset(1, cross_gap=False),  # NC   → row e, col+1  (DIP-8 pos 7)
        8: PinOffset(0, cross_gap=False),  # NC   → row e, col+0  (DIP-8 pos 8)
    },
    pin_names={
        1: 'IN+', 2: 'IN-', 3: 'V+', 4: 'V-', 5: 'OUT',
    },
    color='#303080',
    is_dip=True,
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
    ]
}


def guess_type_id(ref: str, value: str, symbol: str, lib: str = '',
                  description: str = '') -> Optional[str]:
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

    # Reference prefix fallback
    prefix = ''.join(c for c in ref if c.isalpha()).upper()
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
