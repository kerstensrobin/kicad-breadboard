"""
KiCad S-expression netlist parser.

Reads the KiCad S-expression netlist format (.net) exported from Eeschema via
File > Export > Netlist > KiCad format.  KiCad 7+ uses S-expression by default.

Top-level structure:
  (export (version "E")
    (components
      (comp (ref "R1") (value "1k")
        (libsource (lib "Device") (part "R") (description "Resistor")) ...))
    (nets
      (net (code "1") (name "GND")
        (node (ref "R1") (pin "1") (pintype "passive")) ...)))
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Minimal S-expression tokeniser + parser
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\n\r':
            i += 1
        elif c == '(':
            tokens.append('('); i += 1
        elif c == ')':
            tokens.append(')'); i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == '\\':
                    j += 1
                j += 1
            tokens.append(text[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\n\r()':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


SExp = Union[str, List]


def _parse_one(tokens: List[str], pos: int):
    """Parse one value from tokens[pos:]. Returns (value, next_pos)."""
    if pos >= len(tokens):
        return None, pos
    tok = tokens[pos]
    if tok == '(':
        children: List[SExp] = []
        pos += 1
        while pos < len(tokens) and tokens[pos] != ')':
            val, pos = _parse_one(tokens, pos)
            if val is not None:
                children.append(val)
        return children, pos + 1   # consume ')'
    else:
        return tok, pos + 1


def _find(node: SExp, key: str) -> Optional[List]:
    """First child list whose first element equals key."""
    if not isinstance(node, list):
        return None
    for child in node:
        if isinstance(child, list) and child and child[0] == key:
            return child
    return None


def _find_all(node: SExp, key: str) -> List[List]:
    if not isinstance(node, list):
        return []
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _val(node: Optional[List]) -> str:
    """Return the string payload of a (key "value") node."""
    if node and len(node) > 1 and isinstance(node[1], str):
        return node[1]
    return ''


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------

@dataclass
class NetlistPin:
    ref: str
    pin: int              # pin number (0 if non-numeric / named pin)
    pintype: str = ''     # KiCad pin type, e.g. 'power_in', 'input', 'output', 'passive'
    pinfunction: str = '' # KiCad pin function/name from the symbol, e.g. 'OUTPUT', 'GND', 'TRIG'; '~' if unnamed


@dataclass
class Net:
    code: int
    name: str
    pins: List[NetlistPin] = field(default_factory=list)


@dataclass
class NetlistComponent:
    ref: str
    value: str
    symbol: str      # KiCad part name, e.g. 'R', 'NPN', 'TL081'
    lib: str
    description: str = ''
    pin_count: int = 0  # number of distinct pins appearing in nets


@dataclass
class Netlist:
    components: Dict[str, NetlistComponent]
    nets: List[Net]

    def nets_for_ref(self, ref: str) -> Dict[int, Net]:
        """Return {pin_number: Net} for a given component reference."""
        result: Dict[int, Net] = {}
        for net in self.nets:
            for pin in net.pins:
                if pin.ref == ref:
                    result[pin.pin] = net
        return result

    def net_by_name(self, name: str) -> Optional[Net]:
        for net in self.nets:
            if net.name == name:
                return net
        return None

    def pinfunction_map(self, ref: str) -> Dict[int, str]:
        """Return {pin_num: pinfunction} for a component from the netlist pin data.
        KiCad appends _{pin_number} to disambiguate duplicate function names (e.g. '+_2');
        that suffix is stripped here."""
        result: Dict[int, str] = {}
        for net in self.nets:
            for pin in net.pins:
                if pin.ref == ref and pin.pin and pin.pinfunction and pin.pinfunction != '~':
                    fn = re.sub(r'_\d+$', '', pin.pinfunction)
                    result[pin.pin] = fn
        return result

    def power_net_names(self) -> List[str]:
        candidates = []
        for net in self.nets:
            n = net.name.upper()
            if any(k in n for k in ('GND', 'VCC', 'VDD', 'V+', 'V-', 'PWR', 'VSS', 'VEE')):
                candidates.append(net.name)
        return candidates


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse(path: str | Path) -> Netlist:
    """Parse a KiCad S-expression netlist file."""
    text = Path(path).read_text(encoding='utf-8')
    tokens = _tokenize(text)
    tree, _ = _parse_one(tokens, 0)

    # tree is the (export ...) list
    export = tree

    # --- components ---
    components: Dict[str, NetlistComponent] = {}
    comps_node = _find(export, 'components')
    if comps_node:
        for comp in _find_all(comps_node, 'comp'):
            ref   = _val(_find(comp, 'ref'))
            value = _val(_find(comp, 'value'))
            libsrc = _find(comp, 'libsource')
            lib   = _val(_find(libsrc, 'lib'))   if libsrc else ''
            part  = _val(_find(libsrc, 'part'))  if libsrc else ''
            desc  = _val(_find(libsrc, 'description')) if libsrc else ''
            if ref:
                components[ref] = NetlistComponent(
                    ref=ref, value=value, symbol=part, lib=lib, description=desc
                )

    # --- nets ---
    nets: List[Net] = []
    nets_node = _find(export, 'nets')
    if nets_node:
        for net_el in _find_all(nets_node, 'net'):
            try:
                code = int(_val(_find(net_el, 'code')))
            except ValueError:
                code = 0
            name = _val(_find(net_el, 'name'))
            pins: List[NetlistPin] = []
            for node in _find_all(net_el, 'node'):
                nref = _val(_find(node, 'ref'))
                try:
                    pin_num = int(_val(_find(node, 'pin')))
                except ValueError:
                    pin_num = 0
                pintype = _val(_find(node, 'pintype'))
                pinfunction = _val(_find(node, 'pinfunction'))
                if nref:
                    pins.append(NetlistPin(ref=nref, pin=pin_num, pintype=pintype, pinfunction=pinfunction))
            nets.append(Net(code=code, name=name, pins=pins))

    # Back-fill pin_count on each component from what appears in the nets
    _pins_seen: Dict[str, set] = {}
    for net in nets:
        for pin in net.pins:
            _pins_seen.setdefault(pin.ref, set()).add(pin.pin)
    for _ref, _pins in _pins_seen.items():
        if _ref in components:
            components[_ref].pin_count = len(_pins)

    return Netlist(components=components, nets=nets)


def find_netlist(project_path: str | Path) -> Optional[Path]:
    """
    Given a KiCad project directory, .kicad_pro file, or direct .net path,
    return the path to the netlist file.
    """
    p = Path(project_path)
    if p.suffix == '.net' and p.is_file():
        return p
    if p.is_file():
        p = p.parent
    nets = list(p.glob('*.net'))
    return nets[0] if nets else None


def find_schematic(project_path: str | Path) -> Optional[Path]:
    """
    Given a KiCad project directory or any file inside it, return the path
    to the top-level .kicad_sch file (same stem as .kicad_pro if present).
    """
    p = Path(project_path)
    if p.suffix == '.kicad_sch' and p.is_file():
        return p
    if p.is_file():
        p = p.parent
    # Prefer the schematic whose stem matches the .kicad_pro project file
    pros = list(p.glob('*.kicad_pro'))
    if pros:
        candidate = p / f'{pros[0].stem}.kicad_sch'
        if candidate.is_file():
            return candidate
    schs = list(p.glob('*.kicad_sch'))
    return schs[0] if schs else None
