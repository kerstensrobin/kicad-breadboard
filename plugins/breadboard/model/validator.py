"""
Circuit validator.

Compares the student's breadboard state against the schematic netlist and
reports two classes of error:

  OPEN_NET   — pins that belong to the same net are not electrically connected.
  SHORT      — pins from different nets are electrically connected.

Usage:
    results = validate(breadboard, netlist, placements_type_map)
    for r in results:
        print(r)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .breadboard import Breadboard, Hole, TieHole, Terminal, TERMINAL_NAMES, PROBE_NAMES, UnionFind
from .components import guess_type_id, ALL_DEFS
from .netlist import Net, Netlist


class IssueKind(Enum):
    OPEN_NET = 'open_net'
    SHORT = 'short'
    UNPLACED = 'unplaced'


@dataclass
class ValidationIssue:
    kind: IssueKind
    net_name: str
    description: str
    # Holes involved (for canvas highlighting)
    holes: List[Hole] = field(default_factory=list)

    def __str__(self):
        return f"[{self.kind.name}] {self.net_name}: {self.description}"


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0

    def __str__(self):
        if self.ok:
            return "Circuit OK — all nets match."
        return '\n'.join(str(i) for i in self.issues)


def _symmetric_pin_overrides(
    board: Breadboard, netlist: Netlist, uf: UnionFind
) -> Dict[Tuple[str, int], Hole]:
    """
    For non-polar 2-pin components (R, L, C) determine whether the student
    placed the component in reversed orientation and, if so, return a mapping
    that swaps pin 1 ↔ pin 2 so the circuit still validates.

    Three phases:

    1. Seed a physical-bus → schematic-net map from reliable fixed anchors:
       terminal assignments (GND/V1/V2) and non-symmetric component pins.

    2. Constraint propagation: each symmetric component whose buses include at
       least one mapped bus can be resolved immediately, which may map its
       other bus and unlock further components.  Repeats until stable — O(n)
       for typical topologies.

    3. Exhaustive search over isolated sub-problems that have no anchor
       reachable by propagation.  Components are grouped by shared physical
       bus; each group of ≤ 20 is searched in full (2^k combos).  The combo
       minimising open-net count wins.  Groups larger than 20 are left as-is
       and the validator reports the resulting errors normally.
    """
    override: Dict[Tuple[str, int], Hole] = {}

    # ref → {pin_num → net_name}
    comp_pin_nets: Dict[str, Dict[int, str]] = {}
    for net in netlist.nets:
        for pn in net.pins:
            comp_pin_nets.setdefault(pn.ref, {})[pn.pin] = net.name

    # Collect symmetric 2-pin placed components: (ref, h1, h2, net1, net2)
    sym: List[Tuple[str, Hole, Hole, str, Optional[str]]] = []
    for ref, placed in board.placements.items():
        comp_def = ALL_DEFS.get(placed.type_id)
        if not (comp_def and comp_def.symmetric and comp_def.pin_count == 2):
            continue
        h1 = placed.pin_holes.get(1)
        h2 = placed.pin_holes.get(2)
        if h1 is None or h2 is None:
            continue
        pin_nets = comp_pin_nets.get(ref, {})
        net1 = pin_nets.get(1)
        if not net1:
            continue
        sym.append((ref, h1, h2, net1, pin_nets.get(2)))

    if not sym:
        return override

    # ── Phase 1: seed bus→net from fixed anchors ─────────────────────────────
    bus_net: Dict[object, str] = {}   # uf.find(hole) → schematic net name

    def seed(hole: Hole, net_name: str) -> None:
        bus = uf.find(hole)
        if bus not in bus_net:
            bus_net[bus] = net_name

    for term_name in TERMINAL_NAMES:
        net_name = board.get_terminal_net(term_name)
        if net_name:
            seed(Terminal(term_name), net_name)

    for ref, placed in board.placements.items():
        comp_def = ALL_DEFS.get(placed.type_id)
        if comp_def and comp_def.symmetric and comp_def.pin_count == 2:
            continue  # these are what we're resolving
        for pin_num, net_name in comp_pin_nets.get(ref, {}).items():
            h = placed.pin_holes.get(pin_num)
            if h is not None:
                seed(h, net_name)

    # ── Phase 2: constraint propagation ──────────────────────────────────────
    resolved: Set[int] = set()

    def try_resolve(idx: int) -> bool:
        ref, h1, h2, net1, net2 = sym[idx]
        bus1, bus2 = uf.find(h1), uf.find(h2)
        m1, m2 = bus_net.get(bus1), bus_net.get(bus2)
        if m1 is None and m2 is None:
            return False

        if m1 is not None:
            if m1 == net1:
                swap = False
                if net2 and bus2 not in bus_net:
                    bus_net[bus2] = net2
            elif m1 == net2:
                swap = True
                if bus2 not in bus_net:
                    bus_net[bus2] = net1
            else:
                return False  # bus is on an unrelated net — wiring error, skip
        else:
            if m2 == net2:
                swap = False
                if bus1 not in bus_net:
                    bus_net[bus1] = net1
            elif m2 == net1:
                swap = True
                if bus1 not in bus_net:
                    bus_net[bus1] = net2
            else:
                return False

        resolved.add(idx)
        if swap:
            override[(ref, 1)] = h2
            override[(ref, 2)] = h1
        return True

    changed = True
    while changed:
        changed = False
        for i in range(len(sym)):
            if i not in resolved and try_resolve(i):
                changed = True

    # ── Phase 3: exhaustive search for isolated unresolved groups ─────────────
    unresolved = [i for i in range(len(sym)) if i not in resolved]
    if not unresolved:
        return override

    # Group unresolved components by shared physical buses (union-find on indices)
    parent = list(range(len(unresolved)))

    def sg_find(x: int) -> int:
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    bus_to_first: Dict[object, int] = {}
    for ui, i in enumerate(unresolved):
        _, h1, h2, _, _ = sym[i]
        for h in (h1, h2):
            bus = uf.find(h)
            if bus in bus_to_first:
                a, b = sg_find(ui), sg_find(bus_to_first[bus])
                if a != b:
                    parent[a] = b
            else:
                bus_to_first[bus] = ui

    groups: Dict[int, List[int]] = {}
    for ui, i in enumerate(unresolved):
        groups.setdefault(sg_find(ui), []).append(i)

    def net_split_count(trial: Dict) -> int:
        """Count nets whose placed pins don't all share one physical bus."""
        net_buses: Dict[str, Set] = {}
        for net in netlist.nets:
            for pn in net.pins:
                key = (pn.ref, pn.pin)
                hole = trial.get(key) or board.hole_for_pin(pn.ref, pn.pin)
                if hole is not None:
                    net_buses.setdefault(net.name, set()).add(uf.find(hole))
        return sum(1 for buses in net_buses.values() if len(buses) > 1)

    MAX_EXHAUSTIVE = 20   # 2^20 ≈ 1 M combos — completes in milliseconds
    for group in groups.values():
        k = len(group)
        if k > MAX_EXHAUSTIVE:
            continue  # leave as-is; validator will report errors normally

        best_score, best_combo = float('inf'), 0
        for combo in range(1 << k):
            trial = dict(override)
            for bit, idx in enumerate(group):
                ref, h1, h2, _, _ = sym[idx]
                if combo & (1 << bit):
                    trial[(ref, 1)] = h2
                    trial[(ref, 2)] = h1
            score = net_split_count(trial)
            if score < best_score:
                best_score, best_combo = score, combo

        for bit, idx in enumerate(group):
            ref, h1, h2, _, _ = sym[idx]
            if best_combo & (1 << bit):
                override[(ref, 1)] = h2
                override[(ref, 2)] = h1

    return override


def validate(board: Breadboard, netlist: Netlist) -> ValidationResult:
    """
    Validate the student's breadboard against the schematic netlist.

    Steps:
      1. Check all components are placed.
      2. Build the connectivity graph.
      3. For each schematic net, collect the holes of all its pins.
         If they are not all in the same connected component → OPEN_NET.
      4. Collect all (root, net_name) pairs; if two different nets share a
         root → SHORT.
    """
    result = ValidationResult()
    uf = board.build_connectivity()

    # Step 1 — unplaced physical components
    # Virtual components (simulation sources, power symbols) have no known
    # type_id and are handled via terminal assignments instead.
    for ref, comp in netlist.components.items():
        if board.get_placement(ref) is None:
            type_id = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description, comp.pin_count)
            if type_id is not None:
                result.issues.append(ValidationIssue(
                    kind=IssueKind.UNPLACED,
                    net_name='',
                    description=f"{ref} ({comp.value}) is not placed on the breadboard.",
                ))

    # For symmetric (non-polar) components, determine optimal pin-hole mapping
    pin_override = _symmetric_pin_overrides(board, netlist, uf)

    # Step 2 — build a map: net_name → list of holes
    # comp_pin_counts tracks only placed-component pins (not terminals) so that
    # single-endpoint nets (e.g. a wire ending only in a schematic label like
    # "OUTPUT") are not flagged as OPEN_NET — they have only 1 component pin
    # and are deliberately unconnected to another component.
    comp_pin_counts: Dict[str, int] = {}
    net_holes: Dict[str, List[Hole]] = {}

    # Total schematic nodes per net (placed + unplaced); also track whether each net
    # has any power_in pins — used to detect supply nets where the second endpoint is
    # a SPICE source that isn't placed on the breadboard.
    schematic_node_counts: Dict[str, int] = {net.name: len(net.pins) for net in netlist.nets}
    power_in_nets: Set[str] = {
        net.name for net in netlist.nets
        if any(p.pintype == 'power_in' for p in net.pins)
    }

    for net in netlist.nets:
        holes: List[Hole] = []
        for pin_node in net.pins:
            key = (pin_node.ref, pin_node.pin)
            hole = pin_override.get(key) if key in pin_override else board.hole_for_pin(pin_node.ref, pin_node.pin)
            if hole is not None:
                holes.append(hole)
        if holes:
            comp_pin_counts[net.name] = len(holes)
            net_holes[net.name] = holes

    # Add terminal and probe holes — count them so power-supply nets are validated
    terminal_pin_counts: Dict[str, int] = {}
    for term_name in TERMINAL_NAMES:
        net_name = board.get_terminal_net(term_name)
        if net_name:
            net_holes.setdefault(net_name, []).append(Terminal(term_name))
            terminal_pin_counts[net_name] = terminal_pin_counts.get(net_name, 0) + 1
    for probe_name in PROBE_NAMES:
        hole    = board.get_probe_hole(probe_name)
        net_name = board.get_probe_net(probe_name)
        if hole is not None and net_name:
            net_holes.setdefault(net_name, []).append(hole)
            terminal_pin_counts[net_name] = terminal_pin_counts.get(net_name, 0) + 1

    # Step 3 — open nets
    # Require at least 2 total endpoints (placed component pins + assigned terminals);
    # single-endpoint label-only nets are intentionally exempt.
    # Exception: if the schematic has ≥2 nodes for a net but only 1 is placed
    # (e.g. a DIP power pin whose other endpoint is a SPICE source), flag it so
    # the student knows they must assign a binding-post terminal for that net.
    for net_name, holes in net_holes.items():
        comp  = comp_pin_counts.get(net_name, 0)
        term  = terminal_pin_counts.get(net_name, 0)
        total = comp + term
        if total < 2:
            if (comp >= 1 and term == 0
                    and schematic_node_counts.get(net_name, 0) >= 2
                    and net_name in power_in_nets):
                # Supply net with unplaced second endpoint and no terminal assigned
                result.issues.append(ValidationIssue(
                    kind=IssueKind.OPEN_NET,
                    net_name=net_name,
                    description=(
                        f"Net '{net_name}' has component pins but no supply terminal "
                        f"assigned. Use the binding-post dropdown to assign a terminal "
                        f"to this net, then connect it on the board."
                    ),
                    holes=holes,
                ))
            continue
        roots = {uf.find(h) for h in holes}
        if len(roots) > 1:
            result.issues.append(ValidationIssue(
                kind=IssueKind.OPEN_NET,
                net_name=net_name,
                description=(
                    f"Net '{net_name}' is not fully connected "
                    f"({len(roots)} disconnected groups)."
                ),
                holes=holes,
            ))

    # Step 4 — shorts: two nets sharing the same connected component
    root_to_nets: Dict[object, Set[str]] = {}
    for net_name, holes in net_holes.items():
        for h in holes:
            root = uf.find(h)
            root_to_nets.setdefault(root, set()).add(net_name)

    for root, net_names in root_to_nets.items():
        if len(net_names) > 1:
            names = ', '.join(sorted(net_names))
            # Collect all holes for these nets (for highlighting)
            shorted_holes = []
            for net_name in net_names:
                shorted_holes.extend(net_holes.get(net_name, []))
            result.issues.append(ValidationIssue(
                kind=IssueKind.SHORT,
                net_name=names,
                description=f"Nets {names} are shorted together.",
                holes=shorted_holes,
            ))

    return result
