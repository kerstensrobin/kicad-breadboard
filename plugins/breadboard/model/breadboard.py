"""
Core breadboard data model.

Supports five layouts:
  'half'         — 400-point half board (30 columns × 10 rows)
  'full'         — 830-point standard board (63 columns × 10 rows)  [default]
  'double'       — two full boards stacked (sections 0 and 1)
  'triple'       — three full boards stacked (sections 0–2) + shared left vertical power rails
  'double_rails' — two full boards stacked + shared left AND right vertical power rails

Each section has:
- Tie strip area: columns × 10 rows (a–j)
- 4 power rails per section: top_plus, top_minus, bot_plus, bot_minus
- 3 lab power supply terminals: GND, V1, V2  (shared, no section)

Triple layout additionally has:
- 2 shared left vertical rails spanning all sections: vert_plus, vert_minus

Double_rails layout additionally has:
- 2 shared left vertical rails: vert_plus, vert_minus
- 2 shared right vertical rails: vert_right_plus, vert_right_minus

Connectivity rules (static):
- All holes in the same column + same bank + same section are connected (tie strips)
- All holes in the same power rail + same section are connected
- vert_plus/vert_minus/vert_right_plus/vert_right_minus holes are each fully connected (no split)
- Terminals are isolated until the student connects them with wires
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union

COLUMNS = 63          # default (full/double/triple)
HALF_COLUMNS = 30     # half-size board
MINI_COLUMNS = 17     # mini board (170 points, no rails)
TOP_ROWS = ('a', 'b', 'c', 'd', 'e')
BOT_ROWS = ('f', 'g', 'h', 'i', 'j')
ALL_ROWS = TOP_ROWS + BOT_ROWS
RAIL_NAMES = ('top_plus', 'top_minus', 'bot_plus', 'bot_minus')
VERT_RAIL_NAMES = ('vert_plus', 'vert_minus')                       # left vert rails (triple, double_rails)
VERT_RAIL_NAMES_RIGHT = ('vert_right_plus', 'vert_right_minus')    # right vert rails (double_rails only)
VERT_RAIL_LEN = 60                              # holes per vertical rail for triple (20 per section)
VERT_RAIL_LEN_PER_SECTION = 20                  # holes per section on vertical rails
ALL_RAIL_NAMES = RAIL_NAMES + VERT_RAIL_NAMES + VERT_RAIL_NAMES_RIGHT
RAIL_LEN = 50
RAIL_SPLIT = 25     # rails are split into two electrically separate halves here
TERMINAL_NAMES = ('GND', 'V1', 'V2')

# Derived per-layout constants
_LAYOUT_PARAMS: Dict[str, Tuple[int, int]] = {
    'mini':         (MINI_COLUMNS, 1),
    'half':         (HALF_COLUMNS, 1),
    'full':         (COLUMNS,      1),
    'double':       (COLUMNS,      2),
    'triple':       (COLUMNS,      3),
    'double_rails': (COLUMNS,      2),
}
# Layouts that have no power rails
RAILLESS_LAYOUTS = frozenset({'mini'})

# Instrument probe points — placed by the student on arbitrary holes
PROBE_NAMES = ('FG+', 'FG_GND', 'CH1', 'CH2', 'CH3', 'CH4', 'SCOPE_GND',
               'PSU1+', 'PSU1-', 'PSU2+', 'PSU2-', 'PSU3+', 'PSU3-')
PROBE_META = {
    'FG+':       {'label': 'FG+',  'color': '#c87000'},
    'FG_GND':    {'label': 'FG⏚',  'color': '#444444'},
    'CH1':       {'label': 'CH1',  'color': '#b09800'},
    'CH2':       {'label': 'CH2',  'color': '#1050b0'},
    'CH3':       {'label': 'CH3',  'color': '#208050'},
    'CH4':       {'label': 'CH4',  'color': '#802080'},
    'SCOPE_GND': {'label': 'SC⏚',  'color': '#444444'},
    'PSU1+':     {'label': 'PSU1+', 'color': '#cc2020'},
    'PSU1-':     {'label': 'PSU1-', 'color': '#882020'},
    'PSU2+':     {'label': 'PSU2+', 'color': '#1060c0'},
    'PSU2-':     {'label': 'PSU2-', 'color': '#104080'},
    'PSU3+':     {'label': 'PSU3+', 'color': '#208030'},
    'PSU3-':     {'label': 'PSU3-', 'color': '#155020'},
}


@dataclass(frozen=True)
class TieHole:
    col: int        # 1–columns (layout-dependent)
    row: str        # 'a'–'j'
    section: int = 0   # 0 = first board, 1 = second, 2 = third

    def __post_init__(self):
        assert self.col >= 1, f"Column {self.col} must be >= 1"
        assert self.row in ALL_ROWS, f"Row {self.row!r} not in {ALL_ROWS}"
        assert self.section >= 0, f"Section {self.section} must be >= 0"

    def bank(self) -> str:
        return 'top' if self.row in TOP_ROWS else 'bot'

    def __repr__(self):
        s = f'[{self.section}]' if self.section else ''
        return f"{self.row}{self.col}{s}"


@dataclass(frozen=True)
class RailHole:
    rail: str       # one of ALL_RAIL_NAMES
    index: int      # 1–RAIL_LEN (or 1–VERT_RAIL_LEN for vert rails)
    section: int = 0   # 0 for vert rails (they span all sections)

    def __post_init__(self):
        assert self.rail in ALL_RAIL_NAMES, f"Unknown rail {self.rail!r}"
        assert self.index >= 1, f"Rail index {self.index} must be >= 1"
        assert self.section >= 0, f"Section {self.section} must be >= 0"

    def __repr__(self):
        s = f'[{self.section}]' if self.section else ''
        return f"{self.rail}{s}[{self.index}]"


@dataclass(frozen=True)
class Terminal:
    name: str  # one of TERMINAL_NAMES

    def __post_init__(self):
        assert self.name in TERMINAL_NAMES, f"Unknown terminal {self.name!r}"

    def __repr__(self):
        return f"Terminal({self.name})"


@dataclass(frozen=True)
class ModulePin:
    """A pin on a free-floating PCB module (Arduino, RPi Pico, …) placed off the breadboard."""
    ref: str   # component reference, e.g. 'MCU1'
    pin: int   # pin number

    def __repr__(self):
        return f"ModulePin({self.ref}, {self.pin})"


Hole = Union[TieHole, RailHole, Terminal, ModulePin]


class UnionFind:
    """Path-compressed, union-by-rank disjoint set."""

    def __init__(self):
        self._parent: Dict = {}
        self._rank: Dict = {}

    def _ensure(self, x) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def find(self, x):
        self._ensure(x)
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x, y) -> None:
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self._rank[px] < self._rank[py]:
            px, py = py, px
        self._parent[py] = px
        if self._rank[px] == self._rank[py]:
            self._rank[px] += 1

    def connected(self, x, y) -> bool:
        return self.find(x) == self.find(y)

    def roots(self) -> Set:
        return {self.find(x) for x in self._parent}


@dataclass
class PlacedComponent:
    """A component that has been placed on the breadboard."""
    ref: str
    type_id: str                         # matches ComponentDef.type_id
    pin_holes: Dict[int, Hole]          # pin_number → hole
    flipped: int = 0                    # DIP ICs: 0/1 (mirrored); RPi modules: 0-3 (CW rotation)


@dataclass
class Wire:
    h1: Hole
    h2: Hole
    color: str = '#e8c020'  # default jumper wire yellow


class Breadboard:
    """
    Full breadboard state: static topology + student's dynamic placements.

    The connectivity is rebuilt on demand via build_connectivity().

    layout: one of 'half', 'full', 'double', 'triple'
    """

    def __init__(self, layout: str = 'full'):
        self.layout = layout
        self.columns, self.sections = _LAYOUT_PARAMS.get(layout, (COLUMNS, 1))
        self._placements: Dict[str, PlacedComponent] = {}   # ref → placement
        self._wires: List[Wire] = []
        self._static: List[Tuple[Hole, Hole]] = list(self._build_static())
        self._terminal_nets: Dict[str, str] = {}            # terminal_name → net_name
        self._probe_holes:   Dict[str, Optional[Hole]]       = {n: None for n in PROBE_NAMES}
        self._probe_nets:    Dict[str, str]                  = {n: ''   for n in PROBE_NAMES}
        self._probe_offsets: Dict[str, Tuple[int, int]]      = {n: (0, 0) for n in PROBE_NAMES}
        self._module_positions: Dict[str, Tuple[int, int]]  = {}  # ref → (canvas_x, canvas_y)

    # ------------------------------------------------------------------
    # Static breadboard topology
    # ------------------------------------------------------------------

    def _build_static(self):
        # Tie strips: connect each hole to its neighbour in the same bank, per section
        for section in range(self.sections):
            for col in range(1, self.columns + 1):
                for rows in (TOP_ROWS, BOT_ROWS):
                    for i in range(len(rows) - 1):
                        yield TieHole(col, rows[i], section), TieHole(col, rows[i + 1], section)

        # Power rails per section (not present on mini boards)
        if self.layout not in RAILLESS_LAYOUTS:
            for section in range(self.sections):
                for rail in RAIL_NAMES:
                    for i in range(1, RAIL_LEN):
                        if i == RAIL_SPLIT:
                            continue        # gap — no connection across the split
                        yield RailHole(rail, i, section), RailHole(rail, i + 1, section)

        # Shared vertical rails (triple and double_rails) — no split, fully connected
        if self.layout in ('triple', 'double_rails'):
            vert_len = self.sections * VERT_RAIL_LEN_PER_SECTION
            for rail in VERT_RAIL_NAMES:
                for i in range(1, vert_len):
                    yield RailHole(rail, i), RailHole(rail, i + 1)
            # Right-side vertical rails (double_rails only)
            if self.layout == 'double_rails':
                for rail in VERT_RAIL_NAMES_RIGHT:
                    for i in range(1, vert_len):
                        yield RailHole(rail, i), RailHole(rail, i + 1)

    # ------------------------------------------------------------------
    # Dynamic state: components
    # ------------------------------------------------------------------

    def place(self, component: PlacedComponent) -> None:
        self._placements[component.ref] = component

    def remove(self, ref: str) -> Optional[PlacedComponent]:
        self._module_positions.pop(ref, None)
        return self._placements.pop(ref, None)

    def get_placement(self, ref: str) -> Optional[PlacedComponent]:
        return self._placements.get(ref)

    @property
    def placements(self) -> Dict[str, PlacedComponent]:
        return dict(self._placements)

    # ------------------------------------------------------------------
    # Dynamic state: wires
    # ------------------------------------------------------------------

    def add_wire(self, h1: Hole, h2: Hole, color: str = '#e8c020') -> Wire:
        w = Wire(h1, h2, color)
        self._wires.append(w)
        return w

    def remove_wire(self, wire: Wire) -> bool:
        try:
            self._wires.remove(wire)
            return True
        except ValueError:
            return False

    def wire_at(self, h1: Hole, h2: Hole) -> Optional[Wire]:
        for w in self._wires:
            if (w.h1 == h1 and w.h2 == h2) or (w.h1 == h2 and w.h2 == h1):
                return w
        return None

    @property
    def wires(self) -> List[Wire]:
        return list(self._wires)

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def build_connectivity(self) -> UnionFind:
        """Build full connectivity graph from current state."""
        uf = UnionFind()
        for h1, h2 in self._static:
            uf.union(h1, h2)
        for w in self._wires:
            uf.union(w.h1, w.h2)
        return uf

    # ------------------------------------------------------------------
    # Terminal net assignments
    # ------------------------------------------------------------------

    def assign_terminal(self, terminal_name: str, net_name: str) -> None:
        """Assign a schematic net to a physical binding post terminal.
        Pass net_name='' to clear the assignment."""
        assert terminal_name in TERMINAL_NAMES, f"Unknown terminal {terminal_name!r}"
        if net_name:
            self._terminal_nets[terminal_name] = net_name
        else:
            self._terminal_nets.pop(terminal_name, None)

    def get_terminal_net(self, terminal_name: str) -> Optional[str]:
        return self._terminal_nets.get(terminal_name)

    @property
    def terminal_nets(self) -> Dict[str, str]:
        return dict(self._terminal_nets)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Instrument probes
    # ------------------------------------------------------------------

    def place_probe(self, name: str, hole: Hole) -> None:
        if name in PROBE_NAMES:
            self._probe_holes[name] = hole

    def remove_probe(self, name: str) -> None:
        if name in self._probe_holes:
            self._probe_holes[name] = None
            self._probe_offsets[name] = (0, 0)

    def get_probe_hole(self, name: str) -> Optional[Hole]:
        return self._probe_holes.get(name)

    def assign_probe_net(self, name: str, net: str) -> None:
        if name in PROBE_NAMES:
            self._probe_nets[name] = net

    def get_probe_net(self, name: str) -> str:
        return self._probe_nets.get(name, '')

    def set_probe_label_offset(self, name: str, dx: int, dy: int) -> None:
        if name in PROBE_NAMES:
            self._probe_offsets[name] = (dx, dy)

    def get_probe_label_offset(self, name: str) -> Tuple[int, int]:
        return self._probe_offsets.get(name, (0, 0))

    @property
    def probe_holes(self) -> Dict[str, Optional[Hole]]:
        return dict(self._probe_holes)

    @property
    def probe_nets(self) -> Dict[str, str]:
        return dict(self._probe_nets)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Module positions (free-floating PCB boards)
    # ------------------------------------------------------------------

    def set_module_position(self, ref: str, mx: int, my: int) -> None:
        self._module_positions[ref] = (mx, my)

    def get_module_position(self, ref: str) -> Optional[Tuple[int, int]]:
        return self._module_positions.get(ref)

    @property
    def module_positions(self) -> Dict[str, Tuple[int, int]]:
        return dict(self._module_positions)

    # ------------------------------------------------------------------

    def hole_for_pin(self, ref: str, pin: int) -> Optional[Hole]:
        p = self._placements.get(ref)
        return p.pin_holes.get(pin) if p else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_hole_occupied(self, hole: Hole) -> bool:
        """True if any placed component has a pin at this hole."""
        for p in self._placements.values():
            if hole in p.pin_holes.values():
                return True
        return False

    def occupied_holes(self) -> Set[Hole]:
        holes: Set[Hole] = set()
        for p in self._placements.values():
            holes.update(p.pin_holes.values())
        return holes
