"""
Breadboard canvas — wxPython panel that renders the breadboard and handles
all student interaction: drag-drop component placement, wire drawing, deletion.

Coordinate system
-----------------
Canvas pixels are computed from breadboard addresses by CanvasLayout.
  x  increases rightward  (column direction)
  y  increases downward

Layout (top to bottom):
  MARGIN
  Top power rails  (top_plus = red, top_minus = blue)
  RAIL_GAP
  Top tie-strip bank (rows a–e)
  CENTER_GAP   (the physical gap between the two banks)
  Bottom tie-strip bank (rows f–j)
  RAIL_GAP
  Bottom power rails (bot_plus = red, bot_minus = blue)
  MARGIN

Terminals (GND, V1, V2) are drawn as labelled boxes on the left side.

Interaction modes
-----------------
  MODE_SELECT  : left-click selects / moves placed components
  MODE_WIRE    : first click = wire start, second click = wire end
  MODE_DELETE  : left-click on a component or wire removes it

Placement flow (replaces drag-drop)
------------------------------------
  Click a card in the tray → canvas.begin_place(comp_def, ref) is called.
  A ghost preview follows the mouse.  Left-click on a valid hole places the
  component.  Right-click or Escape cancels.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import wx

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_BRAND_IMAGE = os.path.join(_HERE, 'resources', 'kicad_bbrd.png')


def _make_gc(dc: 'wx.DC') -> 'wx.GraphicsContext | None':
    """Return a GraphicsContext for dc, or None if dc doesn't support it (e.g. SVGFileDC)."""
    try:
        return wx.GraphicsContext.Create(dc)
    except Exception:
        return None


def _transparent_brush() -> 'wx.Brush':
    return wx.Brush(wx.Colour(0, 0, 0, 0), wx.BRUSHSTYLE_TRANSPARENT)


def _parse_svg_size(path: str):
    """Return (width, height) from SVG viewBox attribute, or (100.0, 100.0) as fallback."""
    import re
    try:
        with open(path) as _f:
            _data = _f.read(1024)
        _m = re.search(r'viewBox=["\'][\d.eE+\- ]+ ([\d.eE+\-]+) ([\d.eE+\-]+)["\']', _data)
        if _m:
            return float(_m.group(1)), float(_m.group(2))
    except Exception:
        pass
    return 100.0, 100.0

from .model import (
    Breadboard, PlacedComponent, Wire,
    TieHole, RailHole, Terminal, ModulePin, Hole,
    COLUMNS, HALF_COLUMNS, MINI_COLUMNS, TOP_ROWS, BOT_ROWS, ALL_ROWS,
    RAIL_NAMES, VERT_RAIL_NAMES, RAIL_LEN, VERT_RAIL_LEN_PER_SECTION, RAIL_SPLIT, TERMINAL_NAMES,
    RAILLESS_LAYOUTS,
    SUNNY11_UPPER_COLS, SUNNY11_UPPER_RAIL_LEN, SUNNY11_LOWER_COLS,
    SUNNY11_LOWER_RAIL_LEN, SUNNY11_LOWER_ROWS,
    PROBE_NAMES, PROBE_META,
    ComponentDef, ALL_DEFS,
    Netlist, guess_type_id,
    validate, IssueKind,
    RPi_PIN_NAMES_LONG,
    ARDUINO_UNO_FN_NAMES,
)

# ---------------------------------------------------------------------------
# Layout constants (pixels)
# ---------------------------------------------------------------------------
PITCH = 18          # distance between adjacent holes
HOLE_R = 3          # hole dot radius

# Free-floating PCB module geometry (Arduino Nano, RPi Pico, …)
# Modules are drawn HORIZONTALLY: pins run along the top and bottom edges,
# the long axis is left-right, and the USB connector protrudes from the left.
#
#    [USB] ┌─────────────────────────────────────────┐
#          │ ▪ ▪ ▪ ▪ ▪  top pins (labels above)  ▪ ▪ │
#          │        Board Name / Ref               │
#          │ ▪ ▪ ▪ ▪ ▪  bot pins (labels below)  ▪ ▪ │
#          └─────────────────────────────────────────┘
#
MODULE_PIN_PITCH = 18   # horizontal pitch between adjacent header pins
MODULE_BODY_H    = 38   # default body height between the two pin-row centres
MODULE_BODY_PAD  = 5    # body extends this many px beyond the outermost pin
MODULE_PIN_R     = 4    # header pad circle radius
MODULE_LABEL_GAP = 4    # gap between pad edge and label text
MODULE_HEADER_H  = 10   # height of the black header strip along each long edge

# Per-type inner body height (px between top and bottom pin-row centres).
_MODULE_BODY_H: Dict[str, int] = {
    'Arduino_Nano': 3 * PITCH,  # rows three grid-steps apart — aligns to hole grid in any rotation
    'Arduino_Uno':  4 * PITCH,  # rows four grid-steps apart — aligns to hole grid in any rotation
    'RPi_Pico':         PITCH,  # rows one grid-step apart — aligns to hole grid in any rotation
}
_RPi_BOARD_H         = 140  # total RPi board height for landscape orientation
_RPi_PORTRAIT_PITCH  = 10  # pin pitch for portrait orientations (smaller than 18 to keep board compact)

# Per-module connector specs reserved for future use; Nano USB is drawn inline.
_MODULE_CONNECTORS: Dict[str, tuple] = {}
RAIL_H = 18         # height of each power rail colour strip
RAIL_GAP = 18       # gap between rail area and tie-strip area
CENTER_GAP = 28     # gap between top and bottom tie-strip banks
MARGIN = 20         # outer margin
SECTION_GAP = 0     # stacked board sections touch — no gap between them
VERT_STRIP_X = 54   # x-width allocated for the two vertical rails (triple layout)
BRAND_STRIP = 80    # extra space (px) added on the binding-post side for the brand image

# sunny-11: the two upper tie-blocks are rendered transposed relative to every
# other layout (columns run vertically, bank-rows run horizontally) and sit
# side by side, portrait-oriented.
SUNNY11_GAP = CENTER_GAP        # horizontal gap between the a-e/f-j banks
SUNNY11_BLOCK_GAP_X = SUNNY11_GAP  # gap between the V1 and V2 blocks — the same
                                    # width as the internal a-e/f-j dividers, not
                                    # a wider seam, matching the real board
SUNNY11_BLOCK_GAP_Y = 36        # gap between the upper blocks and the lower block

# Binding posts (circular)
TERM_R = 30         # radius of binding-post circle
TERM_CX = TERM_R + 8   # x-centre of all binding posts (from canvas left edge)
TERM_COLORS = {
    'GND': ('#3a3a3a', '#707070'),   # (body colour, highlight ring colour)
    'V1':  ('#bb2020', '#ee7070'),
    'V2':  ('#1a7a30', '#55bb66'),
    'V3':  ('#1a5a8a', '#4499cc'),
    'V4':  ('#7a3a9a', '#b070d0'),
}

WIRE_COLORS = [
    '#e8c020',  # yellow
    '#991111',  # red
    '#1133aa',  # blue
    '#20c040',  # green
    '#e08020',  # orange
    '#c020c0',  # purple
    '#20c0c0',  # cyan
    '#808080',  # grey
    '#111111',  # black
]

MODE_SELECT        = 'select'
MODE_WIRE          = 'wire'
MODE_DELETE        = 'delete'
MODE_PROBE         = 'probe'
MODE_NET_HIGHLIGHT = 'net_highlight'
MODE_NET_PROBE     = 'net_probe'
MODE_DRAW_LINE     = 'draw_line'
MODE_DRAW_RECT     = 'draw_rect'
MODE_DRAW_TEXT     = 'draw_text'
MODE_DRAW_CIRCLE   = 'draw_circle'
MODE_DRAW_TEXTBOX  = 'draw_textbox'

_DRAW_MODES = frozenset({MODE_DRAW_LINE, MODE_DRAW_RECT, MODE_DRAW_TEXT,
                         MODE_DRAW_CIRCLE, MODE_DRAW_TEXTBOX})


@dataclass
class DrawLine:
    x1: float; y1: float; x2: float; y2: float
    color: str = '#333333'; width: int = 2

@dataclass
class DrawRect:
    x1: float; y1: float; x2: float; y2: float
    color: str = '#333333'; width: int = 2
    fill: bool = False; fill_color: str = '#dddddd'

@dataclass
class DrawText:
    x: float; y: float; text: str
    color: str = '#222222'; font_size: int = 11
    bold: bool = False; italic: bool = False

@dataclass
class DrawCircle:
    cx: float; cy: float; r: float
    color: str = '#333333'; width: int = 2
    fill: bool = False; fill_color: str = '#dddddd'

@dataclass
class DrawTextBox:
    x1: float; y1: float; x2: float; y2: float
    text: str = ''
    color: str = '#333333'; width: int = 1
    fill: bool = True; fill_color: str = '#fffbe6'
    font_size: int = 10; bold: bool = False; italic: bool = False
    text_color: str = '#222222'


# ---------------------------------------------------------------------------
# Resistor colour-band helpers
# ---------------------------------------------------------------------------

_BAND_COLORS = [
    '#111111',  # 0  Black
    '#8B3A0F',  # 1  Brown
    '#CC2200',  # 2  Red
    '#FF7700',  # 3  Orange
    '#CCAA00',  # 4  Yellow
    '#226600',  # 5  Green
    '#2244AA',  # 6  Blue
    '#882288',  # 7  Violet
    '#777777',  # 8  Grey
    '#F8F8F8',  # 9  White
]
_GOLD   = '#D4AA00'
_SILVER = '#C8C8C8'


def _parse_ohms(value_str: str) -> Optional[float]:
    """Parse a KiCad resistance value string to ohms, or None if unparseable."""
    import re
    s = value_str.strip()
    # Strip trailing Ω / ohm / R (unit indicator)
    s = re.sub(r'[ΩΩ]$', '', s).strip()
    s = re.sub(r'(?i)ohm$', '', s).strip()

    # "4k7" / "4K7" style (multiplier in middle, e.g. 4.7 kΩ)
    m = re.match(r'^(\d+(?:\.\d+)?)[kK](\d*)$', s)
    if m:
        major = float(m.group(1))
        minor = float('0.' + m.group(2)) if m.group(2) else 0
        return (major + minor) * 1e3

    m = re.match(r'^(\d+(?:\.\d+)?)[mM](\d*)$', s)
    if m:
        major = float(m.group(1))
        minor = float('0.' + m.group(2)) if m.group(2) else 0
        return (major + minor) * 1e6

    # "4R7" decimal-separator style (4.7 Ω)
    m = re.match(r'^(\d+)[Rr](\d+)$', s)
    if m:
        return float(m.group(1)) + float(m.group(2)) / (10 ** len(m.group(2)))

    # Plain numeric with optional trailing multiplier letter
    for suffix, mult in (('K', 1e3), ('k', 1e3), ('M', 1e6), ('G', 1e9)):
        if s.endswith(suffix):
            try:
                return float(s[:-1]) * mult
            except ValueError:
                pass

    # Trailing R is just the ohm unit
    s = re.sub(r'[Rr]$', '', s)
    try:
        return float(s)
    except ValueError:
        return None


def _resistor_bands(ohms: float) -> Optional[Tuple[str, str, str, str]]:
    """Return (band1, band2, band3_multiplier, band4_tolerance) as hex colours."""
    if ohms <= 0:
        return None
    exp = int(math.floor(math.log10(ohms)))
    d1  = int(ohms / 10 ** exp)
    d2  = int(round(ohms / 10 ** (exp - 1))) % 10
    d1  = max(0, min(9, d1))
    d2  = max(0, min(9, d2))
    mult = exp - 1

    if mult < -2 or mult > 9:
        return None
    c3 = _SILVER if mult == -2 else (_GOLD if mult == -1 else _BAND_COLORS[mult])
    return _BAND_COLORS[d1], _BAND_COLORS[d2], c3, _GOLD   # gold = ±5 %


_R_END_HH = 6.5   # end-cap half-height (13 px full)
_R_CAP_W  = 9.0   # end-cap width (each side inward from body edge)
_R_MID_HH = 4.5   # middle bar half-height (9 px full)
_R_CAP_R  = 4.0   # end-cap corner radius (must be < _R_CAP_W/2 = 4.5)


def _res_body_half_height(x: float, body_half: float) -> float:
    """Half-height of the dumbbell resistor body at local x (origin at centre)."""
    return _R_END_HH if abs(x) > body_half - _R_CAP_W else _R_MID_HH


def _make_res_path(gc, body_half: float):
    """Outer contour of the dumbbell resistor shape as a single closed GraphicsPath."""
    r, bh, cw, eh, mh = _R_CAP_R, body_half, _R_CAP_W, _R_END_HH, _R_MID_HH
    # S-curve width at each cap↔bar junction — capped so it fits between the end-cap
    # corner arc and the far edge of the middle bar.
    S = max(0.0, min((eh - mh) * 2.0, bh - cw - r))
    p = gc.CreatePath()
    p.MoveToPoint(-bh + r, -eh)
    p.AddArc(-bh + r, -eh + r, r, -math.pi / 2, -math.pi, False)   # TL
    p.AddLineToPoint(-bh, eh - r)
    p.AddArc(-bh + r,  eh - r, r,  math.pi,      math.pi / 2, False)  # BL
    # Bottom: cap → S-curve → bar → S-curve → cap
    p.AddLineToPoint(-bh + cw - S, eh)
    p.AddCurveToPoint(-bh + cw, eh,  -bh + cw, mh,  -bh + cw + S, mh)
    p.AddLineToPoint( bh - cw - S, mh)
    p.AddCurveToPoint( bh - cw, mh,   bh - cw, eh,   bh - cw + S, eh)
    p.AddLineToPoint( bh - r,  eh)
    p.AddArc( bh - r,  eh - r, r,  math.pi / 2,  0,           False)  # BR
    p.AddLineToPoint( bh, -eh + r)
    p.AddArc( bh - r, -eh + r, r,  0,            -math.pi / 2, False)  # TR
    # Top: cap → S-curve → bar → S-curve → cap
    p.AddLineToPoint( bh - cw + S, -eh)
    p.AddCurveToPoint( bh - cw, -eh,   bh - cw, -mh,   bh - cw - S, -mh)
    p.AddLineToPoint(-bh + cw + S, -mh)
    p.AddCurveToPoint(-bh + cw, -mh,  -bh + cw, -eh,  -bh + cw - S, -eh)
    p.CloseSubpath()
    return p


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------

class CanvasLayout:
    """
    Maps breadboard addresses to canvas pixel coordinates.

    Supports all four board layouts (half / full / double / triple) and
    all four binding-post positions (left / right / top / bottom).
    """

    def __init__(self, board_layout: str = 'full', binding_post_side: str = 'left',
                 show_branding: bool = False, rail_split: bool = True,
                 num_terminals: int = 3):
        # Normalize legacy 'top'/'bottom' → 'top_right'/'bottom_right'
        if binding_post_side == 'top':    binding_post_side = 'top_right'
        if binding_post_side == 'bottom': binding_post_side = 'bottom_right'
        _is_top    = binding_post_side.startswith('top_')
        _is_bottom = binding_post_side.startswith('bottom_')
        _is_left   = binding_post_side == 'left'
        _is_right  = binding_post_side == 'right'
        _is_horiz  = _is_left or _is_right
        _h_align   = binding_post_side.rsplit('_', 1)[-1] if (_is_top or _is_bottom) else ''
        self.board_layout      = board_layout
        self.binding_post_side = binding_post_side
        self.rail_split        = rail_split
        if board_layout == 'sunny-11':
            self._init_sunny11()
            return
        _col_map = {'mini': MINI_COLUMNS, 'half': HALF_COLUMNS}
        self.columns  = _col_map.get(board_layout, COLUMNS)
        self.sections = {'half': 1, 'full': 1, 'double': 2, 'triple': 3, 'double_rails': 2}.get(board_layout, 1)
        self.has_rails = board_layout not in RAILLESS_LAYOUTS
        _rail_len_map = {'half': 24}   # 24 holes/rail: cols 2–29, 1-col padding each end
        self.rail_len  = _rail_len_map.get(board_layout, min(RAIL_LEN, self.columns)) if self.has_rails else 0

        # --- Relative y layout for one section (relative to section top = 0) ---
        self._row_rel: Dict[str, int] = {}
        self._rail_rel: Dict[str, int] = {}

        if self.has_rails:
            top_minus_rel = MARGIN // 2   # built-in top padding — same spacing in every section
            top_plus_rel  = top_minus_rel + RAIL_H + 2
            tie_top_rel   = top_plus_rel  + RAIL_H + RAIL_GAP
            for i, row in enumerate(TOP_ROWS):
                self._row_rel[row] = tie_top_rel + i * PITCH
            tie_bot_rel = self._row_rel['e'] + PITCH + CENTER_GAP
            for i, row in enumerate(BOT_ROWS):
                self._row_rel[row] = tie_bot_rel + i * PITCH
            bot_plus_rel  = self._row_rel['j'] + RAIL_GAP
            bot_minus_rel = bot_plus_rel + RAIL_H + 2
            self._rail_rel = {
                'top_plus':  top_plus_rel  + RAIL_H // 2,
                'top_minus': top_minus_rel + RAIL_H // 2,
                'bot_plus':  bot_plus_rel  + RAIL_H // 2,
                'bot_minus': bot_minus_rel + RAIL_H // 2,
            }
            self._section_body_h = bot_minus_rel + RAIL_H + MARGIN
        else:
            # Mini: no rails — just tie strips with equal top and bottom margins
            tie_top_rel = MARGIN // 2
            for i, row in enumerate(TOP_ROWS):
                self._row_rel[row] = tie_top_rel + i * PITCH
            tie_bot_rel = self._row_rel['e'] + PITCH + CENTER_GAP
            for i, row in enumerate(BOT_ROWS):
                self._row_rel[row] = tie_bot_rel + i * PITCH
            self._section_body_h = self._row_rel['j'] + MARGIN // 2 + 4

        # --- Section vertical offsets ---
        # Extra space reserved above/below when binding posts are on top/bottom
        # Extra MARGIN added so terminals have breathing room before the board body
        _post_extra = TERM_R * 2 + MARGIN * 2
        top_extra = _post_extra if _is_top    else 0
        bot_extra = _post_extra if _is_bottom else 0
        self._section_top = [
            MARGIN + top_extra + s * (self._section_body_h + SECTION_GAP)
            for s in range(self.sections)
        ]
        self.total_height = self._section_top[-1] + self._section_body_h + MARGIN + bot_extra

        # Backward-compat aliases (section 0)
        self._row_y  = {row:  self._section_top[0] + off for row, off in self._row_rel.items()}
        self._rail_y = {rail: self._section_top[0] + off for rail, off in self._rail_rel.items()}

        # --- x layout ---
        vert_space = VERT_STRIP_X if board_layout in ('triple', 'double_rails') else 0
        _brand = BRAND_STRIP if show_branding else 0
        _brand_gap = 8   # gap between branding strip and the nearest post edge
        _POST_BOARD_GAP = 40  # gap between post edge and nearest board body edge

        # Binding-post x-centre — computed before board_left so the left-side
        # case can push the board right by the correct amount.
        # Layout for left:  [MARGIN] [brand?+gap?] [post] [gap] [board…]
        # Layout for right: [board…] [gap] [post] [gap?+brand?] [MARGIN]
        if _is_left:
            term_cx = MARGIN + (_brand + _brand_gap if _brand else 0) + TERM_R
            self.board_left = term_cx + TERM_R + _POST_BOARD_GAP + vert_space
        else:
            # board rect is drawn from (board_left − PITCH − MARGIN//2); keep
            # that left edge at least MARGIN from the canvas edge (x = 0).
            self.board_left = MARGIN + PITCH + MARGIN // 2 + vert_space

        board_right = self.board_left + (self.columns - 1) * PITCH

        if _is_right:
            term_cx = board_right + TERM_R + _POST_BOARD_GAP

        # --- Binding post positions ---
        _large_board = board_layout in ('double', 'triple', 'double_rails')
        _max_terms = 4 if (not _is_horiz or _large_board) else 3
        _active_terminals = TERMINAL_NAMES[:max(2, min(num_terminals, _max_terms))]
        n = len(_active_terminals)
        if _is_horiz:
            v_margin = int(self.total_height * 0.18)
            spacing  = (self.total_height - 2 * v_margin) // max(1, n - 1)
            self._term_pos = {
                name: (term_cx, v_margin + i * spacing)
                for i, name in enumerate(_active_terminals)
            }
        else:
            h_spacing = TERM_R * 3 + 12
            if _h_align == 'left':
                start_x = self.board_left + TERM_R
            elif _h_align == 'center':
                start_x = (self.board_left + board_right) // 2 - (n - 1) * h_spacing // 2
            else:  # 'right'
                start_x = board_right - (n - 1) * h_spacing - TERM_R
            term_y = (MARGIN // 2 + TERM_R + 4) if _is_top \
                     else (self.total_height - bot_extra + MARGIN // 2 + TERM_R + 4)
            self._term_pos = {
                name: (start_x + i * h_spacing, term_y)
                for i, name in enumerate(_active_terminals)
            }

        # Legacy _term_y for any code still using it
        self._term_y = {name: pos[1] for name, pos in self._term_pos.items()}

        # --- Vertical rails (triple and double_rails layouts) ---
        if board_layout in ('triple', 'double_rails'):
            vert_rail_len = self.sections * VERT_RAIL_LEN_PER_SECTION
            # Left rails: between binding posts (left side) and board, or at left margin
            if _is_left:
                vrl = term_cx + TERM_R + 8
            else:
                vrl = MARGIN
            self._vert_rail_cx: Dict[str, int] = {
                'vert_plus':  vrl + PITCH,
                'vert_minus': vrl + PITCH * 2,
            }
            # Right rails (double_rails only): mirror the left-side gap exactly.
            # Compute the actual gap between the left rail background panel and the
            # board body, then apply the same gap on the right.
            if board_layout == 'double_rails':
                _sw2 = (PITCH - 4) // 2
                _board_vis_left  = self.board_left - PITCH - MARGIN // 2
                _left_gap = _board_vis_left - (vrl + 2 * PITCH + _sw2 + 4)
                _board_vis_right = board_right + PITCH + MARGIN // 2
                vrr = _board_vis_right + _left_gap - PITCH + _sw2 + 4
                self._vert_rail_cx['vert_right_plus']  = vrr + PITCH
                self._vert_rail_cx['vert_right_minus'] = vrr + PITCH * 2
            # Distribute holes across the stacked-board height (not full canvas height),
            # with an inset of MARGIN so the top/bottom holes clear the +/− symbols.
            boards_top    = self._section_top[0] - MARGIN // 2
            boards_bottom = self._section_top[-1] + self._section_body_h
            hole_pad = MARGIN
            span = (boards_bottom - hole_pad) - (boards_top + hole_pad)
            step = span // max(1, vert_rail_len - 1)
            self._vert_hole_y: List[int] = [boards_top + hole_pad + i * step for i in range(vert_rail_len)]
        else:
            self._vert_rail_cx = {}
            self._vert_hole_y  = []

        # Module pin pixel positions (populated by BreadboardCanvas after placement)
        self._module_pin_xy: Dict[Tuple[str, int], Tuple[int, int]] = {}

        # --- Total width ---
        right_edge = board_right + PITCH + MARGIN // 2
        if board_layout == 'double_rails':
            right_edge = self._vert_rail_cx['vert_right_minus'] + PITCH // 2
        if _is_right:
            # posts sit right of board; brand (if any) sits right of posts
            right_edge = term_cx + TERM_R + (_brand_gap + _brand if _brand else 0)
        self._total_width = right_edge + MARGIN

        # --- Branding rect (only when show_branding=True) ---
        # Always placed on the outer side of the binding posts:
        #   left  → brand is left of posts  (between left canvas edge and post)
        #   right → brand is right of posts (between post and right canvas edge)
        #   top/bottom → brand is left of posts within the same margin strip
        _max_brand = TERM_R * 12
        if show_branding:
            if _is_left:
                bx = MARGIN
                bw = term_cx - TERM_R - _brand_gap - MARGIN
                bh = min(_max_brand, self.total_height - 2 * MARGIN)
                by = (self.total_height - bh) // 2
                self.branding_rect = wx.Rect(bx, by, max(4, bw), bh)
                self.branding_rotated = True
            elif _is_right:
                bx = term_cx + TERM_R + _brand_gap
                bw = _brand - _brand_gap
                bh = min(_max_brand, self.total_height - 2 * MARGIN)
                by = (self.total_height - bh) // 2
                self.branding_rect = wx.Rect(bx, by, max(4, bw), bh)
                self.branding_rotated = True
            elif _is_top:
                bh = top_extra - MARGIN // 2 - 4
                by = MARGIN // 2 + 2
                board_vis_right = board_right + PITCH // 2
                board_vis_left  = self.board_left - PITCH // 2
                if _h_align == 'right':
                    # Posts at right: branding sits between board left edge and posts
                    posts_left = min(pos[0] for pos in self._term_pos.values()) - TERM_R
                    bx = board_vis_left
                    bw = min(_max_brand, posts_left - bx - 8)
                else:
                    # Posts at left or center: branding sits between posts and board right edge
                    posts_right = max(pos[0] for pos in self._term_pos.values()) + TERM_R
                    bx = posts_right + 8
                    bw = min(_max_brand, board_vis_right - bx)
                self.branding_rect = wx.Rect(bx, by, max(4, bw), max(4, bh))
                self.branding_rotated = False
            else:  # bottom_*
                bh = bot_extra - MARGIN // 2 - 4
                by = self.total_height - bot_extra + MARGIN // 2 + 2
                board_vis_right = board_right + PITCH // 2
                board_vis_left  = self.board_left - PITCH // 2
                if _h_align == 'right':
                    posts_left = min(pos[0] for pos in self._term_pos.values()) - TERM_R
                    bx = board_vis_left
                    bw = min(_max_brand, posts_left - bx - 8)
                else:
                    posts_right = max(pos[0] for pos in self._term_pos.values()) + TERM_R
                    bx = posts_right + 8
                    bw = min(_max_brand, board_vis_right - bx)
                self.branding_rect = wx.Rect(bx, by, max(4, bw), max(4, bh))
                self.branding_rotated = False
        else:
            self.branding_rect: Optional[wx.Rect] = None
            self.branding_rotated = False

    # ------------------------------------------------------------------
    # sunny-11: bespoke geometry (transposed portrait blocks + a landscape block)
    # ------------------------------------------------------------------

    def _init_sunny11(self) -> None:
        self.columns = SUNNY11_UPPER_COLS
        self.sections = 3
        self.has_rails = True
        self.rail_len = 0
        self._vert_rail_cx: Dict[str, int] = {}
        self._vert_hole_y: List[int] = []
        self.branding_rect: Optional[wx.Rect] = None
        self.branding_rotated = False
        self._module_pin_xy: Dict[Tuple[str, int], Tuple[int, int]] = {}

        # --- Portrait blocks: row-letter axis -> x, column-number axis -> y ---
        # Pitch is the same PITCH used everywhere else on the board (upper and
        # lower, both axes) — SUNNY11_LOWER_COLS is chosen so the lower
        # block's natural width already lands close to the upper pair's,
        # rather than stretching one section's hole spacing to force a match.
        self._s11_row_pitch = PITCH
        bank_w = (len(TOP_ROWS) - 1) * PITCH
        self._s11_row_x: Dict[str, int] = {}
        for i, row in enumerate(TOP_ROWS):
            self._s11_row_x[row] = i * PITCH
        for i, row in enumerate(BOT_ROWS):
            self._s11_row_x[row] = bank_w + SUNNY11_GAP + i * PITCH
        block_w = bank_w + SUNNY11_GAP + bank_w
        self._s11_gap_upper = SUNNY11_GAP
        self._s11_gap_block_x = SUNNY11_BLOCK_GAP_X

        post_area_h = TERM_R * 2 + MARGIN * 3
        minus_y = post_area_h + MARGIN // 2
        plus_y  = minus_y + RAIL_H + 2
        tie_top_y = plus_y + RAIL_H + RAIL_GAP
        self._s11_col_y: Dict[int, int] = {
            col: tie_top_y + (col - 1) * PITCH for col in range(1, SUNNY11_UPPER_COLS + 1)
        }
        self._s11_plus_y  = plus_y
        self._s11_minus_y = minus_y
        self._s11_block_w = block_w
        self._s11_block_top = minus_y - RAIL_H
        upper_block_bottom = self._s11_col_y[SUNNY11_UPPER_COLS] + MARGIN // 2
        self._s11_block_bottom = upper_block_bottom

        # The lower block and the upper pair now land close in natural width
        # (see SUNNY11_LOWER_COLS), so centre whichever is wider and shift the
        # other to share that centre, rather than assuming which way round.
        lower_w = (SUNNY11_LOWER_COLS - 1) * PITCH
        lower_left = MARGIN
        lower_center = lower_left + lower_w // 2
        block0_left = max(MARGIN, lower_center - (block_w + SUNNY11_BLOCK_GAP_X // 2))
        self._s11_block_left: Dict[int, int] = {
            0: block0_left,
            1: block0_left + block_w + SUNNY11_BLOCK_GAP_X,
        }
        _row_order = list(ALL_ROWS)   # matches SUNNY11_UPPER_RAIL_LEN order
        self._s11_plus_rail_x: Dict[int, List[int]] = {
            section: [left + self._s11_row_x[r] for r in _row_order]
            for section, left in self._s11_block_left.items()
        }
        self._s11_minus_top_x: List[int] = self._s11_plus_rail_x[0] + self._s11_plus_rail_x[1]

        upper_total_w = self._s11_block_left[1] + block_w + MARGIN

        # --- Landscape lower block: standard orientation ---
        # Unlike the upper blocks (minus on top, plus nearer the tie rows),
        # the real board prints the lower block's rails the other way round:
        # plus (V3/V4) on top, minus nearer the tie rows.
        lower_top    = upper_block_bottom + SUNNY11_BLOCK_GAP_Y
        lower_plus_y  = lower_top
        lower_minus_y = lower_plus_y + RAIL_H + 2
        lower_tie_top = lower_minus_y + RAIL_H + RAIL_GAP
        self._s11_lower_col_x: Dict[int, int] = {
            col: lower_left + (col - 1) * PITCH for col in range(1, SUNNY11_LOWER_COLS + 1)
        }
        self._s11_lower_row_y: Dict[str, int] = {
            row: lower_tie_top + i * PITCH for i, row in enumerate(SUNNY11_LOWER_ROWS)
        }
        self._s11_lower_plus_y  = lower_plus_y
        self._s11_lower_minus_y = lower_minus_y

        # Same construction as the V1/V2 rails above (a 5-hole bank, gap,
        # 5-hole bank per half, and the same-size gap between halves),
        # mirrored horizontally — rather than a different scheme for the
        # bottom rails. Centred within the lower block's own width so the
        # V3/V4 gap still lines up with the groove between the V1/V2 blocks.
        def _half(start: int) -> List[int]:
            return ([start + i * PITCH for i in range(len(TOP_ROWS))]
                    + [start + bank_w + SUNNY11_GAP + i * PITCH for i in range(len(TOP_ROWS))])

        rail_half_w = block_w
        rail_total_w = 2 * rail_half_w + SUNNY11_BLOCK_GAP_X
        rail_start = lower_left + (lower_w - rail_total_w) // 2
        self._s11_lower_plus_x: Dict[str, List[int]] = {
            'lower_plus_left':  _half(rail_start),
            'lower_plus_right': _half(rail_start + rail_half_w + SUNNY11_BLOCK_GAP_X),
        }
        self._s11_lower_minus_x: List[int] = (
            self._s11_lower_plus_x['lower_plus_left'] + self._s11_lower_plus_x['lower_plus_right']
        )

        self._s11_lower_top = lower_top - RAIL_H
        lower_bottom  = self._s11_lower_row_y[SUNNY11_LOWER_ROWS[-1]] + MARGIN
        self._s11_lower_bottom = lower_bottom
        self._s11_lower_w = lower_w

        # --- Baseboard protrusion & consistent margins on every side ---
        # The formulas above only reserve trailing margin on the right and
        # bottom edges; the left edge lands only a hole's pitch away from the
        # canvas edge. Recompute the true content bounds and shift
        # everything so the board sits with a uniform, slightly generous
        # margin on all four sides instead.
        post_y_local = MARGIN + TERM_R
        content_left   = min(self._s11_block_left[0], self._s11_lower_col_x[1]) - PITCH
        content_right  = max(self._s11_block_left[1] + block_w,
                              self._s11_lower_col_x[SUNNY11_LOWER_COLS]) + PITCH
        content_top    = min(self._s11_block_top, post_y_local - TERM_R)
        content_bottom = lower_bottom

        outer_pad = MARGIN + 6    # more generous than the plain MARGIN other
                                  # layouts use, so the baseboard clearly
                                  # protrudes beyond the board on every side
        dx = outer_pad - content_left
        dy = outer_pad - content_top

        for col in self._s11_col_y:
            self._s11_col_y[col] += dy
        self._s11_plus_y  += dy
        self._s11_minus_y += dy
        self._s11_block_top    += dy
        self._s11_block_bottom += dy
        for section in self._s11_block_left:
            self._s11_block_left[section] += dx
        for section in self._s11_plus_rail_x:
            self._s11_plus_rail_x[section] = [x + dx for x in self._s11_plus_rail_x[section]]
        self._s11_minus_top_x = [x + dx for x in self._s11_minus_top_x]
        for col in self._s11_lower_col_x:
            self._s11_lower_col_x[col] += dx
        for row in self._s11_lower_row_y:
            self._s11_lower_row_y[row] += dy
        self._s11_lower_plus_y  += dy
        self._s11_lower_minus_y += dy
        for name in self._s11_lower_plus_x:
            self._s11_lower_plus_x[name] = [x + dx for x in self._s11_lower_plus_x[name]]
        self._s11_lower_minus_x = [x + dx for x in self._s11_lower_minus_x]
        self._s11_lower_top    += dy
        self._s11_lower_bottom += dy

        self._total_width = (content_right - content_left) + 2 * outer_pad
        self.total_height = (content_bottom - content_top) + 2 * outer_pad

        # --- 5 binding posts along the top edge (fixed; not user-configurable) ---
        # Evenly spaced left-to-right in GND/V1/V2/V3/V4 order, matching the
        # real hardware — the posts don't line up above their rail, since each
        # one wires down to it internally regardless of where it sits on top.
        n = len(TERMINAL_NAMES)
        v_margin = TERM_R + MARGIN
        spacing = (self._total_width - 2 * v_margin) // max(1, n - 1)
        post_y = post_y_local + dy
        self._term_pos: Dict[str, Tuple[int, int]] = {
            name: (v_margin + i * spacing, post_y) for i, name in enumerate(TERMINAL_NAMES)
        }
        self._term_y = {name: pos[1] for name, pos in self._term_pos.items()}

    def _sunny11_hole_xy(self, hole: Hole) -> Optional[Tuple[int, int]]:
        if isinstance(hole, TieHole):
            if hole.section in (0, 1):
                if hole.col not in self._s11_col_y or hole.row not in self._s11_row_x:
                    return None
                return (self._s11_block_left[hole.section] + self._s11_row_x[hole.row],
                        self._s11_col_y[hole.col])
            if hole.section == 2:
                if hole.col not in self._s11_lower_col_x or hole.row not in self._s11_lower_row_y:
                    return None
                return self._s11_lower_col_x[hole.col], self._s11_lower_row_y[hole.row]
            return None
        if isinstance(hole, RailHole):
            if hole.rail == 'top_plus':
                xs = self._s11_plus_rail_x.get(hole.section)
                if not xs or not (1 <= hole.index <= len(xs)):
                    return None
                return xs[hole.index - 1], self._s11_plus_y
            if hole.rail == 'sunny_top_minus':
                if not (1 <= hole.index <= len(self._s11_minus_top_x)):
                    return None
                return self._s11_minus_top_x[hole.index - 1], self._s11_minus_y
            if hole.rail in ('lower_plus_left', 'lower_plus_right'):
                xs = self._s11_lower_plus_x.get(hole.rail)
                if not xs or not (1 <= hole.index <= len(xs)):
                    return None
                return xs[hole.index - 1], self._s11_lower_plus_y
            if hole.rail == 'sunny_bot_minus':
                if not (1 <= hole.index <= len(self._s11_lower_minus_x)):
                    return None
                return self._s11_lower_minus_x[hole.index - 1], self._s11_lower_minus_y
            return None
        if isinstance(hole, Terminal):
            return self._term_pos.get(hole.name)
        if isinstance(hole, ModulePin):
            return self._module_pin_xy.get((hole.ref, hole.pin))
        return None

    def _sunny11_nearest(self, px: int, py: int, include_extra: bool = True) -> Optional[Hole]:
        """include_extra=False mirrors nearest_probe_hole: ties/rails only."""
        best: Optional[Hole] = None
        best_d = PITCH

        for section in (0, 1):
            left = self._s11_block_left[section]
            for row, rx_off in self._s11_row_x.items():
                rx = left + rx_off
                if abs(rx - px) > best_d:
                    continue
                for col, ry in self._s11_col_y.items():
                    d = math.hypot(rx - px, ry - py)
                    if d < best_d:
                        best_d = d
                        best = TieHole(col, row, section)

        for row, ry in self._s11_lower_row_y.items():
            if abs(ry - py) > best_d:
                continue
            for col, cx in self._s11_lower_col_x.items():
                d = math.hypot(cx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = TieHole(col, row, 2)

        for section, xs in self._s11_plus_rail_x.items():
            ry = self._s11_plus_y
            if abs(ry - py) > best_d:
                continue
            for idx, rx in enumerate(xs, 1):
                d = math.hypot(rx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole('top_plus', idx, section)

        ry = self._s11_minus_y
        if abs(ry - py) <= best_d:
            for idx, rx in enumerate(self._s11_minus_top_x, 1):
                d = math.hypot(rx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole('sunny_top_minus', idx)

        for name, xs in self._s11_lower_plus_x.items():
            ry = self._s11_lower_plus_y
            if abs(ry - py) > best_d:
                continue
            for idx, rx in enumerate(xs, 1):
                d = math.hypot(rx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole(name, idx, 2)

        ry = self._s11_lower_minus_y
        if abs(ry - py) <= best_d:
            for idx, rx in enumerate(self._s11_lower_minus_x, 1):
                d = math.hypot(rx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole('sunny_bot_minus', idx)

        if include_extra:
            for t_name in TERMINAL_NAMES:
                xy = self._term_pos.get(t_name)
                if xy:
                    d = math.hypot(xy[0] - px, xy[1] - py)
                    if d < best_d:
                        best_d = d
                        best = Terminal(t_name)
            for (ref, pin), (hx, hy) in self._module_pin_xy.items():
                d = math.hypot(hx - px, hy - py)
                if d < best_d:
                    best_d = d
                    best = ModulePin(ref=ref, pin=pin)

        return best

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def section_row_y(self, row: str, section: int) -> int:
        return self._section_top[section] + self._row_rel[row]

    def section_rail_y(self, rail: str, section: int) -> int:
        return self._section_top[section] + self._rail_rel[rail]

    def col_x(self, col: int) -> int:
        """x pixel of tie-strip column col (1-based)."""
        return self.board_left + (col - 1) * PITCH

    def rail_x(self, index: int) -> int:
        """x pixel of rail hole index (1-based).

        Holes sit on the column grid starting at col 2 (1-col inset each end), in
        groups of 5 with every 6th column left empty as a visual divider.  An extra
        2-column gap at RAIL_SPLIT gives a 3-column gap at the electrical mid-rail
        disconnect vs the normal 1-column inter-group gap.
        """
        col = ((index - 1) // 5) * 6 + (index - 1) % 5 + 2
        if self.rail_split and index > RAIL_SPLIT:
            col += 2
        return self.board_left + (col - 1) * PITCH

    def hole_xy(self, hole: Hole) -> Optional[Tuple[int, int]]:
        """Return (x, y) centre of a hole, or None if not renderable."""
        if self.board_layout == 'sunny-11':
            return self._sunny11_hole_xy(hole)
        if isinstance(hole, TieHole):
            s = hole.section
            if s >= self.sections:
                return None
            return self.col_x(hole.col), self.section_row_y(hole.row, s)
        if isinstance(hole, RailHole):
            if hole.rail in self._vert_rail_cx:
                cx = self._vert_rail_cx[hole.rail]
                if hole.index < 1 or hole.index > len(self._vert_hole_y):
                    return None
                return cx, self._vert_hole_y[hole.index - 1]
            if not self.has_rails:
                return None
            s = hole.section
            if s >= self.sections:
                return None
            return self.rail_x(hole.index), self.section_rail_y(hole.rail, s)
        if isinstance(hole, Terminal):
            return self._term_pos.get(hole.name)
        if isinstance(hole, ModulePin):
            return self._module_pin_xy.get((hole.ref, hole.pin))
        return None

    def total_width(self) -> int:
        return self._total_width

    # ------------------------------------------------------------------
    # Module pin coordinate management
    # ------------------------------------------------------------------

    def set_module_pin_xy(self, data: Dict[Tuple[str, int], Tuple[int, int]]) -> None:
        self._module_pin_xy.update(data)

    def clear_module_ref(self, ref: str) -> None:
        for k in [k for k in self._module_pin_xy if k[0] == ref]:
            del self._module_pin_xy[k]

    def nearest_hole(self, px: int, py: int) -> Optional[Hole]:
        """Return the hole closest to canvas pixel (px, py), within snap radius."""
        if self.board_layout == 'sunny-11':
            return self._sunny11_nearest(px, py, include_extra=True)
        best: Optional[Hole] = None
        best_d = PITCH

        # Tie strip holes — all sections
        for section in range(self.sections):
            for col in range(1, self.columns + 1):
                cx = self.col_x(col)
                if abs(cx - px) > best_d:
                    continue
                for row in ALL_ROWS:
                    ry = self.section_row_y(row, section)
                    d = math.hypot(cx - px, ry - py)
                    if d < best_d:
                        best_d = d
                        best = TieHole(col, row, section)

        # Rail holes — all sections (not present on mini)
        if self.has_rails:
            for section in range(self.sections):
                for rail in RAIL_NAMES:
                    ry = self.section_rail_y(rail, section)
                    if abs(ry - py) > best_d:
                        continue
                    for idx in range(1, RAIL_LEN + 1):
                        rx = self.rail_x(idx)
                        d = math.hypot(rx - px, ry - py)
                        if d < best_d:
                            best_d = d
                            best = RailHole(rail, idx, section)

        # Vertical rails (triple only)
        for rail, cx in self._vert_rail_cx.items():
            if abs(cx - px) > best_d:
                continue
            for idx, ry in enumerate(self._vert_hole_y, 1):
                d = math.hypot(cx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole(rail, idx)

        # Terminals
        for t_name in TERMINAL_NAMES:
            t = Terminal(t_name)
            xy = self.hole_xy(t)
            if xy:
                d = math.hypot(xy[0] - px, xy[1] - py)
                if d < best_d:
                    best_d = d
                    best = t

        # Module pins
        for (ref, pin), (hx, hy) in self._module_pin_xy.items():
            d = math.hypot(hx - px, hy - py)
            if d < best_d:
                best_d = d
                best = ModulePin(ref=ref, pin=pin)

        return best

    def nearest_probe_hole(self, px: int, py: int) -> Optional[Hole]:
        """Return the nearest TieHole or RailHole (no terminals), within snap radius."""
        if self.board_layout == 'sunny-11':
            return self._sunny11_nearest(px, py, include_extra=False)
        best: Optional[Hole] = None
        best_d = PITCH

        for section in range(self.sections):
            for col in range(1, self.columns + 1):
                cx = self.col_x(col)
                if abs(cx - px) > best_d:
                    continue
                for row in ALL_ROWS:
                    ry = self.section_row_y(row, section)
                    d = math.hypot(cx - px, ry - py)
                    if d < best_d:
                        best_d = d
                        best = TieHole(col, row, section)

        if self.has_rails:
            for section in range(self.sections):
                for rail in RAIL_NAMES:
                    ry = self.section_rail_y(rail, section)
                    if abs(ry - py) > best_d:
                        continue
                    for idx in range(1, RAIL_LEN + 1):
                        rx = self.rail_x(idx)
                        d = math.hypot(rx - px, ry - py)
                        if d < best_d:
                            best_d = d
                            best = RailHole(rail, idx, section)

        for rail, cx in self._vert_rail_cx.items():
            if abs(cx - px) > best_d:
                continue
            for idx, ry in enumerate(self._vert_hole_y, 1):
                d = math.hypot(cx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole(rail, idx)

        return best


# ---------------------------------------------------------------------------
# Ghost: preview of a component being dragged onto the canvas
# ---------------------------------------------------------------------------

@dataclass
class DragGhost:
    comp_def: ComponentDef
    ref: str
    anchor: Optional[TieHole] = None   # snapped hole for pin 1
    flipped: int = 0                   # DIP: 0/1; RPi modules: 0-3 (CW rotation)


# ---------------------------------------------------------------------------
# Annotation property dialogs
# ---------------------------------------------------------------------------

_ANNOTATION_SPIN_SIZE = (110, -1)


class _ShapePropsDialog(wx.Dialog):
    """Properties dialog for line, rectangle, and circle annotations."""

    def __init__(self, parent, title, *, has_fill=False,
                 color='#333333', width=2, fill=False, fill_color='#dddddd'):
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        gs = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        gs.AddGrowableCol(1)

        gs.Add(wx.StaticText(self, label='Line width:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._width = wx.SpinCtrl(self, value=str(width), min=1, max=10,
                                  size=_ANNOTATION_SPIN_SIZE)
        gs.Add(self._width, flag=wx.ALIGN_LEFT | wx.ALIGN_CENTER_VERTICAL)

        gs.Add(wx.StaticText(self, label='Color:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._color = wx.ColourPickerCtrl(self, colour=wx.Colour(color))
        gs.Add(self._color)

        self._fill_cb = None
        self._fill_color = None
        if has_fill:
            self._fill_cb = wx.CheckBox(self, label='Fill')
            self._fill_cb.SetValue(fill)
            gs.Add(self._fill_cb, flag=wx.ALIGN_CENTER_VERTICAL)
            self._fill_color = wx.ColourPickerCtrl(self, colour=wx.Colour(fill_color))
            gs.Add(self._fill_color)
            self._fill_cb.Bind(wx.EVT_CHECKBOX, self._on_fill_toggle)
            self._fill_color.Enable(fill)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(gs, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizerAndFit(sizer)
        self.CentreOnParent()

    def _on_fill_toggle(self, _evt):
        if self._fill_color:
            self._fill_color.Enable(self._fill_cb.GetValue())

    @property
    def line_width(self) -> int:
        return self._width.GetValue()

    @property
    def color(self) -> str:
        return self._color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)

    @property
    def fill(self) -> bool:
        return bool(self._fill_cb and self._fill_cb.GetValue())

    @property
    def fill_color(self) -> str:
        if self._fill_color:
            return self._fill_color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)
        return '#dddddd'


class _TextPropsDialog(wx.Dialog):
    """Properties dialog for text annotations."""

    def __init__(self, parent, *, text='', color='#222222',
                 font_size=11, bold=False, italic=False):
        super().__init__(parent, title='Text annotation',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        gs = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        gs.AddGrowableCol(1)

        gs.Add(wx.StaticText(self, label='Text:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._text = wx.TextCtrl(self, value=text, size=(220, -1))
        gs.Add(self._text, flag=wx.EXPAND)

        gs.Add(wx.StaticText(self, label='Font size:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._size = wx.SpinCtrl(self, value=str(font_size), min=6, max=72,
                                 size=_ANNOTATION_SPIN_SIZE)
        gs.Add(self._size, flag=wx.ALIGN_LEFT | wx.ALIGN_CENTER_VERTICAL)

        gs.Add(wx.StaticText(self, label='Color:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._color = wx.ColourPickerCtrl(self, colour=wx.Colour(color))
        gs.Add(self._color)

        gs.Add(wx.StaticText(self, label='Style:'), flag=wx.ALIGN_CENTER_VERTICAL)
        style_row = wx.BoxSizer(wx.HORIZONTAL)
        self._bold   = wx.CheckBox(self, label='Bold')
        self._italic = wx.CheckBox(self, label='Italic')
        self._bold.SetValue(bold)
        self._italic.SetValue(italic)
        style_row.Add(self._bold)
        style_row.AddSpacer(10)
        style_row.Add(self._italic)
        gs.Add(style_row)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(gs, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizerAndFit(sizer)
        self.CentreOnParent()
        self._text.SetFocus()

    @property
    def text(self) -> str:
        return self._text.GetValue().strip()

    @property
    def font_size(self) -> int:
        return self._size.GetValue()

    @property
    def color(self) -> str:
        return self._color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)

    @property
    def bold(self) -> bool:
        return self._bold.GetValue()

    @property
    def italic(self) -> bool:
        return self._italic.GetValue()


class _TextBoxPropsDialog(wx.Dialog):
    """Properties dialog for textbox annotations."""

    def __init__(self, parent, *, text='', color='#333333', width=1,
                 fill=True, fill_color='#fffbe6',
                 font_size=10, bold=False, italic=False, text_color='#222222'):
        super().__init__(parent, title='Text box',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        gs = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        gs.AddGrowableCol(1)

        gs.Add(wx.StaticText(self, label='Text:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._text = wx.TextCtrl(self, value=text, size=(220, 60),
                                 style=wx.TE_MULTILINE)
        gs.Add(self._text, flag=wx.EXPAND)

        gs.Add(wx.StaticText(self, label='Font size:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._size = wx.SpinCtrl(self, value=str(font_size), min=6, max=72,
                                 size=_ANNOTATION_SPIN_SIZE)
        gs.Add(self._size, flag=wx.ALIGN_LEFT | wx.ALIGN_CENTER_VERTICAL)

        gs.Add(wx.StaticText(self, label='Text color:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self._text_color = wx.ColourPickerCtrl(self, colour=wx.Colour(text_color))
        gs.Add(self._text_color)

        gs.Add(wx.StaticText(self, label='Style:'), flag=wx.ALIGN_CENTER_VERTICAL)
        style_row = wx.BoxSizer(wx.HORIZONTAL)
        self._bold   = wx.CheckBox(self, label='Bold')
        self._italic = wx.CheckBox(self, label='Italic')
        self._bold.SetValue(bold)
        self._italic.SetValue(italic)
        style_row.Add(self._bold)
        style_row.AddSpacer(10)
        style_row.Add(self._italic)
        gs.Add(style_row)

        gs.Add(wx.StaticLine(self, style=wx.LI_HORIZONTAL), 0,
               wx.EXPAND | wx.TOP | wx.BOTTOM, 4)
        gs.Add(wx.StaticLine(self, style=wx.LI_HORIZONTAL), 0,
               wx.EXPAND | wx.TOP | wx.BOTTOM, 4)

        gs.Add(wx.StaticText(self, label='Border:'), flag=wx.ALIGN_CENTER_VERTICAL)
        border_row = wx.BoxSizer(wx.HORIZONTAL)
        self._width = wx.SpinCtrl(self, value=str(width), min=0, max=10,
                                  size=_ANNOTATION_SPIN_SIZE)
        border_row.Add(wx.StaticText(self, label='w='), 0, wx.ALIGN_CENTER_VERTICAL)
        border_row.Add(self._width, 0)
        border_row.AddSpacer(8)
        self._color = wx.ColourPickerCtrl(self, colour=wx.Colour(color))
        border_row.Add(self._color, 0)
        gs.Add(border_row)

        self._fill_cb = wx.CheckBox(self, label='Fill')
        self._fill_cb.SetValue(fill)
        gs.Add(self._fill_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        self._fill_color = wx.ColourPickerCtrl(self, colour=wx.Colour(fill_color))
        gs.Add(self._fill_color)
        self._fill_cb.Bind(wx.EVT_CHECKBOX,
                           lambda _: self._fill_color.Enable(self._fill_cb.GetValue()))
        self._fill_color.Enable(fill)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(gs, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizerAndFit(sizer)
        self.CentreOnParent()
        self._text.SetFocus()

    @property
    def text(self) -> str:        return self._text.GetValue()
    @property
    def font_size(self) -> int:   return self._size.GetValue()
    @property
    def text_color(self) -> str:  return self._text_color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)
    @property
    def bold(self) -> bool:       return self._bold.GetValue()
    @property
    def italic(self) -> bool:     return self._italic.GetValue()
    @property
    def line_width(self) -> int:  return self._width.GetValue()
    @property
    def color(self) -> str:       return self._color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)
    @property
    def fill(self) -> bool:       return self._fill_cb.GetValue()
    @property
    def fill_color(self) -> str:  return self._fill_color.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)


# ---------------------------------------------------------------------------
# BreadboardCanvas
# ---------------------------------------------------------------------------

class BreadboardCanvas(wx.Panel):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.WANTS_CHARS)
        self.board = board
        self.netlist = netlist
        self.layout = CanvasLayout(board.layout, 'left')

        self.mode = MODE_SELECT
        self._wire_start: Optional[Hole] = None
        self._wire_color_idx = 0
        self._wire_color_fixed: Optional[str] = None   # None = cycle through WIRE_COLORS

        self._ghost: Optional[DragGhost] = None      # component pending placement
        self._ghost_pos: Tuple[int, int] = (0, 0)    # current mouse pos
        self._place_pin1: Optional[Hole] = None       # locked pin-1 hole for 2-pin two-step placement

        self._selected_ref: Optional[str] = None     # selected placed component
        self._selected_wire: Optional[Wire] = None   # selected wire
        self._selected_probe: Optional[str] = None   # selected probe label (for Delete key)
        self._hover_ref: Optional[str] = None         # hovered component (delete mode)
        self._hover_wire: Optional[Wire] = None       # hovered wire (delete mode)
        self._drag_comp: Optional[str] = None        # ref being repositioned on board
        self._drag_offset: Tuple[int, int] = (0, 0)  # mouse offset from pin-1 hole

        self._wire_bend_drag: bool = False            # currently dragging a wire bend handle
        self._wire_bend_candidate: Optional[Wire] = None  # wire that may start a bend drag
        self._wire_bend_start_mouse: Tuple[int, int] = (0, 0)
        self._wire_bend_pre_snap: Optional[dict] = None

        self._wire_end_drag_wire: Optional[Wire] = None   # wire whose endpoint is being moved
        self._wire_end_drag_which: Optional[str] = None   # 'h1' or 'h2'
        self._wire_end_drag_hole: Optional[object] = None # current snap target
        self._wire_end_drag_pre_snap: Optional[dict] = None

        self._pin_drag_ref:  Optional[str]  = None   # ref whose pin is being repositioned
        self._pin_drag_num:  Optional[int]  = None   # which pin number is being dragged
        self._pin_drag_hole: Optional[Hole] = None   # current snap target for that pin

        self._highlighted_holes: Set[Hole] = set()   # from validation
        self._highlight_kind: Optional[IssueKind] = None
        self._net_hl_holes: Set[Hole] = set()        # from net-highlight / net-probe mode
        self._net_hl_name:  str       = ''           # net name currently highlighted
        self._net_probe_cb: Optional[callable] = None  # callback for MODE_NET_PROBE
        self._scope_probes: Dict[int, tuple] = {}      # ch_idx → (hole, color_hex, label)
        self._net_label_rows: List[Tuple[int, int, int, int, str]] = []  # screen-space hit boxes

        self._annotations: List = []                 # DrawLine / DrawRect / DrawText / DrawCircle
        self._draw_start: Optional[Tuple[float, float]] = None   # in-progress shape first point
        self._draw_preview: Optional[Tuple[float, float]] = None  # live mouse pos
        self._hover_ann_idx: Optional[int] = None    # annotation under cursor in DELETE mode
        self._shape_defaults   = {'color': '#333333', 'width': 2, 'fill': False, 'fill_color': '#dddddd'}
        self._text_defaults    = {'color': '#222222', 'font_size': 11, 'bold': False, 'italic': False}
        self._textbox_defaults = {'color': '#333333', 'width': 1, 'fill': True, 'fill_color': '#fffbe6',
                                  'font_size': 10, 'bold': False, 'italic': False, 'text_color': '#222222'}
        self._drag_ann_idx: Optional[int] = None     # annotation being dragged in SELECT mode
        self._drag_ann_orig = None                   # copy of annotation at drag start
        self._drag_ann_start_mouse: Tuple[float, float] = (0.0, 0.0)
        self._drag_ann_pre_snap: Optional[dict] = None
        self._selected_ann_idx: Optional[int] = None # annotation showing resize handles
        self._resize_handle_idx: Optional[int] = None # handle being dragged (None = body drag)
        # (x, y, IssueKind) for each validation issue with locatable holes
        self._validation_icons: List[Tuple[int, int, IssueKind]] = []

        self.show_net_labels: bool = True    # toggled via preferences
        self.show_voltage_labels: bool = True  # toggled via SimPane checkbox
        self.show_binding_posts: bool = True # toggled via preferences
        self.show_baseboard: bool = False    # toggled via preferences
        self.show_branding: bool = False
        self.baseboard_color: str = '#3d6fa8'
        self.branding_image: str = ''
        self.rail_style: str = 'bbrd_classic'  # toggled via preferences

        self._placing_probe: Optional[str] = None   # probe name pending placement
        self._probe_drag: bool = False              # True = drag-to-place (release to commit)
        self._probe_hover: Optional[Hole] = None    # hovered hole in probe mode
        self._hover_probe_name: Optional[str] = None  # hovered placed probe (delete mode)
        self._dragging_probe_label: Optional[str] = None   # probe whose flag is being dragged
        self._drag_label_start_mouse: Tuple[int, int] = (0, 0)
        self._drag_label_start_offset: Tuple[int, int] = (0, 0)

        self._rpi_long_labels: bool = False   # toggle for RPi alt-function pin names
        self._dip_fn_labels:   bool = False   # toggle for DIP IC pin-function labels

        # Callbacks
        self.on_placed: Optional[callable] = None
        self.on_probe_placed: Optional[callable] = None  # called with probe name
        self.on_history_change: Optional[callable] = None  # called(can_undo, can_redo)
        self.on_restore: Optional[callable] = None  # called after undo/redo for full UI refresh
        self.on_terminal_right_click: Optional[callable] = None  # called(term_name, screen_pos)

        # Simulation overlay
        self._sim_result = None   # SimResult or None

        # Undo / redo
        self._undo_stack: list = []
        self._redo_stack: list = []
        self._drag_pre_snap: Optional[dict] = None   # snapshot saved at drag start

        # Zoom / pan state
        self._zoom: float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._pan_initialized: bool = False
        self._user_interacted: bool = False
        self._mid_drag: bool = False
        self._mid_drag_start: Tuple[int, int] = (0, 0)
        self._pan_at_drag_start: Tuple[float, float] = (0.0, 0.0)

        self.SetMinSize((400, 300))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_left_dclick)
        self.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right_down)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_mousewheel)
        self.Bind(wx.EVT_MIDDLE_DOWN, self._on_middle_down)
        self.Bind(wx.EVT_MIDDLE_UP, self._on_middle_up)
        self.Bind(wx.EVT_SIZE, self._on_size)

    # ------------------------------------------------------------------
    # Public API (called from window / tray)
    # ------------------------------------------------------------------

    def rebuild_layout(self) -> None:
        """Rebuild CanvasLayout from current board.layout and stored prefs."""
        self.layout = CanvasLayout(self.board.layout, self.layout.binding_post_side,
                                   self.show_branding)
        self._pan_initialized = False   # trigger re-fit on next paint
        self.Refresh()

    def reload_board(self, board: Breadboard) -> None:
        """Replace the active board and resync all derived state (module pins, etc.)."""
        self.board = board
        self._populate_module_pins()
        self.Refresh()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    @staticmethod
    def _ann_to_json(a) -> dict:
        if isinstance(a, DrawLine):
            return {'kind': 'line', 'x1': a.x1, 'y1': a.y1, 'x2': a.x2, 'y2': a.y2,
                    'color': a.color, 'width': a.width}
        if isinstance(a, DrawRect):
            return {'kind': 'rect', 'x1': a.x1, 'y1': a.y1, 'x2': a.x2, 'y2': a.y2,
                    'color': a.color, 'width': a.width,
                    'fill': a.fill, 'fill_color': a.fill_color}
        if isinstance(a, DrawText):
            return {'kind': 'text', 'x': a.x, 'y': a.y, 'text': a.text,
                    'color': a.color, 'font_size': a.font_size,
                    'bold': a.bold, 'italic': a.italic}
        if isinstance(a, DrawCircle):
            return {'kind': 'circle', 'cx': a.cx, 'cy': a.cy, 'r': a.r,
                    'color': a.color, 'width': a.width,
                    'fill': a.fill, 'fill_color': a.fill_color}
        if isinstance(a, DrawTextBox):
            return {'kind': 'textbox', 'x1': a.x1, 'y1': a.y1, 'x2': a.x2, 'y2': a.y2,
                    'text': a.text, 'color': a.color, 'width': a.width,
                    'fill': a.fill, 'fill_color': a.fill_color,
                    'font_size': a.font_size, 'bold': a.bold, 'italic': a.italic,
                    'text_color': a.text_color}
        return {}

    @staticmethod
    def _ann_from_json(d: dict):
        k = d.get('kind')
        if k == 'line':
            return DrawLine(d['x1'], d['y1'], d['x2'], d['y2'],
                            d.get('color', '#333333'), d.get('width', 2))
        if k == 'rect':
            return DrawRect(d['x1'], d['y1'], d['x2'], d['y2'],
                            d.get('color', '#333333'), d.get('width', 2),
                            d.get('fill', False), d.get('fill_color', '#dddddd'))
        if k == 'text':
            return DrawText(d['x'], d['y'], d['text'],
                            d.get('color', '#222222'), d.get('font_size', 11),
                            d.get('bold', False), d.get('italic', False))
        if k == 'circle':
            return DrawCircle(d['cx'], d['cy'], d['r'],
                              d.get('color', '#333333'), d.get('width', 2),
                              d.get('fill', False), d.get('fill_color', '#dddddd'))
        if k == 'textbox':
            return DrawTextBox(d['x1'], d['y1'], d['x2'], d['y2'],
                               text=d.get('text', ''), color=d.get('color', '#333333'),
                               width=d.get('width', 1), fill=d.get('fill', True),
                               fill_color=d.get('fill_color', '#fffbe6'),
                               font_size=d.get('font_size', 10), bold=d.get('bold', False),
                               italic=d.get('italic', False),
                               text_color=d.get('text_color', '#222222'))
        return None

    def _board_snapshot(self) -> dict:
        """Capture the full mutable board state as a JSON-serialisable dict."""
        from .model.session import _hole_to_json
        b = self.board
        return {
            'terminals': {n: net for n in TERMINAL_NAMES
                          if (net := b.get_terminal_net(n))},
            'placements': [
                {'ref': ref, 'type_id': p.type_id, 'flipped': p.flipped,
                 'led_color': p.led_color,
                 'pins': [[pn, _hole_to_json(h)] for pn, h in sorted(p.pin_holes.items())]}
                for ref, p in b.placements.items()
            ],
            'wires': [
                {'h1': _hole_to_json(w.h1), 'h2': _hole_to_json(w.h2), 'color': w.color,
                 **({'mid': list(w.mid_point)} if w.mid_point else {})}
                for w in b.wires
            ],
            'probes': {
                n: {
                    'hole': _hole_to_json(b.get_probe_hole(n)) if b.get_probe_hole(n) else None,
                    'net': b.get_probe_net(n),
                    'offset': list(b.get_probe_label_offset(n)),
                }
                for n in PROBE_NAMES
                if b.get_probe_hole(n) is not None or b.get_probe_net(n)
            },
            'module_positions': {ref: list(pos)
                                  for ref, pos in b.module_positions.items()},
            'annotations': [self._ann_to_json(a) for a in self._annotations],
        }

    def _restore_snapshot(self, snap: dict) -> None:
        """Overwrite the current board state with a snapshot dict (in-place)."""
        from .model.session import _hole_from_json
        b = self.board
        b._placements.clear()
        b._wires.clear()
        b._terminal_nets.clear()
        for n in PROBE_NAMES:
            b._probe_holes[n] = None
            b._probe_nets[n] = ''
            b._probe_offsets[n] = (0, 0)
        b._module_positions.clear()

        for name, net in snap.get('terminals', {}).items():
            b.assign_terminal(name, net)
        for p in snap.get('placements', []):
            pin_holes = {int(pn): _hole_from_json(h) for pn, h in p['pins']}
            b.place(PlacedComponent(
                ref=p['ref'], type_id=p['type_id'],
                pin_holes=pin_holes, flipped=p['flipped'],
                led_color=p.get('led_color', ''),
            ))
        for w in snap.get('wires', []):
            wire = b.add_wire(_hole_from_json(w['h1']), _hole_from_json(w['h2']), w['color'])
            if 'mid' in w and len(w['mid']) == 2:
                wire.mid_point = (int(w['mid'][0]), int(w['mid'][1]))
        for name, info in snap.get('probes', {}).items():
            if info.get('hole'):
                b.place_probe(name, _hole_from_json(info['hole']))
            if info.get('net'):
                b.assign_probe_net(name, info['net'])
            off = info.get('offset')
            if off and len(off) == 2:
                b.set_probe_label_offset(name, int(off[0]), int(off[1]))
        for ref, pos in snap.get('module_positions', {}).items():
            b.set_module_position(ref, int(pos[0]), int(pos[1]))
        self._annotations = [a for d in snap.get('annotations', [])
                              if (a := self._ann_from_json(d)) is not None]

        self._populate_module_pins()

    def push_undo(self) -> None:
        """Save current board state to undo stack and clear the redo stack."""
        self._undo_stack.append(self._board_snapshot())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._notify_history()

    def _notify_history(self) -> None:
        if self.on_history_change:
            self.on_history_change(bool(self._undo_stack), bool(self._redo_stack))

    def clear_history(self) -> None:
        """Discard undo/redo history (call after session load)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify_history()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._board_snapshot())
        self._restore_snapshot(self._undo_stack.pop())
        self._post_restore()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self._board_snapshot())
        self._restore_snapshot(self._redo_stack.pop())
        self._post_restore()

    def _post_restore(self) -> None:
        """Reset transient UI state after undo/redo and trigger a full refresh."""
        self._selected_ref = None
        self._selected_wire = None
        self._selected_probe = None
        self._selected_ann_idx = None
        self._drag_comp = None
        self._drag_pre_snap = None
        self._ghost = None
        self._wire_start = None
        self._pin_drag_ref = None
        self._pin_drag_num = None
        self._pin_drag_hole = None
        self._notify_history()
        if self.on_placed:
            self.on_placed('')
        if self.on_restore:
            self.on_restore()
        self.Refresh()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._wire_start = None
        self._ghost = None
        self._place_pin1 = None
        self._selected_wire = None
        self._selected_ref = None
        self._selected_probe = None
        self._selected_ann_idx = None
        self._pin_drag_ref  = None
        self._pin_drag_num  = None
        self._pin_drag_hole = None
        self._wire_end_drag_wire = None
        self._wire_end_drag_which = None
        self._wire_end_drag_hole = None
        self._wire_end_drag_pre_snap = None
        if mode != MODE_PROBE:
            self._placing_probe = None
            self._probe_drag = False
            self._probe_hover = None
        if mode != MODE_DELETE:
            self._hover_probe_name = None
        if mode not in (MODE_NET_HIGHLIGHT, MODE_NET_PROBE):
            self._net_hl_holes = set()
            self._net_hl_name  = ''
        if mode != MODE_NET_PROBE:
            self._net_probe_cb = None
        if mode not in _DRAW_MODES:
            self._draw_start = None
            self._draw_preview = None
        if mode != MODE_DELETE:
            self._hover_ann_idx = None
        self.Refresh()

    def begin_probe_place(self, probe_name: str) -> None:
        """Start probe placement mode — next hole click places the probe."""
        self.set_mode(MODE_PROBE)
        self._placing_probe = probe_name
        self._probe_drag = False
        self.SetFocus()

    def set_scope_probe(self, ch_idx: int, color_hex: str, label: str, hole) -> None:
        self._scope_probes[ch_idx] = (hole, color_hex, label)
        self.Refresh()

    def clear_scope_probe(self, ch_idx: int) -> None:
        self._scope_probes.pop(ch_idx, None)
        self.Refresh()

    def clear_all_scope_probes(self) -> None:
        self._scope_probes.clear()
        self.Refresh()

    def begin_net_probe(self, callback) -> None:
        """Enter net-probe mode; callback(net_name, hole) is called on each hole click."""
        self._net_probe_cb = callback
        self.set_mode(MODE_NET_PROBE)
        self.SetCursor(wx.Cursor(wx.CURSOR_CROSS))
        self.SetFocus()

    def end_net_probe(self) -> None:
        if self.mode == MODE_NET_PROBE:
            self.set_mode(MODE_SELECT)
            self._net_hl_holes = set()
            self._net_hl_name = ''
            self.SetCursor(wx.NullCursor)
            self.Refresh()

    def begin_probe_drag(self, probe_name: str) -> None:
        """Start probe placement via drag — release over a hole to place."""
        self.push_undo()
        self.board.remove_probe(probe_name)
        self.board.assign_probe_net(probe_name, '')   # clear so placement re-detects net
        self.set_mode(MODE_PROBE)
        self._placing_probe = probe_name
        self._probe_drag = True
        self.CaptureMouse()
        self.SetFocus()

    def delete_selection(self) -> None:
        """Delete whichever object is currently selected."""
        if self._selected_wire is not None:
            self.push_undo()
            self.board.remove_wire(self._selected_wire)
            self._selected_wire = None
            self.Refresh()
        elif self._selected_ref is not None:
            self.push_undo()
            self.layout.clear_module_ref(self._selected_ref)
            self.board.remove(self._selected_ref)
            if self.on_placed:
                self.on_placed(self._selected_ref)
            self._selected_ref = None
            self.Refresh()
        elif self._selected_probe is not None:
            self.push_undo()
            self.board.remove_probe(self._selected_probe)
            if self.on_probe_placed:
                self.on_probe_placed(self._selected_probe)
            self._selected_probe = None
            self.Refresh()
        elif self._selected_ann_idx is not None:
            if 0 <= self._selected_ann_idx < len(self._annotations):
                self.push_undo()
                self._annotations.pop(self._selected_ann_idx)
            self._selected_ann_idx = None
            self._hover_ann_idx = None
            self._drag_ann_idx = None
            self._drag_ann_orig = None
            self._drag_ann_pre_snap = None
            self._resize_handle_idx = None
            self.Refresh()

    def begin_place(self, comp_def: ComponentDef, ref: str) -> None:
        """Called from the tray when a component card is clicked."""
        self._ghost = DragGhost(comp_def=comp_def, ref=ref)
        self._place_pin1 = None
        self.SetFocus()   # so key events (Escape) reach the canvas
        self.Refresh()

    def _commit_place(self, px: int, py: int) -> bool:
        """Place the current ghost at canvas position (px, py). Returns True on success."""
        if self._ghost is None:
            return False
        comp_def = self._ghost.comp_def
        ref = self._ghost.ref

        # Free-floating module: place at raw canvas position (no snap needed)
        if comp_def.is_module:
            self.push_undo()
            mx, my = int(px), int(py)
            self.board.set_module_position(ref, mx, my)
            pin_holes = {pin: ModulePin(ref=ref, pin=pin)
                         for pin in comp_def.pin_offsets}
            placed = PlacedComponent(ref=ref, type_id=comp_def.type_id,
                                     pin_holes=pin_holes, flipped=self._ghost.flipped)
            self.board.place(placed)
            self._sync_module_pins(ref)
            self._ghost = None
            if self.on_placed:
                self.on_placed(ref)
            self.Refresh()
            return True

        clicked = self.layout.nearest_hole(px, py)
        if clicked is None:
            return False

        # Two-pin non-DIP components use a two-step click flow.
        # They can land on any hole (tie strip OR power rail).
        if comp_def.pin_count == 2 and not comp_def.is_dip:
            if self._place_pin1 is None:
                # First click: lock pin 1, keep ghost active for pin 2
                self._place_pin1 = clicked
                self.Refresh()
                return True
            else:
                # Second click: place with both pins.
                # For diode-family parts pin 1=K, pin 2=A — first click anchors A (pin 2).
                self.push_undo()
                if comp_def.type_id in ('LED', 'D', 'D_Zener'):
                    pin_holes = {2: self._place_pin1, 1: clicked}
                else:
                    pin_holes = {1: self._place_pin1, 2: clicked}
                led_color = comp_def.color if comp_def.type_id == 'LED' else ''
                placed = PlacedComponent(ref=ref, type_id=comp_def.type_id,
                                         pin_holes=pin_holes, flipped=False,
                                         led_color=led_color)
                self.board.place(placed)
                self._ghost = None
                self._place_pin1 = None
                if self.on_placed:
                    self.on_placed(ref)
                self.Refresh()
                return True

        # Single-click placement (DIP, 3-pin, etc.) — anchor must be a tie hole
        if not isinstance(clicked, TieHole):
            return False
        flipped = self._ghost.flipped
        try:
            pin_holes = comp_def.place(clicked, flipped=flipped)
        except (AssertionError, IndexError, KeyError):
            return False
        self.push_undo()
        placed = PlacedComponent(ref=ref, type_id=comp_def.type_id,
                                 pin_holes=pin_holes, flipped=flipped)
        self.board.place(placed)
        self._ghost = None
        if self.on_placed:
            self.on_placed(ref)
        self.Refresh()
        return True

    def _highlight_net_by_name(self, net_name: str) -> None:
        """Highlight all board holes belonging to the named schematic net."""
        if not self.netlist:
            return
        seed_holes: Set[Hole] = set()
        for net in self.netlist.nets:
            if net.name == net_name:
                for pn in net.pins:
                    h = self.board.hole_for_pin(pn.ref, pn.pin)
                    if h is not None:
                        seed_holes.add(h)
        if seed_holes:
            uf = self.board.build_connectivity()
            connected: Set[Hole] = set()
            for h in seed_holes:
                root = uf.find(h)
                connected.update(x for x in uf._parent if uf.find(x) == root)
            self._net_hl_holes = connected
        else:
            self._net_hl_holes = set()
        self._net_hl_name = net_name

    def _hl_net_name_from_holes(self) -> str:
        """Return the schematic net name for the currently highlighted holes."""
        if not self.netlist or not self._net_hl_holes:
            return ''
        for net in self.netlist.nets:
            for pn in net.pins:
                h = self.board.hole_for_pin(pn.ref, pn.pin)
                if h in self._net_hl_holes:
                    return net.name
        return ''

    def set_highlighted(self, holes: Set[Hole], kind: Optional[IssueKind] = None) -> None:
        self._highlighted_holes = holes
        self._highlight_kind = kind
        self.Refresh()

    def clear_highlights(self) -> None:
        self._highlighted_holes = set()
        self._highlight_kind = None
        self._validation_icons.clear()
        self._sim_result = None
        self.Refresh()

    def set_validation_result(self, result) -> None:
        """Store validation issues; position icons at the relevant component."""
        all_holes: Set[Hole] = set()
        self._validation_icons.clear()
        hole_set = set()
        for issue in result.issues:
            if not issue.holes:
                continue
            hole_set = set(issue.holes)
            icon_xy = None
            # Prefer a placed-component pin hole so the icon sits on the board
            for placed in self.board.placements.values():
                for hole in placed.pin_holes.values():
                    if hole in hole_set:
                        xy = self.layout.hole_xy(hole)
                        if xy:
                            icon_xy = xy
                            break
                if icon_xy:
                    break
            if icon_xy is None:
                # Fallback: centroid of all renderable holes
                xys = [self.layout.hole_xy(h) for h in issue.holes
                       if self.layout.hole_xy(h) is not None]
                if xys:
                    icon_xy = (sum(x for x, y in xys) // len(xys),
                               sum(y for x, y in xys) // len(xys))
            if icon_xy:
                # Offset upward so the badge doesn't cover the hole dot
                self._validation_icons.append((icon_xy[0], icon_xy[1] - 14, issue.kind))
            all_holes.update(issue.holes)
        self._highlighted_holes = all_holes
        self._highlight_kind = result.issues[0].kind if result.issues else None
        self.Refresh()

    def set_simulation_result(self, result) -> None:
        self._sim_result = result
        self.Refresh()

    def clear_simulation(self) -> None:
        self._sim_result = None
        self.Refresh()

    def _led_forward_voltage(self, ref: str) -> Optional[float]:
        """Return simulated V_anode - V_cathode for a placed LED, or None."""
        if self._sim_result is None or not self._sim_result.net_voltages:
            return None
        if self.netlist is None:
            return None
        nets = self.netlist.nets_for_ref(ref)
        net_k = nets.get(1)   # pin 1 = K
        net_a = nets.get(2)   # pin 2 = A
        if net_k is None or net_a is None:
            return None
        v_k = self._sim_result.net_voltages.get(net_k.name)
        v_a = self._sim_result.net_voltages.get(net_a.name)
        if v_k is None or v_a is None:
            return None
        return v_a - v_k

    def set_rpi_long_labels(self, long: bool) -> None:
        self._rpi_long_labels = long
        self.Refresh()

    def set_dip_fn_labels(self, on: bool) -> None:
        self._dip_fn_labels = on
        self.Refresh()

    def set_wire_color(self, color: Optional[str]) -> None:
        """Set a fixed wire color hex string, or None to cycle automatically."""
        self._wire_color_fixed = color

    def next_wire_color(self) -> str:
        if self._wire_color_fixed is not None:
            return self._wire_color_fixed
        c = WIRE_COLORS[self._wire_color_idx % len(WIRE_COLORS)]
        self._wire_color_idx += 1
        return c

    def _board_pos(self, px: int, py: int) -> Tuple[float, float]:
        """Convert a window-pixel mouse position to board-pixel coordinates."""
        return (px - self._pan_x) / self._zoom, (py - self._pan_y) / self._zoom

    def _fit_view(self) -> None:
        """Reset zoom and pan so the board fits centred in the window."""
        cw, ch = self.GetClientSize()
        if cw <= 0 or ch <= 0:
            return
        bw = self.layout.total_width()
        bh = self.layout.total_height
        _pad = 12
        self._zoom = min((cw - _pad * 2) / bw, (ch - _pad * 2) / bh)
        self._pan_x = (cw - bw * self._zoom) / 2
        self._pan_y = (ch - bh * self._zoom) / 2
        self.Refresh()

    def zoom_center(self, factor: float) -> None:
        """Zoom in (factor > 1) or out (factor < 1) centred on the canvas."""
        cw, ch = self.GetClientSize()
        cx, cy = cw / 2, ch / 2
        new_zoom = max(0.15, min(5.0, self._zoom * factor))
        scale = new_zoom / self._zoom
        self._pan_x = cx - (cx - self._pan_x) * scale
        self._pan_y = cy - (cy - self._pan_y) * scale
        self._zoom = new_zoom
        self.Refresh()

    def _on_size(self, _evt) -> None:
        if not self._user_interacted:
            self._pan_initialized = False
        self.Refresh()

    # ------------------------------------------------------------------
    # Mouse event handlers
    # ------------------------------------------------------------------

    def _on_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self._ghost = None
            self._place_pin1 = None
            self._wire_start = None
            self._drag_comp = None
            self._selected_wire = None
            self._draw_start = None
            self._draw_preview = None
            self.Refresh()
        elif key in (ord('R'), ord('r')):
            # Rotate during placement, or rotate selected component.
            # 2-pin: only before pin1 is locked (flips the step-1 preview direction).
            if self._ghost is not None and (
                    self._ghost.comp_def.pin_count != 2 or self._place_pin1 is None):
                cd = self._ghost.comp_def
                n_rots = 4 if cd.is_module else 2
                self._ghost.flipped = (self._ghost.flipped + 1) % n_rots
                self.Refresh()
            elif self._selected_ref is not None:
                self._flip_component(self._selected_ref)
        elif key in (wx.WXK_DELETE, wx.WXK_BACK):
            self.delete_selection()
        elif key == wx.WXK_HOME and evt.ControlDown():
            self._fit_view()
        elif key in (ord('+'), ord('='), wx.WXK_NUMPAD_ADD):
            self.zoom_center(1.2)
        elif key in (ord('-'), wx.WXK_NUMPAD_SUBTRACT):
            self.zoom_center(1 / 1.2)
        else:
            evt.Skip()

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())

        # Placement mode: ghost is active — click to place, anywhere to cancel wire
        if self._ghost is not None:
            self._commit_place(px, py)
            return

        if self.mode == MODE_WIRE:
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                if self._wire_start is None:
                    self._wire_start = hole
                else:
                    if hole != self._wire_start:
                        self.push_undo()
                        self.board.add_wire(self._wire_start, hole,
                                            color=self.next_wire_color())
                    self._wire_start = None
                    self.Refresh()
            return

        if self.mode == MODE_PROBE and self._placing_probe:
            hole = self.layout.nearest_probe_hole(px, py)
            if hole is not None:
                self.push_undo()
                self.board.place_probe(self._placing_probe, hole)
                name = self._placing_probe
                self._placing_probe = None
                self.set_mode(MODE_SELECT)
                if self.on_probe_placed:
                    self.on_probe_placed(name)
                self.Refresh()
            return

        if self.mode == MODE_DELETE:
            self._try_delete(px, py)
            return

        if self.mode == MODE_NET_HIGHLIGHT:
            # Hit-test the net-labels overlay (screen space) before board coords
            sx, sy = evt.GetPosition()
            for rx, ry, rw, rh, row_net in self._net_label_rows:
                if rx <= sx <= rx + rw and ry <= sy <= ry + rh:
                    self._highlight_net_by_name(row_net)
                    self.Refresh()
                    return
            # Fall back to hole proximity (board space)
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                if isinstance(hole, Terminal):
                    # Terminal binding posts are not in the UnionFind — look up
                    # their assigned schematic net and highlight from there.
                    net = self.board.get_terminal_net(hole.name)
                    if net:
                        self._highlight_net_by_name(net)
                        self._net_hl_holes.add(hole)
                    else:
                        self._net_hl_holes = set()
                        self._net_hl_name  = ''
                else:
                    uf = self.board.build_connectivity()
                    root = uf.find(hole)
                    self._net_hl_holes = {h for h in uf._parent if uf.find(h) == root}
                    self._net_hl_name  = self._hl_net_name_from_holes()
            else:
                self._net_hl_holes = set()
                self._net_hl_name  = ''
            self.Refresh()
            return

        if self.mode == MODE_NET_PROBE:
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                if isinstance(hole, Terminal):
                    net = self.board.get_terminal_net(hole.name)
                    if net:
                        self._highlight_net_by_name(net)
                        self._net_hl_holes.add(hole)
                        self._net_hl_name = net
                        if self._net_probe_cb:
                            self._net_probe_cb(net, hole)
                else:
                    uf = self.board.build_connectivity()
                    root = uf.find(hole)
                    self._net_hl_holes = {h for h in uf._parent if uf.find(h) == root}
                    self._net_hl_name  = self._hl_net_name_from_holes()
                    if self._net_hl_name and self._net_probe_cb:
                        self._net_probe_cb(self._net_hl_name, hole)
            self.Refresh()
            return

        if self.mode == MODE_DRAW_TEXTBOX:
            if self._draw_start is None:
                self._draw_start = (px, py)
                self._draw_preview = (px, py)
            else:
                x1, y1 = self._draw_start
                if abs(px - x1) > 4 or abs(py - y1) > 4:
                    dlg = _TextBoxPropsDialog(self.GetTopLevelParent(),
                                              **self._textbox_defaults)
                    if dlg.ShowModal() == wx.ID_OK:
                        self._textbox_defaults.update({
                            'color': dlg.color, 'width': dlg.line_width,
                            'fill': dlg.fill, 'fill_color': dlg.fill_color,
                            'font_size': dlg.font_size, 'bold': dlg.bold,
                            'italic': dlg.italic, 'text_color': dlg.text_color,
                        })
                        if dlg.text:
                            self.push_undo()
                            self._annotations.append(DrawTextBox(
                                x1, y1, px, py, text=dlg.text,
                                color=dlg.color, width=dlg.line_width,
                                fill=dlg.fill, fill_color=dlg.fill_color,
                                font_size=dlg.font_size, bold=dlg.bold,
                                italic=dlg.italic, text_color=dlg.text_color))
                    dlg.Destroy()
                self._draw_start = None
                self._draw_preview = None
                if self._annotations:
                    self._selected_ann_idx = len(self._annotations) - 1
                self.set_mode(MODE_SELECT)
            self.Refresh()
            return

        if self.mode in (MODE_DRAW_LINE, MODE_DRAW_RECT, MODE_DRAW_CIRCLE):
            if self._draw_start is None:
                self._draw_start = (px, py)
                self._draw_preview = (px, py)
            else:
                x1, y1 = self._draw_start
                if abs(px - x1) > 2 or abs(py - y1) > 2:
                    if self.mode == MODE_DRAW_CIRCLE:
                        r = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
                        dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Circle properties', has_fill=True,
                                                color=self._shape_defaults['color'],
                                                width=self._shape_defaults['width'],
                                                fill=self._shape_defaults['fill'],
                                                fill_color=self._shape_defaults['fill_color'])
                        if dlg.ShowModal() == wx.ID_OK:
                            self._shape_defaults.update({'color': dlg.color, 'width': dlg.line_width,
                                                         'fill': dlg.fill, 'fill_color': dlg.fill_color})
                            self.push_undo()
                            self._annotations.append(DrawCircle(x1, y1, r,
                                color=dlg.color, width=dlg.line_width,
                                fill=dlg.fill, fill_color=dlg.fill_color))
                        dlg.Destroy()
                    elif self.mode == MODE_DRAW_LINE:
                        dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Line properties',
                                                color=self._shape_defaults['color'],
                                                width=self._shape_defaults['width'])
                        if dlg.ShowModal() == wx.ID_OK:
                            self._shape_defaults.update({'color': dlg.color, 'width': dlg.line_width})
                            self.push_undo()
                            self._annotations.append(DrawLine(x1, y1, px, py,
                                color=dlg.color, width=dlg.line_width))
                        dlg.Destroy()
                    else:  # DRAW_RECT
                        dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Rectangle properties', has_fill=True,
                                                color=self._shape_defaults['color'],
                                                width=self._shape_defaults['width'],
                                                fill=self._shape_defaults['fill'],
                                                fill_color=self._shape_defaults['fill_color'])
                        if dlg.ShowModal() == wx.ID_OK:
                            self._shape_defaults.update({'color': dlg.color, 'width': dlg.line_width,
                                                         'fill': dlg.fill, 'fill_color': dlg.fill_color})
                            self.push_undo()
                            self._annotations.append(DrawRect(x1, y1, px, py,
                                color=dlg.color, width=dlg.line_width,
                                fill=dlg.fill, fill_color=dlg.fill_color))
                        dlg.Destroy()
                self._draw_start = None
                self._draw_preview = None
                if self._annotations:
                    self._selected_ann_idx = len(self._annotations) - 1
                self.set_mode(MODE_SELECT)
            self.Refresh()
            return

        if self.mode == MODE_DRAW_TEXT:
            dlg = _TextPropsDialog(self.GetTopLevelParent(),
                                   color=self._text_defaults['color'],
                                   font_size=self._text_defaults['font_size'],
                                   bold=self._text_defaults['bold'],
                                   italic=self._text_defaults['italic'])
            if dlg.ShowModal() == wx.ID_OK and dlg.text:
                self._text_defaults.update({'color': dlg.color, 'font_size': dlg.font_size,
                                            'bold': dlg.bold, 'italic': dlg.italic})
                self.push_undo()
                self._annotations.append(DrawText(px, py, dlg.text,
                    color=dlg.color, font_size=dlg.font_size,
                    bold=dlg.bold, italic=dlg.italic))
                self._selected_ann_idx = len(self._annotations) - 1
                self.set_mode(MODE_SELECT)
            dlg.Destroy()
            self.Refresh()
            return

        if self.mode == MODE_SELECT:
            label_name = self._probe_label_at(px, py)
            if label_name:
                self._dragging_probe_label = label_name
                self._drag_label_start_mouse = (px, py)
                self._drag_label_start_offset = self.board.get_probe_label_offset(label_name)
                self._selected_ref = None
                self._selected_wire = None
                self._selected_probe = label_name
                # Do NOT call CaptureMouse here — on GTK it fires a synthetic
                # motion event at (0,0) which causes a huge coordinate jump.
                self.Refresh()
                return
            pin_hit = self._pin_at(px, py)
            if pin_hit:
                ref, pin_num = pin_hit
                placed = self.board.get_placement(ref)
                self._drag_pre_snap = self._board_snapshot()
                self._pin_drag_ref  = ref
                self._pin_drag_num  = pin_num
                self._pin_drag_hole = placed.pin_holes.get(pin_num) if placed else None
                self._selected_ref  = ref
                self._selected_wire = None
                self._selected_probe = None
                self.SetFocus()
                self.Refresh()
                self.CaptureMouse()
                return
            ref = self._comp_at(px, py)
            if ref:
                self._drag_pre_snap = self._board_snapshot()
                self._selected_ref = ref
                self._selected_wire = None
                self._selected_probe = None
                self._drag_comp = ref
                p = self.board.get_placement(ref)
                if p:
                    comp_def = ALL_DEFS.get(p.type_id)
                    if comp_def and comp_def.is_module:
                        mod_pos = self.board.get_module_position(ref)
                        if mod_pos:
                            self._drag_offset = (px - mod_pos[0], py - mod_pos[1])
                    else:
                        pin1_hole = p.pin_holes.get(1)
                        if pin1_hole:
                            xy = self.layout.hole_xy(pin1_hole)
                            if xy:
                                self._drag_offset = (px - xy[0], py - xy[1])
                self.SetFocus()   # grab focus so Delete key reaches the canvas
                self.Refresh()
                self.CaptureMouse()
            else:
                import copy as _copy
                # Check wire endpoints first — takes priority over body-click/bend
                end_hit = self._wire_end_at(px, py)
                if end_hit:
                    wire, which = end_hit
                    self._wire_end_drag_wire = wire
                    self._wire_end_drag_which = which
                    self._wire_end_drag_hole = getattr(wire, which)
                    self._wire_end_drag_pre_snap = self._board_snapshot()
                    self._selected_wire = wire
                    self._selected_ref = None
                    self._selected_probe = None
                    self._selected_ann_idx = None
                    self.SetFocus()
                    if not self.HasCapture():
                        self.CaptureMouse()
                    self.Refresh()
                    return
                wire = self._wire_at(px, py)
                self._selected_wire = wire
                self._selected_ref = None
                self._selected_probe = None
                if wire:
                    self._selected_ann_idx = None
                    self.SetFocus()
                    # Track for potential bend drag
                    self._wire_bend_candidate = wire
                    self._wire_bend_start_mouse = (px, py)
                    self._wire_bend_pre_snap = self._board_snapshot()
                    # Capture at mouse-down so the SplitterWindow sash never
                    # receives stray events during the drag.
                    if not self.HasCapture():
                        self.CaptureMouse()
                else:
                    # Check resize handles of currently selected annotation first
                    h_idx = None
                    if self._selected_ann_idx is not None:
                        h_idx = self._handle_at(self._selected_ann_idx, px, py)
                    if h_idx is not None:
                        # Start a resize drag on the selected annotation
                        self._drag_ann_idx = self._selected_ann_idx
                        self._drag_ann_orig = _copy.copy(self._annotations[self._selected_ann_idx])
                        self._drag_ann_start_mouse = (px, py)
                        self._drag_ann_pre_snap = self._board_snapshot()
                        self._resize_handle_idx = h_idx
                        self.SetFocus()
                    else:
                        ann_idx = self._ann_at(px, py)
                        if ann_idx is not None:
                            self._selected_ann_idx = ann_idx
                            self._drag_ann_idx = ann_idx
                            self._drag_ann_orig = _copy.copy(self._annotations[ann_idx])
                            self._drag_ann_start_mouse = (px, py)
                            self._drag_ann_pre_snap = self._board_snapshot()
                            self._resize_handle_idx = None
                            self.SetFocus()
                        else:
                            self._selected_ann_idx = None
                self.Refresh()

    def _on_left_dclick(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())
        if self.mode == MODE_SELECT:
            wire = self._wire_at(px, py)
            if wire and wire.mid_point:
                self.push_undo()
                wire.mid_point = None
                self.Refresh()
                return
            ann_idx = self._ann_at(px, py)
            if ann_idx is not None:
                self._edit_annotation(ann_idx)
                return
        evt.Skip()

    def _edit_annotation(self, idx: int) -> None:
        """Open property dialog to edit an existing annotation in-place."""
        ann = self._annotations[idx]
        if isinstance(ann, DrawLine):
            dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Line properties',
                                    color=ann.color, width=ann.width)
            if dlg.ShowModal() == wx.ID_OK:
                self.push_undo()
                ann.color = dlg.color
                ann.width = dlg.line_width
            dlg.Destroy()
        elif isinstance(ann, DrawRect):
            dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Rectangle properties', has_fill=True,
                                    color=ann.color, width=ann.width,
                                    fill=ann.fill, fill_color=ann.fill_color)
            if dlg.ShowModal() == wx.ID_OK:
                self.push_undo()
                ann.color = dlg.color
                ann.width = dlg.line_width
                ann.fill = dlg.fill
                ann.fill_color = dlg.fill_color
            dlg.Destroy()
        elif isinstance(ann, DrawCircle):
            dlg = _ShapePropsDialog(self.GetTopLevelParent(), 'Circle properties', has_fill=True,
                                    color=ann.color, width=ann.width,
                                    fill=ann.fill, fill_color=ann.fill_color)
            if dlg.ShowModal() == wx.ID_OK:
                self.push_undo()
                ann.color = dlg.color
                ann.width = dlg.line_width
                ann.fill = dlg.fill
                ann.fill_color = dlg.fill_color
            dlg.Destroy()
        elif isinstance(ann, DrawText):
            dlg = _TextPropsDialog(self.GetTopLevelParent(), text=ann.text, color=ann.color,
                                   font_size=ann.font_size, bold=ann.bold, italic=ann.italic)
            if dlg.ShowModal() == wx.ID_OK and dlg.text:
                self.push_undo()
                ann.text = dlg.text
                ann.color = dlg.color
                ann.font_size = dlg.font_size
                ann.bold = dlg.bold
                ann.italic = dlg.italic
            dlg.Destroy()
        elif isinstance(ann, DrawTextBox):
            dlg = _TextBoxPropsDialog(self.GetTopLevelParent(),
                                      text=ann.text, color=ann.color, width=ann.width,
                                      fill=ann.fill, fill_color=ann.fill_color,
                                      font_size=ann.font_size, bold=ann.bold,
                                      italic=ann.italic, text_color=ann.text_color)
            if dlg.ShowModal() == wx.ID_OK:
                self.push_undo()
                ann.text = dlg.text
                ann.color = dlg.color
                ann.width = dlg.line_width
                ann.fill = dlg.fill
                ann.fill_color = dlg.fill_color
                ann.font_size = dlg.font_size
                ann.bold = dlg.bold
                ann.italic = dlg.italic
                ann.text_color = dlg.text_color
            dlg.Destroy()
        self.Refresh()

    def _on_left_up(self, evt: wx.MouseEvent) -> None:
        if self.mode == MODE_PROBE and self._probe_drag and self._placing_probe:
            if self.HasCapture():
                self.ReleaseMouse()
            px, py = self._board_pos(*evt.GetPosition())
            hole = self.layout.nearest_probe_hole(px, py)
            if hole is not None:
                # undo was already pushed by begin_probe_drag (which removed the probe)
                self.board.place_probe(self._placing_probe, hole)
                if self.on_probe_placed:
                    self.on_probe_placed(self._placing_probe)
            self._placing_probe = None
            self._probe_drag = False
            self._probe_hover = None
            self.set_mode(MODE_SELECT)
            self.Refresh()
            return
        if self._dragging_probe_label:
            self._dragging_probe_label = None
            self.Refresh()
            return
        if self._wire_end_drag_wire is not None:
            if self.HasCapture():
                self.ReleaseMouse()
            wire   = self._wire_end_drag_wire
            which  = self._wire_end_drag_which
            target = self._wire_end_drag_hole
            old_hole = getattr(wire, which)
            if target is not None and target != old_hole:
                setattr(wire, which, target)
                if self._wire_end_drag_pre_snap is not None:
                    self._undo_stack.append(self._wire_end_drag_pre_snap)
                    if len(self._undo_stack) > 50:
                        self._undo_stack.pop(0)
                    self._redo_stack.clear()
                    self._notify_history()
            self._wire_end_drag_wire = None
            self._wire_end_drag_which = None
            self._wire_end_drag_hole = None
            self._wire_end_drag_pre_snap = None
            self.Refresh()
            return
        if self._wire_bend_drag:
            if self.HasCapture():
                self.ReleaseMouse()
            self._wire_bend_drag = False
            self._wire_bend_candidate = None
            if self._wire_bend_pre_snap is not None:
                cur = self._board_snapshot()
                if self._wire_bend_pre_snap != cur:
                    self._undo_stack.append(self._wire_bend_pre_snap)
                    if len(self._undo_stack) > 50:
                        self._undo_stack.pop(0)
                    self._redo_stack.clear()
                    self._notify_history()
                self._wire_bend_pre_snap = None
            self.Refresh()
            return
        # Clear bend candidate on any mouse-up (click without drag)
        if self._wire_bend_candidate and self.HasCapture():
            self.ReleaseMouse()
        self._wire_bend_candidate = None
        self._wire_bend_pre_snap = None
        if self._drag_ann_idx is not None:
            if self._drag_ann_pre_snap is not None:
                cur = self._board_snapshot()
                if self._drag_ann_pre_snap != cur:
                    self._undo_stack.append(self._drag_ann_pre_snap)
                    if len(self._undo_stack) > 50:
                        self._undo_stack.pop(0)
                    self._redo_stack.clear()
                    self._notify_history()
            self._drag_ann_idx = None
            self._drag_ann_orig = None
            self._drag_ann_pre_snap = None
            self._resize_handle_idx = None
            self.Refresh()
            return
        if self._pin_drag_ref is not None:
            if self.HasCapture():
                self.ReleaseMouse()
            self._commit_pin_drag()
            self._pin_drag_ref  = None
            self._pin_drag_num  = None
            self._pin_drag_hole = None
            self.Refresh()
            return
        if self.HasCapture():
            self.ReleaseMouse()
        if self._drag_comp:
            px, py = self._board_pos(*evt.GetPosition())
            px -= self._drag_offset[0]
            py -= self._drag_offset[1]
            p = self.board.get_placement(self._drag_comp)
            comp_def = ALL_DEFS.get(p.type_id) if p else None
            if comp_def and comp_def.is_module:
                mx, my = int(px), int(py)
                self.board.set_module_position(self._drag_comp, mx, my)
                self._sync_module_pins(self._drag_comp)
            else:
                new_anchor = self.layout.nearest_hole(px, py)
                if p and comp_def:
                    if comp_def.pin_count == 2 and not comp_def.is_dip:
                        # Preserve orientation (diagonal or rail-connected) by keeping the
                        # pixel offset between the two pins and snapping pin2 to the nearest
                        # hole at that translated position.  Works for TieHole, RailHole, or
                        # Terminal on either pin — no assumption about hole types.
                        old_p1_xy = self.layout.hole_xy(p.pin_holes.get(1))
                        old_p2_xy = self.layout.hole_xy(p.pin_holes.get(2))
                        new_p1_xy = self.layout.hole_xy(new_anchor) if new_anchor else None
                        if old_p1_xy and old_p2_xy and new_p1_xy:
                            dx = old_p2_xy[0] - old_p1_xy[0]
                            dy = old_p2_xy[1] - old_p1_xy[1]
                            new_p2 = self.layout.nearest_hole(new_p1_xy[0] + dx,
                                                              new_p1_xy[1] + dy)
                            if new_p2 is not None:
                                p.pin_holes = {1: new_anchor, 2: new_p2}
                        elif isinstance(new_anchor, TieHole):
                            try:
                                p.pin_holes = comp_def.place(new_anchor, flipped=p.flipped)
                            except (AssertionError, IndexError, KeyError):
                                pass
                    elif isinstance(new_anchor, TieHole):
                        try:
                            p.pin_holes = comp_def.place(new_anchor, flipped=p.flipped)
                        except (AssertionError, IndexError, KeyError):
                            pass
            # Push undo only if the drag actually moved the component
            if self._drag_pre_snap is not None:
                if self._drag_pre_snap != self._board_snapshot():
                    self._undo_stack.append(self._drag_pre_snap)
                    if len(self._undo_stack) > 50:
                        self._undo_stack.pop(0)
                    self._redo_stack.clear()
                    self._notify_history()
                self._drag_pre_snap = None
            self._drag_comp = None
            self.Refresh()

    def _on_mousewheel(self, evt: wx.MouseEvent) -> None:
        self._user_interacted = True
        rotation = evt.GetWheelRotation()
        # KiCad-style controls:
        #   Scroll alone      → zoom (cursor-centred)
        #   Shift + scroll    → vertical pan
        #   Ctrl  + scroll    → horizontal pan
        PAN_STEP = 60   # pixels per wheel notch
        if evt.ShiftDown():
            self._pan_y += PAN_STEP if rotation > 0 else -PAN_STEP
        elif evt.ControlDown():
            self._pan_x += PAN_STEP if rotation > 0 else -PAN_STEP
        else:
            cx, cy = evt.GetPosition()
            factor = 1.12 if rotation > 0 else (1.0 / 1.12)
            new_zoom = max(0.15, min(5.0, self._zoom * factor))
            scale = new_zoom / self._zoom
            self._pan_x = cx - (cx - self._pan_x) * scale
            self._pan_y = cy - (cy - self._pan_y) * scale
            self._zoom = new_zoom
        self.Refresh()

    def _on_middle_down(self, evt: wx.MouseEvent) -> None:
        self._user_interacted = True
        self._mid_drag = True
        pos = evt.GetPosition()
        self._mid_drag_start = (pos.x, pos.y)
        self._pan_at_drag_start = (self._pan_x, self._pan_y)
        if not self.HasCapture():
            self.CaptureMouse()
        self.SetCursor(wx.Cursor(wx.CURSOR_SIZING))

    def _on_middle_up(self, evt: wx.MouseEvent) -> None:
        if self._mid_drag:
            self._mid_drag = False
            if self.HasCapture():
                self.ReleaseMouse()
            self.SetCursor(wx.Cursor(wx.CURSOR_ARROW))

    def _on_motion(self, evt: wx.MouseEvent) -> None:
        if self._mid_drag:
            pos = evt.GetPosition()
            dx = pos.x - self._mid_drag_start[0]
            dy = pos.y - self._mid_drag_start[1]
            self._pan_x = self._pan_at_drag_start[0] + dx
            self._pan_y = self._pan_at_drag_start[1] + dy
            self.Refresh()
            return
        px, py = self._board_pos(*evt.GetPosition())

        if self._dragging_probe_label:
            if not evt.LeftIsDown():
                # Button released without firing LEFT_UP (e.g. focus change)
                self._dragging_probe_label = None
                self.Refresh()
                return
            mx0, my0 = self._drag_label_start_mouse
            ox, oy   = self._drag_label_start_offset
            self.board.set_probe_label_offset(
                self._dragging_probe_label,
                int(round(ox + (px - mx0))),
                int(round(oy + (py - my0))))
            self.Refresh()
            return

        if self._drag_ann_idx is not None:
            if not evt.LeftIsDown():
                self._drag_ann_idx = None
                self._drag_ann_orig = None
                self._drag_ann_pre_snap = None
                self._resize_handle_idx = None
                self.Refresh()
                return
            orig = self._drag_ann_orig
            ann = self._annotations[self._drag_ann_idx]
            if self._resize_handle_idx is not None:
                self._apply_resize(ann, self._resize_handle_idx, px, py)
            else:
                sx, sy = self._drag_ann_start_mouse
                dx, dy = px - sx, py - sy
                if isinstance(ann, DrawLine):
                    ann.x1 = orig.x1 + dx; ann.y1 = orig.y1 + dy
                    ann.x2 = orig.x2 + dx; ann.y2 = orig.y2 + dy
                elif isinstance(ann, DrawRect):
                    ann.x1 = orig.x1 + dx; ann.y1 = orig.y1 + dy
                    ann.x2 = orig.x2 + dx; ann.y2 = orig.y2 + dy
                elif isinstance(ann, DrawCircle):
                    ann.cx = orig.cx + dx; ann.cy = orig.cy + dy
                elif isinstance(ann, DrawText):
                    ann.x = orig.x + dx; ann.y = orig.y + dy
            self.Refresh()
            return

        # Live-move a dragged module
        if self._drag_comp:
            p = self.board.get_placement(self._drag_comp)
            comp_def = ALL_DEFS.get(p.type_id) if p else None
            if comp_def and comp_def.is_module:
                mx = int(px - self._drag_offset[0])
                my = int(py - self._drag_offset[1])
                self.board.set_module_position(self._drag_comp, mx, my)
                self._sync_module_pins(self._drag_comp)
            elif comp_def and comp_def.pin_count >= 3 and not comp_def.is_dip:
                # Live snap for TO-92: move all legs together during drag
                tpx = px - self._drag_offset[0]
                tpy = py - self._drag_offset[1]
                new_anchor = self.layout.nearest_hole(tpx, tpy)
                if isinstance(new_anchor, TieHole):
                    try:
                        p.pin_holes = comp_def.place(new_anchor, flipped=p.flipped)
                    except (AssertionError, IndexError, KeyError):
                        pass
            self.Refresh()
            return

        # Wire endpoint drag: snap the dragged end to the nearest hole
        if self._wire_end_drag_wire is not None:
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                self._wire_end_drag_hole = hole
            self.Refresh()
            return

        # Wire bend drag: upgrade candidate to active drag once mouse moves enough
        if self._wire_bend_candidate and not self._wire_bend_drag:
            dx = px - self._wire_bend_start_mouse[0]
            dy = py - self._wire_bend_start_mouse[1]
            if dx * dx + dy * dy > 16:
                self._wire_bend_drag = True
        if self._wire_bend_drag and self._selected_wire is not None:
            self._selected_wire.mid_point = (int(px), int(py))
            self.Refresh()
            return

        # Pin lead drag: update snap target
        if self._pin_drag_ref is not None:
            placed = self.board.get_placement(self._pin_drag_ref)
            comp_def = ALL_DEFS.get(placed.type_id) if placed else None
            if comp_def:
                hole = self.layout.nearest_hole(px, py)
                if hole is not None:
                    self._pin_drag_hole = hole   # any hole type
            self.Refresh()
            return

        self._ghost_pos = (px, py)
        if self._ghost:
            comp_def = self._ghost.comp_def
            if comp_def.is_module:
                pass  # module ghost always follows mouse; no snap needed
            else:
                anchor = self.layout.nearest_hole(px, py)
                if comp_def.pin_count == 2 and not comp_def.is_dip:
                    self._ghost.anchor = anchor  # accept tie strip or power rail
                else:
                    self._ghost.anchor = anchor if isinstance(anchor, TieHole) else None
        if self.mode == MODE_PROBE:
            self._probe_hover = self.layout.nearest_probe_hole(px, py)
        if self.mode == MODE_NET_PROBE:
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                uf = self.board.build_connectivity()
                root = uf.find(hole)
                self._net_hl_holes = {h for h in uf._parent if uf.find(h) == root}
                self._net_hl_name  = self._hl_net_name_from_holes()
            else:
                self._net_hl_holes = set()
                self._net_hl_name  = ''
        if self.mode in _DRAW_MODES and self._draw_start is not None:
            self._draw_preview = (px, py)
        if self.mode == MODE_DELETE:
            self._hover_ref = self._comp_at(px, py)
            self._hover_wire = self._wire_at(px, py) if not self._hover_ref else None
            self._hover_probe_name = None
            if not self._hover_ref and not self._hover_wire:
                self._hover_probe_name = self._probe_label_at(px, py)
            self._hover_ann_idx = self._ann_at(px, py) if not self._hover_ref and not self._hover_wire and not self._hover_probe_name else None
        else:
            self._hover_ref = None
            self._hover_wire = None
            self._hover_probe_name = None
            self._hover_ann_idx = None
        self.Refresh()

    def _on_right_down(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())
        # Right-click on a binding post terminal → net assignment menu
        if self.on_terminal_right_click and self.show_binding_posts:
            for t_name, (tx, ty) in self.layout._term_pos.items():
                if (px - tx) ** 2 + (py - ty) ** 2 <= TERM_R ** 2:
                    self.on_terminal_right_click(t_name, evt.GetPosition())
                    return
        # Right-click on a placed DIP or 3-pin component → flip it
        ref = self._comp_at(px, py)
        if ref:
            placed = self.board.get_placement(ref)
            comp_def = ALL_DEFS.get(placed.type_id) if placed else None
            if comp_def and (comp_def.is_dip or comp_def.is_module or comp_def.pin_count >= 3):
                self._flip_component(ref)
                return
        # Otherwise cancel the current operation
        self._wire_start = None
        self._ghost = None
        self._place_pin1 = None
        self._drag_comp = None
        self._pin_drag_ref  = None
        self._pin_drag_num  = None
        self._pin_drag_hole = None
        if self.HasCapture():
            self.ReleaseMouse()
        self.Refresh()

    def _flip_component(self, ref: str) -> None:
        """Flip a placed component (modules: mirror left/right; DIP/3-pin: rotate 180°)."""
        placed = self.board.get_placement(ref)
        if not placed:
            return
        comp_def = ALL_DEFS.get(placed.type_id)
        if not comp_def:
            return
        self.push_undo()

        if comp_def.is_module:
            placed.flipped = (placed.flipped + 1) % 4
            self._sync_module_pins(ref)
            self.Refresh()
            return

        pin1 = placed.pin_holes.get(1)
        if not isinstance(pin1, TieHole):
            return
        new_flipped = not placed.flipped
        if comp_def.is_dip:
            n = comp_def.footprint_cols() - 1
            new_anchor = TieHole(pin1.col + (n if new_flipped else -n), 'e', pin1.section)
        elif comp_def.pin_count >= 3:
            n = comp_def.pin_count - 1   # span = pin_count - 1
            new_anchor = TieHole(pin1.col + (n if new_flipped else -n), pin1.row, pin1.section)
        elif comp_def.pin_count == 2:
            # For 2-pin axial: use pin2 as new anchor and toggle flipped.
            # place(pin2, flipped=True)  → pin1 at pin2.col, pin2 at pin2.col-span
            # place(pin2, flipped=False) → restores original orientation
            pin2 = placed.pin_holes.get(2)
            if not isinstance(pin2, TieHole):
                return
            new_anchor = pin2
            # new_flipped already set to `not placed.flipped` above
        else:
            return
        try:
            placed.pin_holes = comp_def.place(new_anchor, flipped=new_flipped)
            placed.flipped = new_flipped
        except (AssertionError, IndexError, KeyError):
            pass
        self.Refresh()

    # ------------------------------------------------------------------
    # Hit testing helpers
    # ------------------------------------------------------------------

    def _comp_at(self, px: int, py: int) -> Optional[str]:
        """Return ref of the component whose body contains pixel (px, py)."""
        for ref, p in self.board.placements.items():
            comp_def = ALL_DEFS.get(p.type_id)
            holes_xy = [self.layout.hole_xy(h) for h in p.pin_holes.values()]
            holes_xy = [xy for xy in holes_xy if xy is not None]
            if not holes_xy:
                continue
            xs = [xy[0] for xy in holes_xy]
            ys = [xy[1] for xy in holes_xy]
            if comp_def and comp_def.is_module:
                # Use generous padding to cover the full board body, not just GPIO pins
                pad_x, pad_y = 90, 120
            elif comp_def and comp_def.pin_count >= 3 and not comp_def.is_dip:
                # TO-92 dome extends r_body=12px beyond the pin row
                pad_x, pad_y = 6, 14
            else:
                pad_x, pad_y = 6, 10
            if (min(xs) - pad_x <= px <= max(xs) + pad_x and
                    min(ys) - pad_y <= py <= max(ys) + pad_y):
                return ref
        return None

    def _pin_at(self, px: int, py: int) -> Optional[Tuple[str, int]]:
        """Return (ref, pin_num) if (px,py) is within click radius of a draggable pin lead.

        Only considers 2-pin and 3-pin non-DIP, non-module components.
        """
        _HIT_R = HOLE_R + 8   # ~11 px
        best_d = _HIT_R
        best: Optional[Tuple[str, int]] = None
        for ref, placed in self.board.placements.items():
            comp_def = ALL_DEFS.get(placed.type_id)
            if comp_def is None or comp_def.is_module or comp_def.is_dip:
                continue
            if comp_def.pin_count != 2:
                continue
            for pin_num, hole in placed.pin_holes.items():
                xy = self.layout.hole_xy(hole)
                if xy is None:
                    continue
                d = math.hypot(px - xy[0], py - xy[1])
                if d < best_d:
                    best_d = d
                    best = (ref, pin_num)
        return best

    def _wire_at(self, px: int, py: int) -> Optional[Wire]:
        """Return the wire closest to pixel (px, py), within click tolerance."""
        TOLERANCE = 6
        best_wire = None
        best_d = float('inf')
        for w in self.board.wires:
            xy1 = self.layout.hole_xy(w.h1)
            xy2 = self.layout.hole_xy(w.h2)
            if xy1 is None or xy2 is None:
                continue
            if w.mid_point:
                mid = w.mid_point
                d = min(_point_to_segment_dist(px, py, xy1[0], xy1[1], mid[0], mid[1]),
                        _point_to_segment_dist(px, py, mid[0], mid[1], xy2[0], xy2[1]))
            else:
                d = _point_to_segment_dist(px, py, xy1[0], xy1[1], xy2[0], xy2[1])
            if d < TOLERANCE and d < best_d:
                best_d = d
                best_wire = w
        return best_wire

    def _wire_end_at(self, px: int, py: int):
        """Return (wire, 'h1'|'h2') if (px,py) is near a wire endpoint, else None."""
        HIT_R = HOLE_R + 8
        best_d = HIT_R
        best = None
        for w in self.board.wires:
            for attr in ('h1', 'h2'):
                xy = self.layout.hole_xy(getattr(w, attr))
                if xy is None:
                    continue
                d = math.hypot(px - xy[0], py - xy[1])
                if d < best_d:
                    best_d = d
                    best = (w, attr)
        return best

    def _probe_label_at(self, px: int, py: int) -> Optional[str]:
        """Return the name of the placed probe whose icon contains (px, py)."""
        body_w, body_h, tip_h = 28, 12, 5
        for name in PROBE_NAMES:
            hole = self.board.get_probe_hole(name)
            if hole is None:
                continue
            xy = self.layout.hole_xy(hole)
            if xy is None:
                continue
            hx, hy = int(xy[0]), int(xy[1])
            fcx, fcy = self._probe_flag_pos(name, hx, hy)
            body_left = fcx - body_w // 2
            if (body_left <= px <= body_left + body_w and
                    fcy <= py <= fcy + body_h + tip_h):
                return name
        return None

    def _ann_at(self, px: float, py: float, tol: float = 8.0) -> Optional[int]:
        """Return the index of the annotation closest to (px, py), or None."""
        for i, ann in enumerate(self._annotations):
            if isinstance(ann, DrawLine):
                if _point_to_segment_dist(px, py, ann.x1, ann.y1, ann.x2, ann.y2) < tol:
                    return i
            elif isinstance(ann, DrawRect):
                x1, y1 = min(ann.x1, ann.x2), min(ann.y1, ann.y2)
                x2, y2 = max(ann.x1, ann.x2), max(ann.y1, ann.y2)
                if ann.fill and x1 <= px <= x2 and y1 <= py <= y2:
                    return i
                edges = [(x1, y1, x2, y1), (x2, y1, x2, y2),
                         (x2, y2, x1, y2), (x1, y2, x1, y1)]
                if any(_point_to_segment_dist(px, py, *e) < tol for e in edges):
                    return i
            elif isinstance(ann, DrawCircle):
                dist = ((px - ann.cx) ** 2 + (py - ann.cy) ** 2) ** 0.5
                if ann.fill and dist <= ann.r + tol:
                    return i
                if abs(dist - ann.r) < tol:
                    return i
            elif isinstance(ann, DrawText):
                char_w, line_h = 7, 14
                tw = len(ann.text) * char_w
                if ann.x <= px <= ann.x + tw and ann.y <= py <= ann.y + line_h:
                    return i
            elif isinstance(ann, DrawTextBox):
                x1, y1 = min(ann.x1, ann.x2), min(ann.y1, ann.y2)
                x2, y2 = max(ann.x1, ann.x2), max(ann.y1, ann.y2)
                if x1 <= px <= x2 and y1 <= py <= y2:
                    return i
        return None

    def _try_delete(self, px: int, py: int) -> None:
        ref = self._comp_at(px, py)
        if ref:
            self.push_undo()
            self.layout.clear_module_ref(ref)
            self.board.remove(ref)
            if self._selected_ref == ref:
                self._selected_ref = None
            if self.on_placed:
                self.on_placed(ref)
            self.Refresh()
            return
        # Check for probe markers
        name = self._probe_label_at(px, py)
        if name:
            self.push_undo()
            self.board.remove_probe(name)
            if self.on_probe_placed:
                self.on_probe_placed(name)
            self.Refresh()
            return
        w = self._wire_at(px, py)
        if w:
            self.push_undo()
            self.board.remove_wire(w)
            self.Refresh()
            return
        idx = self._ann_at(px, py)
        if idx is not None:
            self.push_undo()
            self._annotations.pop(idx)
            self._hover_ann_idx = None
            self._selected_ann_idx = None
            self.Refresh()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def render_to_bitmap(self) -> 'wx.Bitmap':
        """Render the full board to an off-screen bitmap (for PNG export)."""
        w = self.layout.total_width()
        h = self.layout.total_height
        bmp = wx.Bitmap(w, h)
        mdc = wx.MemoryDC(bmp)
        mdc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)))
        mdc.Clear()
        self._draw_board(mdc)
        mdc.SelectObject(wx.NullBitmap)
        return bmp

    def render_to_svg(self, path: str) -> None:
        """Render the full board to an SVG file."""
        w = self.layout.total_width()
        h = self.layout.total_height
        svg_dc = wx.SVGFileDC(path, w, h)
        self._draw_board(svg_dc)
        del svg_dc   # flushes and closes the file

    def _on_paint(self, _evt) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)))
        dc.Clear()
        if not self._pan_initialized:
            self._fit_view()
            self._pan_initialized = True
        dc.SetUserScale(self._zoom, self._zoom)
        dc.SetDeviceOrigin(int(self._pan_x), int(self._pan_y))
        self._draw_board(dc)

    def _draw_board(self, dc: wx.DC) -> None:
        lay = self.layout

        if self.show_baseboard:
            self._draw_baseboard(dc)

        if lay.board_layout == 'sunny-11':
            self._draw_board_sunny11(dc)
        else:
            # Per-section board bodies, rails, holes
            for section in range(lay.sections):
                self._draw_section_body(dc, section)
                if lay.has_rails:
                    self._draw_rails(dc, section)
                self._draw_center_gap(dc, section)
                self._draw_holes(dc, section)

            if lay.board_layout in ('triple', 'double_rails'):
                self._draw_vert_rails(dc)

        self._draw_components(dc)
        if self._pin_drag_ref is not None:
            self._draw_pin_drag_preview(dc)
        self._draw_wires(dc)
        if self._wire_end_drag_wire is not None:
            self._draw_wire_end_drag_preview(dc)
        if self.show_binding_posts:
            self._draw_terminals(dc)
        self._draw_probes(dc)
        self._draw_scope_probes(dc)

        if self._ghost:
            self._draw_ghost(dc)

        if self._wire_start:
            self._draw_wire_start_indicator(dc)

        self._draw_annotations(dc)
        if lay.board_layout != 'sunny-11':
            self._draw_column_labels(dc)
        self._draw_validation_icons(dc)
        self._draw_sim_overlay(dc)

        # Legend is drawn in screen coordinates (reset transform first)
        dc.SetUserScale(1.0, 1.0)
        dc.SetDeviceOrigin(0, 0)
        self._draw_net_labels(dc)

    def _draw_baseboard(self, dc: wx.DC) -> None:
        lay = self.layout

        # The layout already reserves MARGIN on every side and allocates extra
        # space for top/bottom binding posts, so the full canvas extent is the
        # correct baseboard boundary.
        base_rect = wx.Rect(0, 0, lay.total_width(), lay.total_height)

        color = self.baseboard_color
        try:
            c = wx.Colour(color)
            if not c.IsOk():
                c = wx.Colour('#1a3a6a')
        except Exception:
            c = wx.Colour('#1a3a6a')
        border_c = wx.Colour(
            min(255, c.Red()   + 30),
            min(255, c.Green() + 30),
            min(255, c.Blue()  + 30),
        )
        ring_c = wx.Colour(
            min(255, c.Red()   + 60),
            min(255, c.Green() + 60),
            min(255, c.Blue()  + 60),
        )
        shadow_c = wx.Colour(
            max(0, c.Red()   - 45),
            max(0, c.Green() - 45),
            max(0, c.Blue()  - 45),
        )
        w, h = base_rect.width, base_rect.height

        # Main flat body
        dc.SetBrush(wx.Brush(c))
        dc.SetPen(wx.Pen(border_c, 2))
        dc.DrawRoundedRectangle(base_rect, 12)

        # Drop-shadow strips on bottom and right inner edges (same idea as terminal shadow)
        SH, inset = 4, 10
        dc.SetBrush(wx.Brush(shadow_c))
        dc.SetPen(wx.Pen(shadow_c, 0))
        dc.DrawRoundedRectangle(inset, h - inset - SH, w - 2 * inset, SH, 2)   # bottom
        dc.DrawRoundedRectangle(w - inset - SH, inset, SH, h - 2 * inset, 2)   # right

        # Inner highlight ring on all edges (the line the user confirmed they like)
        dc.SetBrush(wx.Brush(c))
        dc.SetPen(wx.Pen(ring_c, 2))
        dc.DrawRoundedRectangle(4, 4, w - 8, h - 8, 10)

        if self.show_branding and lay.branding_rect is not None:
            self._draw_branding(dc, lay.branding_rect)

    def _draw_branding(self, dc: wx.DC, rect: wx.Rect) -> None:
        """Draw the branding image inside rect.
        For left/right posts the rect is a vertical strip; the image is rotated 90° CCW.

        Images are rasterised at physical-pixel dimensions (logical × DC zoom) so the
        DC's zoom transform never upscales a low-res bitmap, preventing pixelation.

        Intentionally uses dc.DrawBitmap (not a GraphicsContext).  On GTK/Linux,
        creating and destroying a GC mid-paint corrupts the Cairo DC state and causes
        all subsequent direct-DC drawing (the section body, rails, holes, …) to become
        invisible — manifesting as the baseboard hiding the entire breadboard.
        Instead the bitmap is drawn in screen coordinates by temporarily resetting the
        DC transform, then restoring it.
        """
        img_path = self.branding_image or (
            _DEFAULT_BRAND_IMAGE if os.path.isfile(_DEFAULT_BRAND_IMAGE) else '')
        if not img_path:
            return

        # Use known canvas state rather than querying the DC: on GTK,
        # dc.GetDeviceOrigin() unreliably returns (0,0) even after
        # SetDeviceOrigin(pan_x, pan_y), so restoring that value would shift
        # all subsequent drawing to the wrong position.
        zoom = self._zoom
        ox, oy = int(self._pan_x), int(self._pan_y)

        def _blit(bmp: wx.Bitmap, log_w: int, log_h: int) -> None:
            """Blit bmp (rasterised at log_w*zoom × log_h*zoom screen px) centred in
            rect.  Converts the logical centre position to screen coords, resets the
            DC transform, draws 1:1, then restores the transform."""
            sx = int((rect.x + (rect.width  - log_w) // 2) * zoom + ox)
            sy = int((rect.y + (rect.height - log_h) // 2) * zoom + oy)
            dc.SetUserScale(1.0, 1.0)
            dc.SetDeviceOrigin(0, 0)
            try:
                dc.DrawBitmap(bmp, sx, sy, True)
            finally:
                dc.SetUserScale(zoom, zoom)
                dc.SetDeviceOrigin(ox, oy)

        try:
            if img_path.lower().endswith('.svg') and hasattr(wx, 'BitmapBundle'):
                iw, ih = _parse_svg_size(img_path)
                if self.layout.branding_rotated:
                    # SVG is landscape; rasterise pre-rotation then rotate so it fills
                    # the portrait rect.
                    scale = min(rect.height / iw, rect.width / ih) if iw and ih else 1.0
                    nw = max(1, int(iw * scale))
                    nh = max(1, int(ih * scale))
                    rnw, rnh = max(1, int(nw * zoom)), max(1, int(nh * zoom))
                    bundle = wx.BitmapBundle.FromSVGFile(img_path, wx.Size(rnw, rnh))
                    bmp = wx.Bitmap(
                        bundle.GetBitmap(wx.Size(rnw, rnh))
                              .ConvertToImage().Rotate90(clockwise=False)
                    )
                    _blit(bmp, nh, nw)   # logical dims swap after 90° rotation
                else:
                    scale = min(rect.width / iw, rect.height / ih) if iw and ih else 1.0
                    nw = max(1, int(iw * scale))
                    nh = max(1, int(ih * scale))
                    rnw, rnh = max(1, int(nw * zoom)), max(1, int(nh * zoom))
                    bundle = wx.BitmapBundle.FromSVGFile(img_path, wx.Size(rnw, rnh))
                    bmp = bundle.GetBitmap(wx.Size(rnw, rnh))
                    _blit(bmp, nw, nh)
                return
        except Exception:
            pass
        try:
            img = wx.Image(img_path, wx.BITMAP_TYPE_ANY)
            if not img.IsOk():
                return
            if self.layout.branding_rotated:
                img = img.Rotate90(clockwise=False)
            iw, ih = img.GetWidth(), img.GetHeight()
            scale = min(rect.width / iw, rect.height / ih) if iw and ih else 1.0
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            rnw, rnh = max(1, int(nw * zoom)), max(1, int(nh * zoom))
            img = img.Scale(rnw, rnh, wx.IMAGE_QUALITY_HIGH)
            _blit(wx.Bitmap(img), nw, nh)
        except Exception:
            pass

    def _draw_section_body(self, dc: wx.DC, section: int) -> None:
        lay = self.layout
        top = lay._section_top[section]
        board_rect = wx.Rect(
            lay.board_left - PITCH - MARGIN // 2,
            top,
            (lay.columns + 1) * PITCH + MARGIN,   # extra PITCH makes right margin == left margin
            lay._section_body_h,
        )
        dc.SetBrush(wx.Brush('#e8e0c8'))
        dc.SetPen(wx.Pen('#b0a090', 1))
        dc.DrawRoundedRectangle(board_rect, 8)

    def _draw_vert_rails(self, dc: wx.DC) -> None:
        lay = self.layout
        rail_colors = {
            'vert_plus':        '#cc2222',
            'vert_minus':       '#2244cc',
            'vert_right_plus':  '#cc2222',
            'vert_right_minus': '#2244cc',
        }

        if not lay._vert_rail_cx:
            return

        # Rails span only the stacked-board height
        boards_top    = lay._section_top[0] - MARGIN // 2
        boards_bottom = lay._section_top[-1] + lay._section_body_h
        boards_height = boards_bottom - boards_top

        # Draw a cream background panel for each side (left / right) separately
        board_cx = lay.board_left + (lay.columns - 1) * PITCH // 2
        for side_rails in (
            {r: cx for r, cx in lay._vert_rail_cx.items() if cx <= board_cx},
            {r: cx for r, cx in lay._vert_rail_cx.items() if cx >  board_cx},
        ):
            if not side_rails:
                continue
            xs = list(side_rails.values())
            sw2 = (PITCH - 4) // 2   # half the strip width
            bg_x = min(xs) - sw2 - 4
            bg_w = max(xs) - min(xs) + 2 * sw2 + 8
            bg_rect = wx.Rect(bg_x, boards_top, bg_w, boards_height)
            dc.SetBrush(wx.Brush('#e8e0c8'))
            dc.SetPen(wx.Pen('#b0a090', 1))
            dc.DrawRoundedRectangle(bg_rect, 6)

        strip_w = PITCH - 4   # match horizontal rail strip_h (= RAIL_H - 4 = 14 px)
        for rail, cx in lay._vert_rail_cx.items():
            color = rail_colors.get(rail, '#cc2222')
            strip_rect = wx.Rect(cx - strip_w // 2, boards_top + 2,
                                 strip_w, boards_height - 4)
            dc.SetBrush(wx.Brush(color))
            dc.SetPen(wx.Pen(color, 0))
            dc.DrawRoundedRectangle(strip_rect, 4)
            # Holes
            for idx, ry in enumerate(lay._vert_hole_y, 1):
                self._draw_hole_dot(dc, cx, ry, RailHole(rail, idx))
            # + / − symbols at top and bottom
            symbol = '+' if 'plus' in rail else '−'
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#ffffff')
            tw, th = dc.GetTextExtent(symbol)
            dc.DrawText(symbol, cx - tw // 2, boards_top + 2)
            dc.DrawText(symbol, cx - tw // 2, boards_bottom - th - 2)

    def _draw_rails(self, dc: wx.DC, section: int = 0) -> None:
        lay = self.layout
        rail_colors = {
            'top_plus': '#cc2222', 'top_minus': '#2244cc',
            'bot_plus': '#cc2222', 'bot_minus': '#2244cc',
        }
        strip_h = RAIL_H - 4   # coloured stripe height

        rl = lay.rail_len
        for rail in RAIL_NAMES:
            ry = lay.section_rail_y(rail, section)
            color = rail_colors[rail]

            # Connected segments (respects electrical split)
            if lay.rail_split and rl > RAIL_SPLIT:
                segments = [(1, RAIL_SPLIT), (RAIL_SPLIT + 1, rl)]
            else:
                segments = [(1, rl)]

            if self.rail_style == 'bbrd_classic':
                # One rounded rect per group of 5 holes
                n_groups = (rl + 4) // 5
                for group in range(n_groups):
                    first = group * 5 + 1
                    last  = min(group * 5 + 5, rl)
                    x_left  = lay.rail_x(first) - PITCH // 2
                    x_right = lay.rail_x(last)  + PITCH // 2
                    stripe = wx.Rect(x_left, ry - strip_h // 2, x_right - x_left, strip_h)
                    dc.SetBrush(wx.Brush(color))
                    dc.SetPen(wx.Pen(color, 0))
                    dc.DrawRoundedRectangle(stripe, 3)

            elif self.rail_style == 'bbrd_modern':
                # One large outline-only rounded rect per connected segment
                modern_h = RAIL_H
                for first, last in segments:
                    x_left  = lay.rail_x(first) - PITCH // 2
                    x_right = lay.rail_x(last)  + PITCH // 2
                    stripe = wx.Rect(x_left, ry - modern_h // 2, x_right - x_left, modern_h)
                    dc.SetBrush(wx.TRANSPARENT_BRUSH)
                    dc.SetPen(wx.Pen(color, 2))
                    dc.DrawRoundedRectangle(stripe, 5)

            elif self.rail_style == 'solid_line':
                # Thin line on the playing-field side of each rail (no line between rails)
                # plus rails: line faces the tie strips; minus rails: line faces outward
                line_h = 3
                if rail in ('top_minus', 'bot_plus'):
                    line_y = ry - RAIL_H // 2
                else:
                    line_y = ry + RAIL_H // 2 - line_h
                for first, last in segments:
                    x_left  = lay.rail_x(first) - PITCH // 2
                    x_right = lay.rail_x(last)  + PITCH // 2
                    stripe = wx.Rect(x_left, line_y, x_right - x_left, line_h)
                    dc.SetBrush(wx.Brush(color))
                    dc.SetPen(wx.Pen(color, 0))
                    dc.DrawRectangle(stripe)

            # Holes
            for idx in range(1, rl + 1):
                rx = lay.rail_x(idx)
                self._draw_hole_dot(dc, rx, ry, RailHole(rail, idx, section))

            # + / − symbol at both ends
            symbol = '+' if 'plus' in rail else '−'
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#444444')
            dc.DrawText(symbol, lay.rail_x(1) - PITCH + 1, ry - 7)
            dc.DrawText(symbol, lay.rail_x(rl) + 4, ry - 7)

    def _draw_center_gap(self, dc: wx.DC, section: int = 0) -> None:
        lay = self.layout
        gap_y_top = lay.section_row_y('e', section) + PITCH // 2
        gap_y_bot = lay.section_row_y('f', section) - PITCH // 2
        gap_rect = wx.Rect(
            lay.board_left - PITCH - MARGIN // 4,
            gap_y_top,
            (lay.columns + 1) * PITCH + MARGIN // 2,   # extra PITCH for equal left/right margin
            gap_y_bot - gap_y_top,
        )
        dc.SetBrush(wx.Brush('#c0b898'))
        dc.SetPen(wx.Pen('#a09080', 1))
        dc.DrawRectangle(gap_rect)

    def _draw_holes(self, dc: wx.DC, section: int = 0) -> None:
        lay = self.layout
        for col in range(1, lay.columns + 1):
            cx = lay.col_x(col)
            for row in ALL_ROWS:
                ry = lay.section_row_y(row, section)
                h = TieHole(col, row, section)
                self._draw_hole_dot(dc, cx, ry, h)

    def _draw_hole_dot(self, dc: wx.DC, cx: int, cy: int, hole: Hole) -> None:
        # Net-highlight ring (outermost — drawn first so others layer on top)
        if hole in self._net_hl_holes:
            dc.SetBrush(wx.Brush('#00ccff'))
            dc.SetPen(wx.Pen('#0099cc', 1))
            dc.DrawCircle(cx, cy, HOLE_R + 4)
        # Validation ring
        if hole in self._highlighted_holes:
            color = '#ff4444' if self._highlight_kind == IssueKind.SHORT else '#ffaa00'
            dc.SetBrush(wx.Brush(color))
            dc.SetPen(wx.Pen(color, 1))
            dc.DrawCircle(cx, cy, HOLE_R + 2)
        # Normal hole dot
        if self.board.is_hole_occupied(hole):
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#555555', 1))
        else:
            dc.SetBrush(wx.Brush('#444444'))
            dc.SetPen(wx.Pen('#222222', 1))
        dc.DrawCircle(cx, cy, HOLE_R)

    def _draw_s11_rail(self, dc: wx.DC, xs: List[int], y: int, color: str, symbol: str,
                       rail_name: str, section: int = 0, symbol_ends: str = 'both') -> None:
        """symbol_ends restricts the +/- end labels to 'left', 'right', or 'both' —
        used to avoid two labels colliding where a rail is split (e.g. V3 | V4)."""
        if not xs:
            return
        strip_h = RAIL_H - 4
        x_left  = min(xs) - PITCH // 2
        x_right = max(xs) + PITCH // 2
        stripe = wx.Rect(x_left, y - strip_h // 2, x_right - x_left, strip_h)
        dc.SetBrush(wx.Brush(color))
        dc.SetPen(wx.Pen(color, 0))
        dc.DrawRoundedRectangle(stripe, 3)
        for idx, x in enumerate(xs, 1):
            self._draw_hole_dot(dc, x, y, RailHole(rail_name, idx, section))
        dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground('#444444')
        if symbol_ends in ('left', 'both'):
            dc.DrawText(symbol, x_left - PITCH // 2, y - 7)
        if symbol_ends in ('right', 'both'):
            dc.DrawText(symbol, x_right + 4, y - 7)

    def _draw_board_sunny11(self, dc: wx.DC) -> None:
        """Bespoke paint path for the sunny-11 layout: two portrait tie-blocks
        (V1/V2) side by side over one landscape tie-block (V3/V4)."""
        lay = self.layout

        # One continuous board body (the real hardware is a single moulded
        # piece with three tie-hole "slots" cut into it, not separate boards
        # placed side by side) — internal divisions are drawn as grooves in
        # the same body colour, never as gaps showing the canvas background.
        overall_left  = min(lay._s11_block_left[0], lay._s11_lower_col_x[1]) - PITCH
        overall_right = max(lay._s11_block_left[1] + lay._s11_block_w,
                             lay._s11_lower_col_x[SUNNY11_LOWER_COLS]) + PITCH
        overall_top    = lay._s11_block_top - MARGIN // 2
        overall_bottom = lay._s11_lower_bottom
        board_rect = wx.Rect(overall_left, overall_top,
                             overall_right - overall_left, overall_bottom - overall_top)
        dc.SetBrush(wx.Brush('#e8e0c8'))
        dc.SetPen(wx.Pen('#b0a090', 1))
        dc.DrawRoundedRectangle(board_rect, 8)

        # Groove between the V1 and V2 slots — same vertical extent as the
        # internal a-e/f-j gaps (tie area only, not up through the rails),
        # and inset half a pitch on each side, in between the neighbouring
        # holes rather than spanning hole-centre to hole-centre.
        groove_rect = wx.Rect(
            lay._s11_block_left[0] + lay._s11_block_w + lay._s11_row_pitch // 2,
            lay._s11_col_y[1] - PITCH,
            lay._s11_gap_block_x - lay._s11_row_pitch,
            lay._s11_col_y[SUNNY11_UPPER_COLS] - lay._s11_col_y[1] + 2 * PITCH,
        )
        dc.SetBrush(wx.Brush('#c0b898'))
        dc.SetPen(wx.Pen('#a09080', 1))
        dc.DrawRectangle(groove_rect)

        # Horizontal centre gap (a-e | f-j) in each portrait block
        bank_w = (len(TOP_ROWS) - 1) * lay._s11_row_pitch
        for section in (0, 1):
            left = lay._s11_block_left[section]
            gap_rect = wx.Rect(
                left + bank_w + lay._s11_row_pitch // 2,
                lay._s11_col_y[1] - PITCH,
                lay._s11_gap_upper - lay._s11_row_pitch,
                lay._s11_col_y[SUNNY11_UPPER_COLS] - lay._s11_col_y[1] + 2 * PITCH,
            )
            dc.SetBrush(wx.Brush('#c0b898'))
            dc.SetPen(wx.Pen('#a09080', 1))
            dc.DrawRectangle(gap_rect)

        # No extra marker needed at the V3/V4 split: the two rail stripes
        # (drawn below) are already independent shapes with a real gap
        # between them, same as the V1/V2 rails above — an added notch here
        # only showed up as an odd dip in the plus rail's edge.

        # Rails
        self._draw_s11_rail(dc, lay._s11_minus_top_x, lay._s11_minus_y,
                             '#2244cc', '−', 'sunny_top_minus')
        for section in (0, 1):
            self._draw_s11_rail(dc, lay._s11_plus_rail_x[section], lay._s11_plus_y,
                                 '#cc2222', '+', 'top_plus', section)
        self._draw_s11_rail(dc, lay._s11_lower_minus_x, lay._s11_lower_minus_y,
                             '#2244cc', '−', 'sunny_bot_minus')
        self._draw_s11_rail(dc, lay._s11_lower_plus_x['lower_plus_left'], lay._s11_lower_plus_y,
                             '#cc2222', '+', 'lower_plus_left', 2, symbol_ends='left')
        self._draw_s11_rail(dc, lay._s11_lower_plus_x['lower_plus_right'], lay._s11_lower_plus_y,
                             '#cc2222', '+', 'lower_plus_right', 2, symbol_ends='right')

        # Tie holes
        for section in (0, 1):
            left = lay._s11_block_left[section]
            for row, rx_off in lay._s11_row_x.items():
                rx = left + rx_off
                for col, ry in lay._s11_col_y.items():
                    self._draw_hole_dot(dc, rx, ry, TieHole(col, row, section))
        for row, ry in lay._s11_lower_row_y.items():
            for col, cx in lay._s11_lower_col_x.items():
                self._draw_hole_dot(dc, cx, ry, TieHole(col, row, 2))

    def _draw_pin_drag_preview(self, dc: wx.DC) -> None:
        """Draw the component being pin-dragged at its current (live) position."""
        ref     = self._pin_drag_ref
        pin_num = self._pin_drag_num
        target  = self._pin_drag_hole
        if ref is None or pin_num is None:
            return
        placed   = self.board.get_placement(ref)
        comp_def = ALL_DEFS.get(placed.type_id) if placed else None
        if not placed or not comp_def:
            return

        preview_holes: Dict[int, Hole] = dict(placed.pin_holes)
        if target is not None:
            preview_holes[pin_num] = target

        import dataclasses as _dc
        preview = _dc.replace(placed, pin_holes=preview_holes)
        self._draw_placed_component(dc, ref, preview, comp_def, selected=True, delete_hover=False)

        # Gold ring on the snap target hole
        if target is not None:
            xy = self.layout.hole_xy(target)
            if xy:
                dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(wx.Colour('#e8c020'), 2))
                dc.DrawCircle(xy[0], xy[1], HOLE_R + 4)

    def _draw_wire_end_drag_preview(self, dc: wx.DC) -> None:
        """Gold ring on the snap target while dragging a wire endpoint."""
        target = self._wire_end_drag_hole
        if target is None:
            return
        xy = self.layout.hole_xy(target)
        if xy:
            dc.SetBrush(_transparent_brush())
            dc.SetPen(wx.Pen(wx.Colour('#e8c020'), 2))
            dc.DrawCircle(xy[0], xy[1], HOLE_R + 4)

    def _commit_pin_drag(self) -> None:
        """Apply the pin drag result to the placed component."""
        ref     = self._pin_drag_ref
        pin_num = self._pin_drag_num
        target  = self._pin_drag_hole
        if ref is None or pin_num is None or target is None:
            self._drag_pre_snap = None
            return
        placed   = self.board.get_placement(ref)
        comp_def = ALL_DEFS.get(placed.type_id) if placed else None
        if not placed or not comp_def:
            self._drag_pre_snap = None
            return

        old_hole = placed.pin_holes.get(pin_num)
        placed.pin_holes[pin_num] = target

        if self._drag_pre_snap is not None and old_hole != target:
            self._undo_stack.append(self._drag_pre_snap)
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
            self._redo_stack.clear()
            self._notify_history()
        self._drag_pre_snap = None

        if self.on_placed:
            self.on_placed(ref)

    def _ann_handles(self, ann):
        """Return (x, y) positions of resize handles for an annotation."""
        if isinstance(ann, DrawLine):
            return [(ann.x1, ann.y1), (ann.x2, ann.y2)]
        if isinstance(ann, DrawRect):
            return [(ann.x1, ann.y1), (ann.x2, ann.y1), (ann.x2, ann.y2), (ann.x1, ann.y2)]
        if isinstance(ann, DrawCircle):
            return [(ann.cx, ann.cy - ann.r), (ann.cx + ann.r, ann.cy),
                    (ann.cx, ann.cy + ann.r), (ann.cx - ann.r, ann.cy)]
        if isinstance(ann, DrawText):
            return [(ann.x, ann.y)]
        if isinstance(ann, DrawTextBox):
            return [(ann.x1, ann.y1), (ann.x2, ann.y1), (ann.x2, ann.y2), (ann.x1, ann.y2)]
        return []

    def _handle_at(self, ann_idx: int, px: float, py: float, radius: float = 8.0) -> Optional[int]:
        """Return handle index if (px,py) is within radius of any handle, else None."""
        for i, (hx, hy) in enumerate(self._ann_handles(self._annotations[ann_idx])):
            if (px - hx) ** 2 + (py - hy) ** 2 <= radius ** 2:
                return i
        return None

    def _apply_resize(self, ann, h_idx: int, nx: float, ny: float) -> None:
        """Move handle h_idx of ann to (nx, ny)."""
        if isinstance(ann, DrawLine):
            if h_idx == 0:
                ann.x1, ann.y1 = nx, ny
            else:
                ann.x2, ann.y2 = nx, ny
        elif isinstance(ann, DrawRect):
            if h_idx == 0:
                ann.x1, ann.y1 = nx, ny
            elif h_idx == 1:
                ann.x2, ann.y1 = nx, ny
            elif h_idx == 2:
                ann.x2, ann.y2 = nx, ny
            elif h_idx == 3:
                ann.x1, ann.y2 = nx, ny
        elif isinstance(ann, DrawCircle):
            ann.r = max(4.0, ((nx - ann.cx) ** 2 + (ny - ann.cy) ** 2) ** 0.5)
        elif isinstance(ann, DrawText):
            ann.x, ann.y = nx, ny
        elif isinstance(ann, DrawTextBox):
            if h_idx == 0:
                ann.x1, ann.y1 = nx, ny
            elif h_idx == 1:
                ann.x2, ann.y1 = nx, ny
            elif h_idx == 2:
                ann.x2, ann.y2 = nx, ny
            elif h_idx == 3:
                ann.x1, ann.y2 = nx, ny

    def _rail_crossing_segments(self, xy1, xy2):
        """Yield (p1, p2) subsegments of the line that fall inside horizontal rail strips."""
        lay = self.layout
        if not lay.has_rails:
            return
        x1, y1 = xy1
        x2, y2 = xy2
        for section in range(lay.sections):
            for rail in RAIL_NAMES:
                ry = lay.section_rail_y(rail, section)
                y_lo, y_hi = ry - RAIL_H // 2, ry + RAIL_H // 2
                if y1 == y2:
                    if y_lo <= y1 <= y_hi:
                        yield xy1, xy2
                else:
                    t_lo = (y_lo - y1) / (y2 - y1)
                    t_hi = (y_hi - y1) / (y2 - y1)
                    if t_lo > t_hi:
                        t_lo, t_hi = t_hi, t_lo
                    t0 = max(0.0, t_lo)
                    t1 = min(1.0, t_hi)
                    if t0 < t1:
                        dx, dy = x2 - x1, y2 - y1
                        yield ((int(x1 + t0 * dx), int(y1 + t0 * dy)),
                               (int(x1 + t1 * dx), int(y1 + t1 * dy)))

    def _wire_points(self, wire, xy1, xy2):
        """Return list of (x, y) points for drawing: 2 if straight, 3 if bent."""
        if wire.mid_point:
            return [xy1, wire.mid_point, xy2]
        return [xy1, xy2]

    def _draw_wire_polyline(self, dc: wx.DC, pts, pen: wx.Pen) -> None:
        dc.SetPen(pen)
        if len(pts) == 3:
            self._draw_wire_rounded_corner(dc, pts[0], pts[1], pts[2])
        else:
            for i in range(len(pts) - 1):
                dc.DrawLine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])

    _WIRE_CORNER_R = 10  # corner radius in pixels for bent wires

    def _draw_wire_rounded_corner(self, dc: wx.DC, A, B, C) -> None:
        """Draw a 3-point wire path with a smooth quadratic-Bezier corner at B."""
        dx1, dy1 = A[0] - B[0], A[1] - B[1]
        dx2, dy2 = C[0] - B[0], C[1] - B[1]
        len1 = math.hypot(dx1, dy1)
        len2 = math.hypot(dx2, dy2)
        if len1 < 1 or len2 < 1:
            dc.DrawLine(A[0], A[1], B[0], B[1])
            dc.DrawLine(B[0], B[1], C[0], C[1])
            return
        r = min(self._WIRE_CORNER_R, len1 * 0.9, len2 * 0.9)
        t1x = B[0] + r * dx1 / len1
        t1y = B[1] + r * dy1 / len1
        t2x = B[0] + r * dx2 / len2
        t2y = B[1] + r * dy2 / len2
        gc = _make_gc(dc)
        if gc is not None:
            pen = dc.GetPen()
            gc.SetPen(gc.CreatePen(
                wx.GraphicsPenInfo(pen.GetColour()).Width(pen.GetWidth())
                .Cap(wx.CAP_ROUND).Join(wx.JOIN_ROUND)
            ))
            gc.SetBrush(_transparent_brush())
            path = gc.CreatePath()
            path.MoveToPoint(A[0], A[1])
            path.AddLineToPoint(t1x, t1y)
            path.AddQuadCurveToPoint(B[0], B[1], t2x, t2y)
            path.AddLineToPoint(C[0], C[1])
            gc.StrokePath(path)
        else:
            N = 12
            pts = [wx.Point(A[0], A[1])]
            for i in range(N + 1):
                t = i / N
                mt = 1.0 - t
                pts.append(wx.Point(
                    round(mt * mt * t1x + 2.0 * mt * t * B[0] + t * t * t2x),
                    round(mt * mt * t1y + 2.0 * mt * t * B[1] + t * t * t2y),
                ))
            pts.append(wx.Point(C[0], C[1]))
            dc.DrawLines(pts)

    def _draw_wires(self, dc: wx.DC) -> None:
        lay = self.layout
        # Net-highlight halo: draw a fat cyan line behind each wire on the highlighted net
        if self._net_hl_holes:
            dc.SetPen(wx.Pen(wx.Colour('#00ccff'), 9))
            for wire in self.board.wires:
                if wire.h1 in self._net_hl_holes and wire.h2 in self._net_hl_holes:
                    xy1 = lay.hole_xy(wire.h1)
                    xy2 = lay.hole_xy(wire.h2)
                    if xy1 and xy2:
                        pts = self._wire_points(wire, xy1, xy2)
                        self._draw_wire_polyline(dc, pts, dc.GetPen())
        for wire in self.board.wires:
            xy1 = lay.hole_xy(wire.h1)
            xy2 = lay.hole_xy(wire.h2)
            if xy1 is None or xy2 is None:
                continue
            pts = self._wire_points(wire, xy1, xy2)
            selected = (wire is self._selected_wire)
            delete_hover = (wire is self._hover_wire)
            width = 5 if selected else 3
            color = '#ffffff' if selected else wire.color
            # White border only on rail-crossing segments for contrast (straight wires only)
            if not selected and not delete_hover and not wire.mid_point:
                dc.SetPen(wx.Pen(wx.Colour('#ffffff'), 5))
                for s1, s2 in self._rail_crossing_segments(xy1, xy2):
                    dc.DrawLine(s1[0], s1[1], s2[0], s2[1])
            # Selection / delete-hover halo
            if selected:
                self._draw_wire_polyline(dc, pts, wx.Pen(wx.Colour(wire.color), 7))
            elif delete_hover:
                self._draw_wire_polyline(dc, pts, wx.Pen(wx.Colour('#ff4444'), 7))
            self._draw_wire_polyline(dc, pts, wx.Pen(wx.Colour(color), width))
            # End dots
            dc.SetBrush(wx.Brush(color))
            dc.SetPen(wx.Pen(color, 1))
            dc.DrawCircle(xy1[0], xy1[1], 4)
            dc.DrawCircle(xy2[0], xy2[1], 4)
            # Bend handle: show on selected wire
            if selected:
                hx, hy = wire.mid_point if wire.mid_point else (
                    (xy1[0] + xy2[0]) // 2, (xy1[1] + xy2[1]) // 2)
                dc.SetBrush(wx.Brush(wx.Colour('#ffffff')))
                dc.SetPen(wx.Pen(wx.Colour(wire.color), 2))
                dc.DrawCircle(hx, hy, 5)

    def _draw_components(self, dc: wx.DC) -> None:
        for ref, placed in self.board.placements.items():
            if ref == self._pin_drag_ref:
                continue   # drawn live by _draw_pin_drag_preview
            comp_def = ALL_DEFS.get(placed.type_id)
            if comp_def is None:
                continue
            selected = (ref == self._selected_ref)
            delete_hover = (ref == self._hover_ref)
            self._draw_placed_component(dc, ref, placed, comp_def, selected, delete_hover)

    def _draw_placed_component(self, dc: wx.DC, ref: str,
                                placed: PlacedComponent, comp_def: ComponentDef,
                                selected: bool, delete_hover: bool = False) -> None:
        # Free-floating modules are rendered from their stored canvas position,
        # not from hole coordinates — handle them before the hole-based path.
        if comp_def.is_module:
            self._draw_module_component(dc, comp_def, placed, ref, selected)
            return

        lay = self.layout
        holes = [lay.hole_xy(h) for h in placed.pin_holes.values() if lay.hole_xy(h)]
        if not holes:
            return

        xs = [xy[0] for xy in holes]
        ys = [xy[1] for xy in holes]

        # Draw selection / delete-hover halo behind the component
        if selected or delete_hover:
            halo_color = '#ff4444' if delete_hover else '#00ccff'
            halo_rect = wx.Rect(min(xs) - 8, min(ys) - 11,
                                max(xs) - min(xs) + 16, max(ys) - min(ys) + 22)
            dc.SetBrush(_transparent_brush())
            dc.SetPen(wx.Pen(wx.Colour(halo_color), 3))
            dc.DrawRoundedRectangle(halo_rect, 5)

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        body_color = comp_def.color
        border_color = '#333333'

        dc.SetBrush(wx.Brush(body_color))
        dc.SetPen(wx.Pen(border_color, 2 if selected else 1))

        if comp_def.is_dip:
            body_rect = wx.Rect(x_min - 4, y_min - 2, x_max - x_min + 8, y_max - y_min + 4)

            # Legs: small grey tabs extending above/below the body at each pin
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#555555', 1))
            for hole in placed.pin_holes.values():
                xy = lay.hole_xy(hole)
                if xy is None:
                    continue
                hx, hy = xy
                if isinstance(hole, TieHole) and hole.row in TOP_ROWS:
                    dc.DrawRectangle(hx - 1, body_rect.GetTop() - 6, 3, 7)
                elif isinstance(hole, TieHole) and hole.row in BOT_ROWS:
                    dc.DrawRectangle(hx - 1, body_rect.GetBottom() - 1, 3, 7)

            # IC body (drawn over the inner part of the legs)
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen(border_color, 2 if selected else 1))
            dc.DrawRoundedRectangle(body_rect, 3)

            # Pin labels inside the body — pin numbers normally, rotated function names when toggled
            fn_map = (self.netlist.pinfunction_map(ref)
                      if self._dip_fn_labels and self.netlist else {})

            # Pin-1 dot: small circle on body surface, on the same side as pin 1.
            # Inverted (dark fill, white border) when fn labels are shown so the dot
            # stands out against the light grey label text.
            pin1_hole = placed.pin_holes.get(1)
            if pin1_hole:
                pin1_xy = lay.hole_xy(pin1_hole)
                if pin1_xy:
                    if fn_map:
                        dc.SetBrush(wx.Brush('#888888'))
                        dc.SetPen(wx.Pen('#000000', 1))
                    else:
                        dc.SetBrush(wx.Brush('#ffffff'))
                        dc.SetPen(wx.Pen('#333333', 1))
                    if isinstance(pin1_hole, TieHole) and pin1_hole.row in TOP_ROWS:
                        dot_y = body_rect.GetY() + 12
                    else:
                        dot_y = body_rect.GetBottom() - 12
                    dc.DrawCircle(pin1_xy[0], dot_y, 3)
            if fn_map:
                # Rotated text via GraphicsContext.
                # Top-side labels lean right (75° CW), bottom-side lean left (105° CCW),
                # so same-column labels cross rather than overlap — much more readable for
                # long function names (e.g. CD4033B).  DrawText(0, -th/2) centres the label
                # horizontally around the pin's x position regardless of angle (derivation:
                # screen_x_centre = tx - sinθ*(ly + th/2) = tx iff ly = -th/2).
                _ANGLE_TOP = math.pi * 105 / 180  # 105° CW  → down-left
                _ANGLE_BOT = math.pi * 75 / 180   # 75° CCW  → up-right
                _half_h = body_rect.GetHeight() / 2
                gc_lbl = _make_gc(dc)
                if gc_lbl is not None:
                    font_fn = wx.Font(4, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                      wx.FONTWEIGHT_NORMAL)
                    gc_lbl.SetFont(gc_lbl.CreateFont(font_fn, wx.Colour('#cccccc')))
                    for pin_num, hole in placed.pin_holes.items():
                        xy = lay.hole_xy(hole)
                        if xy is None:
                            continue
                        label = fn_map.get(pin_num) or str(pin_num)
                        tw, th = gc_lbl.GetTextExtent(label)
                        hx = float(xy[0])
                        is_top = isinstance(hole, TieHole) and hole.row in TOP_ROWS
                        is_bot = isinstance(hole, TieHole) and hole.row in BOT_ROWS
                        if not is_top and not is_bot:
                            continue
                        gc_lbl.PushState()
                        tilt = tw > _half_h
                        if is_top:
                            gc_lbl.Translate(hx, float(body_rect.GetTop() + 2))
                            gc_lbl.Rotate(_ANGLE_TOP if tilt else math.pi / 2)
                        else:
                            gc_lbl.Translate(hx, float(body_rect.GetBottom() - 2))
                            gc_lbl.Rotate(-(_ANGLE_BOT if tilt else math.pi / 2))
                        gc_lbl.DrawText(label, 0, -th / 2)
                        gc_lbl.PopState()
                else:
                    dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_NORMAL))
                    dc.SetTextForeground('#cccccc')
                    for pin_num, hole in placed.pin_holes.items():
                        xy = lay.hole_xy(hole)
                        if xy is None:
                            continue
                        label = fn_map.get(pin_num) or str(pin_num)
                        tw, th = dc.GetTextExtent(label)
                        hx = xy[0]
                        if isinstance(hole, TieHole) and hole.row in TOP_ROWS:
                            dc.DrawText(label, hx - tw // 2, body_rect.GetTop() + 2)
                        elif isinstance(hole, TieHole) and hole.row in BOT_ROWS:
                            dc.DrawText(label, hx - tw // 2, body_rect.GetBottom() - th - 2)
            else:
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.SetTextForeground('#cccccc')
                for pin_num, hole in placed.pin_holes.items():
                    xy = lay.hole_xy(hole)
                    if xy is None:
                        continue
                    label = str(pin_num)
                    tw, th = dc.GetTextExtent(label)
                    hx = xy[0]
                    if isinstance(hole, TieHole) and hole.row in TOP_ROWS:
                        dc.DrawText(label, hx - tw // 2, body_rect.GetTop() + 2)
                    elif isinstance(hole, TieHole) and hole.row in BOT_ROWS:
                        dc.DrawText(label, hx - tw // 2, body_rect.GetBottom() - th - 2)

            # Ref + value label centered in the IC body
            cx = body_rect.GetX() + body_rect.GetWidth() // 2
            cy = body_rect.GetY() + body_rect.GetHeight() // 2
            comp_nl = self.netlist.components.get(ref) if self.netlist else None
            if placed.type_id == 'OPAMP_SPICE':
                value_str = 'SIM'
            else:
                value_str = comp_nl.value if comp_nl else ''
            dc.SetTextForeground('#ffffff')
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            rw, rh = dc.GetTextExtent(ref)
            if value_str:
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                vw, vh = dc.GetTextExtent(value_str)
                gap = 1
                total_h = rh + gap + vh
                dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
                dc.DrawText(ref, cx - rw // 2, cy - total_h // 2)
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.DrawText(value_str, cx - vw // 2, cy - total_h // 2 + rh + gap)
            else:
                dc.DrawText(ref, cx - rw // 2, cy - rh // 2)

        elif comp_def.pin_count == 2:
            p1 = lay.hole_xy(placed.pin_holes[1])
            p2 = lay.hole_xy(placed.pin_holes[2])
            if p1 and p2:
                if placed.type_id == 'SPST':
                    self._draw_pushbutton(dc, comp_def, placed, ref, p1, p2, selected)
                else:
                    self._draw_axial_component(dc, comp_def, placed, ref, p1, p2, selected)
        else:
            # 3-pin and 4-pin components
            _SLIDER_TYPES = frozenset({'SPDT', 'SP3T'})
            _TO92_TYPES = frozenset({'NPN', 'PNP', 'JFET_N', 'JFET_P', 'BS170', 'NMOS', 'PMOS'})
            if placed.type_id in _SLIDER_TYPES:
                sample_hole = next(iter(placed.pin_holes.values()))
                in_top = isinstance(sample_hole, TieHole) and sample_hole.row in TOP_ROWS
                self._draw_slider_switch(dc, comp_def, placed, ref,
                                         x_min, x_max, y_min, y_max, in_top, selected)
                return
            elif placed.type_id in _TO92_TYPES:
                # Ammo-pack style TO-92: small D-shaped body elevated above holes,
                # three thin wire leads sticking out to each pin hole.
                sample_hole = next(iter(placed.pin_holes.values()))
                in_top = isinstance(sample_hole, TieHole) and sample_hole.row in TOP_ROWS

                # Fixed body size centered on the middle pin hole
                cx_mid    = float((x_min + x_max) // 2)
                body_half = 12.0   # half-width → body is 24 px wide
                r_body    = body_half
                # Flat face sits at the hole-row centre (halfway into the hole circle)
                flat_y    = float(y_min) if in_top else float(y_max)

                # Converging leads from each pin hole to the flat face of the body.
                # flat_y == pin_y so outer leads are short horizontal stubs.
                inset     = 3.0
                step      = (2 * body_half - 2 * inset) / 2
                attach_xs = [cx_mid - body_half + inset + i * step for i in range(3)]
                pin_xs    = sorted(xy[0] for xy in holes)
                pin_y     = y_min if in_top else y_max
                dc.SetPen(wx.Pen('#888888', 3))
                for px, ax in zip(pin_xs, attach_xs):
                    dc.DrawLine(px, pin_y, int(ax), int(flat_y))

                # D-shaped body
                dome_up = not bool(placed.flipped)
                dc.SetBrush(wx.Brush(wx.Colour(comp_def.color)))
                dc.SetPen(wx.Pen('#333333', 2 if selected else 1))
                gc = _make_gc(dc)
                if gc is not None:
                    path = gc.CreatePath()
                    path.MoveToPoint(cx_mid - body_half, flat_y)
                    path.AddLineToPoint(cx_mid + body_half, flat_y)
                    path.AddArc(cx_mid, flat_y, r_body, 0.0, math.pi, not dome_up)
                    path.CloseSubpath()
                    gc.SetBrush(gc.CreateBrush(wx.Brush(wx.Colour(comp_def.color))))
                    gc.SetPen(gc.CreatePen(
                        wx.GraphicsPenInfo(wx.Colour('#333333')).Width(2 if selected else 1)))
                    gc.DrawPath(path)
                else:
                    # Fallback: dc.DrawArc for SVGFileDC and other non-GC DCs
                    if dome_up:
                        dc.DrawArc(int(cx_mid + body_half), int(flat_y),
                                   int(cx_mid - body_half), int(flat_y),
                                   int(cx_mid), int(flat_y))
                    else:
                        dc.DrawArc(int(cx_mid - body_half), int(flat_y),
                                   int(cx_mid + body_half), int(flat_y),
                                   int(cx_mid), int(flat_y))

                # Ref label centered in the dome (use dc for screen coords)
                dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.SetTextForeground('#eeeeee')
                tw, th = dc.GetTextExtent(ref)
                lx = int(cx_mid) - tw // 2
                if dome_up:
                    ly = int(flat_y - r_body * 0.55) - th // 2
                else:
                    ly = int(flat_y + r_body * 0.55) - th // 2
                dc.DrawText(ref, lx, ly)

                # Pin name labels on the side away from the body
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.SetTextForeground('#222222')
                label_gap = 4
                for pin_num, hole in placed.pin_holes.items():
                    pxy = lay.hole_xy(hole)
                    if pxy is None:
                        continue
                    name = comp_def.pin_names.get(pin_num, str(pin_num))
                    ptw, pth = dc.GetTextExtent(name)
                    if dome_up:
                        dc.DrawText(name, pxy[0] - ptw // 2, pin_y + label_gap)
                    else:
                        dc.DrawText(name, pxy[0] - ptw // 2, pin_y - pth - label_gap)
                return
            else:
                # POT: Bourns-style trimpot — flat blue rectangle + golden side screw
                body_rect = wx.Rect(x_min - 3, y_min - 6, x_max - x_min + 6, 12)
                dc.SetBrush(wx.Brush(body_color))
                dc.SetPen(wx.Pen(border_color, 2 if selected else 1))
                dc.DrawRectangle(body_rect)

                # Golden trim-screw: right end normally, left end when flipped
                screw_cx = body_rect.GetLeft() + 6 if placed.flipped else body_rect.GetRight() - 6
                screw_cy = y_min
                screw_r  = 5
                dc.SetBrush(wx.Brush('#d4a520'))
                dc.SetPen(wx.Pen('#886600', 1))
                dc.DrawCircle(screw_cx, screw_cy, screw_r)
                # Screw slot (crosshair)
                dc.SetPen(wx.Pen('#553300', 1))
                dc.DrawLine(screw_cx - 3, screw_cy, screw_cx + 3, screw_cy)
                dc.DrawLine(screw_cx, screw_cy - 3, screw_cx, screw_cy + 3)

                # Pin labels (1, W, 3) below the body
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.SetTextForeground('#222222')
                for pin_num in sorted(placed.pin_holes):
                    hole = placed.pin_holes[pin_num]
                    pin_name = comp_def.pin_names.get(pin_num, str(pin_num))
                    xy = lay.hole_xy(hole)
                    if xy is None:
                        continue
                    tw, th = dc.GetTextExtent(pin_name)
                    dc.DrawText(pin_name, xy[0] - tw // 2, body_rect.GetBottom() + 1)

        # Reference label (skipped for DIP ICs and capacitors — they draw labels inside the body)
        if not comp_def.is_dip and placed.type_id != 'C':
            dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            # Pick text colour based on body luminance so it stays legible on dark bodies.
            _bc = wx.Colour(placed.led_color if placed.led_color else comp_def.color)
            _lum = 0.299 * _bc.Red() + 0.587 * _bc.Green() + 0.114 * _bc.Blue()
            dc.SetTextForeground('#ffffff' if _lum < 128 else '#222222')
            label_x = (x_min + x_max) // 2
            label_y = (y_min + y_max) // 2 - 5
            dc.DrawText(ref, label_x - dc.GetTextExtent(ref).Width // 2, label_y)

    def _draw_module_component(self, dc: wx.DC, comp_def: ComponentDef,
                               placed: PlacedComponent, ref: str,
                               selected: bool, *_ignored) -> None:
        """Render a free-floating PCB module using its stored canvas position."""
        pos = self.board.get_module_position(ref)
        if pos is None:
            return
        mx, my = pos
        self._draw_module_board_at(dc, comp_def, ref=ref, mx=mx, my=my,
                                   selected=selected, ghost=False,
                                   flipped=placed.flipped)

    def _draw_module_board_at(self, dc: wx.DC, comp_def: ComponentDef,
                               ref: str, mx: int, my: int,
                               selected: bool, ghost: bool = False,
                               flipped: bool = False) -> None:
        if comp_def.type_id == 'RPi_Pico':
            self._draw_rpi_board_at(dc, comp_def, ref, mx, my, selected, ghost, flipped)
            return
        self._draw_nano_board_at(dc, comp_def, ref, mx, my, selected, ghost, flipped)

    def _draw_rpi_board_at(self, dc: wx.DC, comp_def: ComponentDef,
                            ref: str, mx: int, my: int,
                            selected: bool, ghost: bool = False,
                            flipped: bool = False) -> None:
        """Draw a Raspberry Pi board.  rotation = int(flipped) % 4:
          0 = GPIO top  (landscape)     1 = GPIO right (portrait, 90° CW)
          2 = GPIO bottom (landscape)   3 = GPIO left  (portrait, 270° CW)
        (mx, my) = pin-1 canvas position in all orientations.
        """
        rot = int(flipped) % 4
        GAP   = self._body_h(comp_def)   # 8 px between the two header rows
        max_col = max((o.col_delta for o in comp_def.pin_offsets.values()), default=0)
        PAD   = MODULE_BODY_PAD          # 5 px
        HDR_H = GAP + PAD * 2           # 18 px header strip
        P     = MODULE_PIN_PITCH         # 18 px — same pitch in all orientations
        SA    = 32   # "A" side margin: left (0), bottom (1), right (2), top (3)
        SB    = 75   # "B" side margin (connectors): right (0), top (1), left (2), bottom (3)

        board_color = wx.Colour(comp_def.color)
        grey = wx.Colour('#d0d0d0')
        dark = wx.Colour('#888888')
        RPi_PIN_R = 3
        eth_s = 30;  usb_s = 20;  chip_s = 44
        group_h = usb_s + 4 + usb_s + 4 + eth_s

        # ── Per-rotation geometry ──────────────────────────────────────────
        if rot == 0:
            # GPIO TOP — col→right, gap→down, board extends downward
            ps = max_col * P
            body_x = mx - SA;  body_y = my - PAD
            body_w = ps + SA + SB;  body_h = _RPi_BOARD_H
            hdr_rx, hdr_ry, hdr_rw, hdr_rh = mx - PAD, body_y, ps + PAD*2, HDR_H

            def _pin_xy(o): return (mx + o.col_delta * P, my + (GAP if o.cross_gap else 0))

            def _label(dc_, o, name):
                px, _ = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                _, rh = dc_.GetTextExtent('M')
                if not o.cross_gap:   # outer row: label outside the board (above)
                    dc_.DrawText(name, px - tw//2, body_y - rh - 2)
                else:                 # inner row: label inside the board (below header strip)
                    dc_.DrawText(name, px - tw//2, body_y + HDR_H + 2)

            ba_y = body_y + HDR_H;  ba_h = body_h - HDR_H;  ba_cy = ba_y + ba_h // 2
            hole_positions = [(body_x+16, body_y+HDR_H//2), (body_x+body_w-53, body_y+HDR_H//2),
                              (body_x+16, body_y+body_h-10), (body_x+body_w-53, body_y+body_h-10)]
            chip_cx = body_x + (SA + ps) // 2;  chip_cy = ba_y + ba_h // 2
            gt = ba_cy - group_h // 2
            usb1_cx = usb2_cx = body_x + body_w - usb_s//2 - 3
            eth_cx  = body_x + body_w - eth_s//2 - 3
            usb1_cy = gt + usb_s//2;  usb2_cy = usb1_cy + usb_s + 4
            eth_cy  = usb2_cy + usb_s//2 + 4 + eth_s//2
            name_cx = chip_cx;  name_cy = chip_cy + chip_s//2 + 6

        elif rot == 1:
            # GPIO RIGHT — col→down, gap→left, board extends leftward; SA@top SB@bottom
            ps = max_col * P
            body_h = ps + SA + SB;  body_w = _RPi_BOARD_H
            body_x = mx + PAD - body_w;  body_y = my - SA
            hdr_rx = body_x + body_w - HDR_H;  hdr_ry = my - PAD
            hdr_rw = HDR_H;  hdr_rh = ps + PAD * 2

            def _pin_xy(o): return (mx - (GAP if o.cross_gap else 0), my + o.col_delta * P)

            def _label(dc_, o, name):
                _, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:   # outer row: label outside the board (right)
                    dc_.DrawText(name, body_x + body_w + 3, py - th//2)
                else:                 # inner row: label inside board (left of header strip)
                    dc_.DrawText(name, body_x + body_w - HDR_H - tw - 2, py - th//2)

            ba_x = body_x;  ba_w = body_w - HDR_H;  ba_cx = ba_x + ba_w // 2
            ba_cy = body_y + body_h // 2
            hole_positions = [(body_x+10, body_y+16), (body_x+body_w-HDR_H//2, body_y+16),
                              (body_x+10, body_y+body_h-53), (body_x+body_w-HDR_H//2, body_y+body_h-53)]
            chip_cx = ba_cx;  chip_cy = body_y + SA + ps // 2
            # Connectors: horizontal stack on BOTTOM edge (SB side), eth closest to SA (left)
            gl = ba_cx - group_h // 2
            usb1_cy = usb2_cy = body_y + body_h - usb_s//2 - 3
            eth_cy  = body_y + body_h - eth_s//2 - 3
            eth_cx  = gl + eth_s//2
            usb1_cx = eth_cx + eth_s//2 + 4 + usb_s//2
            usb2_cx = usb1_cx + usb_s + 4
            name_cx = ba_cx;  name_cy = chip_cy + chip_s//2 + 6

        elif rot == 2:
            # GPIO BOTTOM — col→left, gap→up, board extends upward
            ps = max_col * P
            body_x = mx - ps - SB;  body_y = my + PAD - _RPi_BOARD_H
            body_w = ps + SA + SB;  body_h = _RPi_BOARD_H
            hdr_rx = mx - ps - PAD;  hdr_ry = body_y + body_h - HDR_H
            hdr_rw = ps + PAD * 2;   hdr_rh = HDR_H

            def _pin_xy(o): return (mx - o.col_delta * P, my - (GAP if o.cross_gap else 0))

            def _label(dc_, o, name):
                px, _ = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                _, rh = dc_.GetTextExtent('M')
                if not o.cross_gap:   # outer row: label outside the board (below)
                    dc_.DrawText(name, px - tw//2, body_y + body_h + 2)
                else:                 # inner row: label inside board (above header strip)
                    dc_.DrawText(name, px - tw//2, body_y + body_h - HDR_H - rh - 2)

            ba_y = body_y;  ba_h = body_h - HDR_H;  ba_cy = ba_y + ba_h // 2
            hole_positions = [(body_x+53, body_y+10), (body_x+body_w-16, body_y+10),
                              (body_x+53, body_y+body_h-HDR_H//2), (body_x+body_w-16, body_y+body_h-HDR_H//2)]
            chip_cx = body_x + (SB + ps) // 2;  chip_cy = ba_y + ba_h // 2
            gt = ba_cy - group_h // 2
            usb1_cx = usb2_cx = body_x + usb_s//2 + 3
            eth_cx  = body_x + eth_s//2 + 3
            eth_cy  = gt + eth_s//2
            usb2_cy = gt + eth_s + 4 + usb_s//2
            usb1_cy = usb2_cy + usb_s + 4
            name_cx = chip_cx;  name_cy = chip_cy - chip_s//2 - 30

        else:  # rot == 3
            # GPIO LEFT — col→up, gap→right, board extends rightward; SA@bottom SB@top
            ps = max_col * P
            body_h = ps + SA + SB;  body_w = _RPi_BOARD_H
            body_x = mx - PAD;  body_y = my + SA - body_h
            hdr_rx = body_x;  hdr_ry = my - ps - PAD
            hdr_rw = HDR_H;  hdr_rh = ps + PAD * 2

            def _pin_xy(o): return (mx + (GAP if o.cross_gap else 0), my - o.col_delta * P)

            def _label(dc_, o, name):
                _, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:   # outer row: label outside the board (left)
                    dc_.DrawText(name, body_x - tw - 3, py - th//2)
                else:                 # inner row: label inside board (right of header strip)
                    dc_.DrawText(name, body_x + HDR_H + 2, py - th//2)

            ba_x = body_x + HDR_H;  ba_w = body_w - HDR_H;  ba_cx = ba_x + ba_w // 2
            ba_cy = body_y + body_h // 2
            hole_positions = [(body_x+HDR_H//2, body_y+53), (body_x+body_w-10, body_y+53),
                              (body_x+HDR_H//2, body_y+body_h-16), (body_x+body_w-10, body_y+body_h-16)]
            chip_cx = ba_cx;  chip_cy = body_y + SB + ps // 2
            # Connectors: horizontal stack on TOP edge (SB side), eth rightmost (closest to GPIO reading right→left)
            gl = ba_cx - group_h // 2
            usb1_cy = usb2_cy = body_y + usb_s//2 + 3
            eth_cy  = body_y + eth_s//2 + 3
            usb1_cx = gl + usb_s//2
            usb2_cx = usb1_cx + usb_s + 4
            eth_cx  = usb2_cx + usb_s//2 + 4 + eth_s//2
            name_cx = ba_cx;  name_cy = chip_cy - chip_s//2 - 30

        # ── Ghost ─────────────────────────────────────────────────────────
        if ghost:
            r, g, b = board_color.Red(), board_color.Green(), board_color.Blue()
            gfill = wx.Colour(min(255, r+70), min(255, g+70), min(255, b+70))
            dc.SetBrush(wx.Brush(gfill))
            dc.SetPen(wx.Pen(wx.Colour('#888888'), 1, wx.PENSTYLE_DOT))
            dc.DrawRoundedRectangle(body_x, body_y, body_w, body_h, 4)
            dc.SetBrush(wx.Brush(wx.Colour('#555555')))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(hdr_rx, hdr_ry, hdr_rw, hdr_rh)
            dc.SetBrush(wx.Brush(wx.Colour('#aaaaaa')))
            dc.SetPen(wx.Pen(wx.Colour('#777777'), 1))
            for pin_num, offset in comp_def.pin_offsets.items():
                px, py = _pin_xy(offset)
                if pin_num == 1:
                    dc.DrawRectangle(px - RPi_PIN_R, py - RPi_PIN_R, RPi_PIN_R*2, RPi_PIN_R*2)
                else:
                    dc.DrawCircle(px, py, RPi_PIN_R)
            return

        # ── Selection halo ────────────────────────────────────────────────
        if selected:
            dc.SetBrush(_transparent_brush())
            dc.SetPen(wx.Pen('#00ccff', 3))
            dc.DrawRoundedRectangle(body_x - 4, body_y - 4, body_w + 8, body_h + 8, 6)

        # ── PCB body ──────────────────────────────────────────────────────
        dc.SetBrush(wx.Brush(board_color))
        dc.SetPen(wx.Pen(wx.Colour('#1a1a1a'), 1))
        dc.DrawRoundedRectangle(body_x, body_y, body_w, body_h, 4)

        # ── Black header plastic ───────────────────────────────────────────
        dc.SetBrush(wx.Brush(wx.Colour('#1c1c1c')))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRectangle(hdr_rx, hdr_ry, hdr_rw, hdr_rh)

        # ── Mounting holes ────────────────────────────────────────────────
        for hx, hy in hole_positions:
            dc.SetBrush(wx.Brush(wx.Colour('#c8a800')))
            dc.SetPen(wx.Pen(wx.Colour('#8a7200'), 1))
            dc.DrawCircle(hx, hy, 7)
            dc.SetBrush(wx.Brush(wx.Colour('#2a4030')))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawCircle(hx, hy, 4)

        # ── Processor square ──────────────────────────────────────────────
        dc.SetBrush(wx.Brush(grey))
        dc.SetPen(wx.Pen(dark, 1))
        dc.DrawRectangle(chip_cx - chip_s//2, chip_cy - chip_s//2, chip_s, chip_s)

        # ── Connectors (ethernet + 2× USB) ────────────────────────────────
        dc.SetBrush(wx.Brush(grey))
        dc.SetPen(wx.Pen(dark, 1))
        dc.DrawRectangle(eth_cx  - eth_s//2, eth_cy  - eth_s//2, eth_s,  eth_s)
        dc.DrawRectangle(usb1_cx - usb_s//2, usb1_cy - usb_s//2, usb_s,  usb_s)
        dc.DrawRectangle(usb2_cx - usb_s//2, usb2_cy - usb_s//2, usb_s,  usb_s)

        # ── Pin pads ──────────────────────────────────────────────────────
        dc.SetBrush(wx.Brush(wx.Colour('#c8c8c8')))
        dc.SetPen(wx.Pen(wx.Colour('#888888'), 1))
        for pin_num, offset in comp_def.pin_offsets.items():
            px, py = _pin_xy(offset)
            if pin_num == 1:
                dc.DrawRectangle(px - RPi_PIN_R, py - RPi_PIN_R, RPi_PIN_R*2, RPi_PIN_R*2)
            else:
                dc.DrawCircle(px, py, RPi_PIN_R)

        # ── Pin labels ────────────────────────────────────────────────────
        dc.SetFont(wx.Font(4, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground('#1a1a1a')
        pin_name_map = RPi_PIN_NAMES_LONG if self._rpi_long_labels else comp_def.pin_names
        for pin_num, offset in comp_def.pin_offsets.items():
            _label(dc, offset, pin_name_map.get(pin_num, str(pin_num)))

        # ── Board name + ref ──────────────────────────────────────────────
        r2, g2, b2 = board_color.Red(), board_color.Green(), board_color.Blue()
        luma = 0.299*r2 + 0.587*g2 + 0.114*b2
        dc.SetTextForeground('#ffffff' if luma < 160 else '#222222')
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dname = comp_def.display_name
        nw, nh = dc.GetTextExtent(dname)
        dc.DrawText(dname, name_cx - nw//2, name_cy)
        if ref:
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            rw, rh = dc.GetTextExtent(ref)
            dc.DrawText(ref, name_cx - rw//2, name_cy + nh + 2)

    def _draw_nano_board_at(self, dc: wx.DC, comp_def: ComponentDef,
                             ref: str, mx: int, my: int,
                             selected: bool, ghost: bool = False,
                             flipped: bool = False) -> None:
        """Draw a free-floating PCB module (Arduino Nano / Uno / Teensy …).

        rotation = int(flipped) % 4:
          0 = header top+bottom, board extends right  (landscape, USB/conn on left)
          1 = header left+right, board extends down   (portrait 90° CW,  conn on top)
          2 = header top+bottom, board extends left   (landscape 180°,   conn on right)
          3 = header left+right, board extends up     (portrait 270° CW, conn on bottom)
        (mx, my) = pin-1 canvas position in all orientations.
        """
        rot      = int(flipped) % 4
        bh       = self._body_h(comp_def)   # inner gap between the two header rows (px)
        max_col  = max((o.col_delta for o in comp_def.pin_offsets.values()), default=0)
        P        = MODULE_PIN_PITCH
        PAD      = MODULE_BODY_PAD
        HH       = MODULE_HEADER_H
        PIN_R    = MODULE_PIN_R
        LG       = MODULE_LABEL_GAP
        board_color  = wx.Colour(comp_def.color)
        border_color = wx.Colour('#1a1a1a')
        pad_color    = wx.Colour('#c8c8c8')
        r, g, b  = board_color.Red(), board_color.Green(), board_color.Blue()
        is_nano  = comp_def.type_id == 'Arduino_Nano'

        # ── Per-rotation geometry ──────────────────────────────────────────
        ps = max_col * P   # pin span in the long direction

        # USB connector short dimension: narrower than the full board short side.
        # Centered on the board's short axis; protrudes 12 px, overlaps 4 px.
        USB_SHORT = 22   # px — short side of the USB rectangle

        if rot == 0:
            # Landscape: outer row top, inner row bottom; board extends right.
            # USB connector is on the col-max / D13 end (RIGHT side).
            body_x = mx - PAD;       body_y = my - PAD
            body_w = ps + PAD * 2;   body_h = bh + PAD * 2
            hdr1 = (body_x + 1, body_y,              body_w - 2, HH)   # top (outer)
            hdr2 = (body_x + 1, body_y + body_h - HH, body_w - 2, HH) # bottom (inner)
            silk1 = (body_x+2, body_y+HH,          body_x+body_w-2, body_y+HH)
            silk2 = (body_x+2, body_y+body_h-HH,   body_x+body_w-2, body_y+body_h-HH)

            def _pin_xy(o):
                return mx + o.col_delta * P, my + (bh if o.cross_gap else 0)

            def _label(dc_, o, name):
                px, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:  # outer / top: label above board
                    dc_.DrawText(name, px - tw//2, body_y - th - 2)
                else:                # inner / bottom: label below
                    dc_.DrawText(name, px - tw//2, py + PIN_R + LG)

            inner_cx = body_x + body_w // 2
            inner_cy = body_y + body_h // 2
            text_cx  = inner_cx
            text_cy  = inner_cy
            # USB protrudes RIGHT (D13 / col-max end)
            _uy = body_y + (body_h - USB_SHORT) // 2
            usb_rect = (body_x + body_w - 4, _uy, 16, USB_SHORT)

        elif rot == 1:
            # Portrait 90° CW: outer row right, inner row left; board extends down.
            # USB connector is on the col-max / D13 end (BOTTOM side).
            body_x = mx - bh - PAD;  body_y = my - PAD
            body_w = bh + PAD * 2;   body_h = ps + PAD * 2
            hdr1 = (body_x + body_w - HH, body_y + 1, HH, body_h - 2)  # right (outer)
            hdr2 = (body_x,               body_y + 1, HH, body_h - 2)  # left  (inner)
            silk1 = (body_x+body_w-HH, body_y+2, body_x+body_w-HH, body_y+body_h-2)
            silk2 = (body_x+HH,        body_y+2, body_x+HH,        body_y+body_h-2)

            def _pin_xy(o):
                return mx - (bh if o.cross_gap else 0), my + o.col_delta * P

            def _label(dc_, o, name):
                px, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:  # outer / right: label right of board
                    dc_.DrawText(name, body_x + body_w + 2, py - th//2)
                else:                # inner / left: label left of inner strip (outside board)
                    dc_.DrawText(name, body_x - tw - 2, py - th//2)

            inner_cx = body_x + body_w // 2
            inner_cy = body_y + body_h // 2
            text_cx  = inner_cx
            text_cy  = inner_cy
            # USB protrudes DOWN (D13 / col-max end)
            _ux = body_x + (body_w - USB_SHORT) // 2
            usb_rect = (_ux, body_y + body_h - 4, USB_SHORT, 16)

        elif rot == 2:
            # Landscape 180°: outer row bottom, inner row top; board extends left.
            # USB connector is on the col-max / D13 end (LEFT side, since cols go left).
            body_x = mx - ps - PAD;  body_y = my - bh - PAD
            body_w = ps + PAD * 2;   body_h = bh + PAD * 2
            hdr1 = (body_x + 1, body_y + body_h - HH, body_w - 2, HH) # bottom (outer)
            hdr2 = (body_x + 1, body_y,               body_w - 2, HH) # top    (inner)
            silk1 = (body_x+2, body_y+body_h-HH,   body_x+body_w-2, body_y+body_h-HH)
            silk2 = (body_x+2, body_y+HH,          body_x+body_w-2, body_y+HH)

            def _pin_xy(o):
                return mx - o.col_delta * P, my - (bh if o.cross_gap else 0)

            def _label(dc_, o, name):
                px, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:  # outer / bottom: label below board
                    dc_.DrawText(name, px - tw//2, py + PIN_R + LG)
                else:                # inner / top: label above board
                    dc_.DrawText(name, px - tw//2, body_y - th - 2)

            inner_cx = body_x + body_w // 2
            inner_cy = body_y + body_h // 2
            text_cx  = inner_cx
            text_cy  = inner_cy
            # USB protrudes LEFT (D13 / col-max end, since rot==2 cols go left)
            _uy = body_y + (body_h - USB_SHORT) // 2
            usb_rect = (body_x - 12, _uy, 16, USB_SHORT)

        else:  # rot == 3
            # Portrait 270° CW: outer row left, inner row right; board extends up.
            # USB connector is on the col-max / D13 end (TOP side, since cols go up).
            body_x = mx - PAD;        body_y = my - ps - PAD
            body_w = bh + PAD * 2;    body_h = ps + PAD * 2
            hdr1 = (body_x,               body_y + 1, HH, body_h - 2)  # left  (outer)
            hdr2 = (body_x + body_w - HH, body_y + 1, HH, body_h - 2)  # right (inner)
            silk1 = (body_x+HH,        body_y+2, body_x+HH,        body_y+body_h-2)
            silk2 = (body_x+body_w-HH, body_y+2, body_x+body_w-HH, body_y+body_h-2)

            def _pin_xy(o):
                return mx + (bh if o.cross_gap else 0), my - o.col_delta * P

            def _label(dc_, o, name):
                px, py = _pin_xy(o)
                tw, th = dc_.GetTextExtent(name)
                if not o.cross_gap:  # outer / left: label left of board
                    dc_.DrawText(name, body_x - tw - 2, py - th//2)
                else:                # inner / right: label right of inner strip (outside board)
                    dc_.DrawText(name, body_x + body_w + 2, py - th//2)

            inner_cx = body_x + body_w // 2
            inner_cy = body_y + body_h // 2
            text_cx  = inner_cx
            text_cy  = inner_cy
            # USB protrudes UP (D13 / col-max end, since rot==3 cols go up)
            _ux = body_x + (body_w - USB_SHORT) // 2
            usb_rect = (_ux, body_y - 12, USB_SHORT, 16)

        landscape = rot in (0, 2)

        # ── Ghost mode ───────────────────────────────────────────────────
        if ghost:
            gfill = wx.Colour(min(255, r+70), min(255, g+70), min(255, b+70))
            dc.SetBrush(wx.Brush(gfill))
            dc.SetPen(wx.Pen(wx.Colour('#888888'), 1, wx.PENSTYLE_DOT))
            dc.DrawRoundedRectangle(body_x, body_y, body_w, body_h, 3)
            dc.SetBrush(wx.Brush(wx.Colour('#555555')))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(*hdr1)
            dc.DrawRectangle(*hdr2)
            dc.SetBrush(wx.Brush(wx.Colour('#aaaaaa')))
            dc.SetPen(wx.Pen(wx.Colour('#777777'), 1))
            for pin_num, offset in comp_def.pin_offsets.items():
                px, py = _pin_xy(offset)
                if pin_num == 1:
                    dc.DrawRectangle(px - PIN_R, py - PIN_R, PIN_R*2, PIN_R*2)
                else:
                    dc.DrawCircle(px, py, PIN_R)
            if landscape:
                dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.SetTextForeground('#555555')
                for pin_num, offset in comp_def.pin_offsets.items():
                    _label(dc, offset, comp_def.pin_names.get(pin_num, str(pin_num)))
            return

        # ── Selection halo ────────────────────────────────────────────────
        if selected:
            dc.SetBrush(_transparent_brush())
            dc.SetPen(wx.Pen('#00ccff', 3))
            dc.DrawRoundedRectangle(body_x - 4, body_y - 4, body_w + 8, body_h + 8, 6)

        # ── PCB body ──────────────────────────────────────────────────────
        dc.SetBrush(wx.Brush(board_color))
        dc.SetPen(wx.Pen(border_color, 1))
        dc.DrawRoundedRectangle(body_x, body_y, body_w, body_h, 3)

        # ── USB / connector block (protrudes from the pin-1 end) ──────────
        if is_nano:
            dc.SetBrush(wx.Brush(wx.Colour('#d8d8d8')))
            dc.SetPen(wx.Pen(wx.Colour('#aaaaaa'), 1))
            dc.DrawRectangle(*usb_rect)

        # ── Black header strips ───────────────────────────────────────────
        dc.SetBrush(wx.Brush(wx.Colour('#1c1c1c')))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRectangle(*hdr1)
        dc.DrawRectangle(*hdr2)

        # ── Silkscreen lines (depth accent inside each strip) ─────────────
        silk_c = wx.Colour(min(255, r+60), min(255, g+60), min(255, b+60))
        dc.SetPen(wx.Pen(silk_c, 1))
        dc.DrawLine(*silk1)
        dc.DrawLine(*silk2)

        # ── ATmega / SoC chip — diamond in the inner area ────────────────
        if is_nano:
            chip_r = 14
            dc.SetBrush(wx.Brush(wx.Colour('#111111')))
            dc.SetPen(wx.Pen(wx.Colour('#444444'), 1))
            dc.DrawPolygon([
                wx.Point(inner_cx,          inner_cy - chip_r),
                wx.Point(inner_cx + chip_r, inner_cy),
                wx.Point(inner_cx,          inner_cy + chip_r),
                wx.Point(inner_cx - chip_r, inner_cy),
            ])

        # ── Header pads ───────────────────────────────────────────────────
        dc.SetBrush(wx.Brush(pad_color))
        dc.SetPen(wx.Pen(wx.Colour('#888888'), 1))
        for pin_num, offset in comp_def.pin_offsets.items():
            px, py = _pin_xy(offset)
            if pin_num == 1:
                dc.DrawRectangle(px - PIN_R, py - PIN_R, PIN_R*2, PIN_R*2)
            else:
                dc.DrawCircle(px, py, PIN_R)

        # ── Pin labels ────────────────────────────────────────────────────────
        # Arduino Uno: basic labels always; toggle switches to extended (SPI/I2C/~).
        # Other modules: always basic. Portrait: horizontal text via _label.
        # Landscape Uno extended: rotated 90° via GC so adjacent labels don't overlap.
        if comp_def.type_id == 'Arduino_Uno':
            _name_map = ARDUINO_UNO_FN_NAMES if self._dip_fn_labels else comp_def.pin_names
        else:
            _name_map = comp_def.pin_names

        if landscape and self._dip_fn_labels and comp_def.type_id == 'Arduino_Uno':
            # 90° rotated labels: each label is ~6 px wide (font height) in x,
            # so they never overlap on the 18 px pin pitch.
            # +π/2 = text goes upward (outer strip above board for rot 0, inner above for rot 2)
            # -π/2 = text goes downward (inner strip below board for rot 0, outer below for rot 2)
            gc_lbl = _make_gc(dc)
            if gc_lbl is not None:
                _font_lbl = wx.Font(4, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                    wx.FONTWEIGHT_NORMAL)
                gc_lbl.SetFont(gc_lbl.CreateFont(_font_lbl, wx.Colour('#1a1a1a')))
                for pin_num, offset in comp_def.pin_offsets.items():
                    px, _ = _pin_xy(offset)
                    name = _name_map.get(pin_num, str(pin_num))
                    _, th = gc_lbl.GetTextExtent(name)
                    gc_lbl.PushState()
                    outer = not offset.cross_gap
                    above_board = (outer and rot == 0) or (not outer and rot == 2)
                    if above_board:
                        gc_lbl.Translate(float(px), float(body_y) - LG)
                        gc_lbl.Rotate(-math.pi / 2)
                    else:
                        gc_lbl.Translate(float(px), float(body_y + body_h) + LG)
                        gc_lbl.Rotate(math.pi / 2)
                    gc_lbl.DrawText(name, 0.0, -float(th) / 2)
                    gc_lbl.PopState()
            # else: skip rotated labels on DCs that don't support GraphicsContext
        else:
            # Horizontal labels — works for basic landscape and all portrait rotations
            dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#1a1a1a')
            for pin_num, offset in comp_def.pin_offsets.items():
                _label(dc, offset, _name_map.get(pin_num, str(pin_num)))

        # ── Board name + ref ──────────────────────────────────────────────
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        dname = comp_def.display_name
        # Arduino Uno defers its label to the extras block (the chip is drawn
        # after this section and would cover the text otherwise).
        if comp_def.type_id != 'Arduino_Uno':
            txt_color = wx.Colour('#ffffff' if luma < 160 else '#222222')
            if not landscape:
                # Portrait: the inner area is too narrow for horizontal text — rotate 90°.
                # rot==1 cols go down → label reads top-to-bottom (+90°, i.e. π/2).
                # rot==3 cols go up   → label reads bottom-to-top (-90°, i.e. -π/2).
                # Offset ps//4 px toward USB end (col-max) to sit between diamond and USB.
                angle  = math.pi / 2 if rot == 1 else -math.pi / 2
                lbl_cy = float(text_cy) + (ps // 4) * (1 if rot == 1 else -1)
                gc_nm = _make_gc(dc)
                if gc_nm is not None:
                    _fn = wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
                    _fr = wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
                    gc_nm.SetFont(gc_nm.CreateFont(_fn, txt_color))
                    nw, nh = gc_nm.GetTextExtent(dname)
                    if ref:
                        gc_nm.SetFont(gc_nm.CreateFont(_fr, txt_color))
                        rw, rh = gc_nm.GetTextExtent(ref)
                        gap = 2
                        total = nw + gap + rw
                        gc_nm.SetFont(gc_nm.CreateFont(_fn, txt_color))
                        gc_nm.PushState()
                        gc_nm.Translate(float(text_cx), lbl_cy)
                        gc_nm.Rotate(angle)
                        gc_nm.DrawText(dname, -total / 2, -nh / 2)
                        gc_nm.PopState()
                        gc_nm.SetFont(gc_nm.CreateFont(_fr, txt_color))
                        gc_nm.PushState()
                        gc_nm.Translate(float(text_cx), lbl_cy)
                        gc_nm.Rotate(angle)
                        gc_nm.DrawText(ref, -total / 2 + nw + gap, -rh / 2)
                        gc_nm.PopState()
                    else:
                        gc_nm.PushState()
                        gc_nm.Translate(float(text_cx), lbl_cy)
                        gc_nm.Rotate(angle)
                        gc_nm.DrawText(dname, -nw / 2, -nh / 2)
                        gc_nm.PopState()
                # else: skip rotated name on DCs that don't support GraphicsContext
            else:
                dc.SetTextForeground(txt_color)
                dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
                nw, nh = dc.GetTextExtent(dname)
                # Nano: shift label between diamond and USB port (toward col-max end).
                # rot==0 USB is right (+x), rot==2 USB is left (−x).
                nano_x_off = (ps // 4) * (1 if rot == 0 else -1) if is_nano else 0
                lbl_cy = text_cy
                lbl_cx = text_cx + nano_x_off

                if ref:
                    dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_NORMAL))
                    rw, rh = dc.GetTextExtent(ref)
                    gap = 2
                    total_h = nh + gap + rh
                    dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_BOLD))
                    dc.DrawText(dname, lbl_cx - nw//2, lbl_cy - total_h//2)
                    dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_NORMAL))
                    dc.DrawText(ref,   lbl_cx - rw//2, lbl_cy - total_h//2 + nh + gap)
                else:
                    dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_BOLD))
                    dc.DrawText(dname, lbl_cx - nw//2, lbl_cy - nh//2)

        # ── Arduino Uno extras: split strips + ATmega + USB + barrel jack ──
        if comp_def.type_id == 'Arduino_Uno':
            HALF = P // 2

            # Erase gaps in the inner header strip (new layout: cols 0-2 empty,
            # col 11 empty between VIN at col 10 and A0 at col 12).
            dc.SetBrush(wx.Brush(board_color))
            dc.SetPen(wx.Pen(board_color, 1))
            if rot == 0:   # inner strip at bottom; cols go right
                # Left buffer: cols 0-2 have no inner pins
                dc.DrawRectangle(body_x, body_y + body_h - HH - 1,
                                 PAD + 3*P - HALF, HH + 2)
                # Gap at col 11 (between VIN col 10 and A0 col 12)
                dc.DrawRectangle(mx + 10*P + HALF, body_y + body_h - HH - 1,
                                 P, HH + 2)
            elif rot == 1: # inner strip on left; cols go down
                # Left buffer: cols 0-2 have no inner pins
                dc.DrawRectangle(body_x - 1, body_y,
                                 HH + 2, PAD + 3*P - HALF)
                # Gap at col 11
                dc.DrawRectangle(body_x - 1, my + 10*P + HALF,
                                 HH + 2, P)
            elif rot == 2: # inner strip at top; cols go left
                # Left buffer: cols 0-2 are on the right side (col 0 = mx)
                dc.DrawRectangle(mx - 3*P + HALF, body_y - 1,
                                 PAD + 3*P - HALF, HH + 2)
                # Gap at col 11
                dc.DrawRectangle(mx - 12*P + HALF, body_y - 1,
                                 P, HH + 2)
            else:          # inner strip on right; cols go up
                # Left buffer: cols 0-2 are at the bottom (col 0 = my)
                dc.DrawRectangle(body_x + body_w - HH - 1, my - 3*P + HALF,
                                 HH + 2, PAD + 3*P - HALF)
                # Gap at col 11
                dc.DrawRectangle(body_x + body_w - HH - 1, my - 12*P + HALF,
                                 HH + 2, P)

            # Fill the notch in the outer (digital) header strip between D8 (col 9)
            # and D7 (col 10) — centred at col 9.5 from the USB end.
            dc.SetBrush(wx.Brush(wx.Colour('#1c1c1c')))
            dc.SetPen(wx.TRANSPARENT_PEN)
            if rot == 0:   # outer strip at top; notch at mx + 9*P + HALF
                dc.DrawRectangle(mx + 9*P + HALF - 2, body_y, 5, HH)
            elif rot == 1: # outer strip on right; notch at my + 9*P + HALF
                dc.DrawRectangle(body_x + body_w - HH, my + 9*P + HALF - 2,
                                 HH, 5)
            elif rot == 2: # outer strip at bottom; notch at mx - 9*P - HALF
                dc.DrawRectangle(mx - 10*P + HALF - 2, body_y + body_h - HH,
                                 5, HH)
            else:          # outer strip on left; notch at my - 9*P - HALF
                dc.DrawRectangle(body_x, my - 10*P + HALF - 2,
                                 HH, 5)

            # ATmega328P — drawn in all four rotations.
            # USB is on the col-0 end; chip goes toward the non-USB side.
            dc.SetBrush(wx.Brush(wx.Colour('#111111')))
            dc.SetPen(wx.Pen(wx.Colour('#444444'), 1))
            if landscape:
                inner_h = body_h - 2 * HH - 2
                chip_w = int(body_w * 0.45)
                chip_h = max(14, inner_h * 2 // 5)
                chip_cx_u = body_x + int(body_w * (0.60 if rot == 0 else 0.40))
                chip_cy_u = inner_cy + (inner_h // 4 if rot == 0 else -inner_h // 4)
            else:  # portrait
                inner_w = body_w - 2 * HH - 2
                chip_w = max(14, inner_w * 2 // 5)
                chip_h = int(body_h * 0.45)
                # rot 1: col-0 at TOP → chip toward bottom (60 % from top)
                # rot 3: col-0 at BOTTOM → chip toward top (40 % from top)
                chip_cy_u = body_y + int(body_h * (0.60 if rot == 1 else 0.40))
                # Shift toward inner strip: LEFT for rot==1, RIGHT for rot==3
                chip_cx_u = inner_cx + (-inner_w // 4 if rot == 1 else inner_w // 4)
            dc.DrawRectangle(chip_cx_u - chip_w // 2, chip_cy_u - chip_h // 2,
                             chip_w, chip_h)

            # USB port (grey) + barrel jack (dark) on the col-0 (LEFT) end,
            # all 4 rotations. Stacked adjacent, centred on the board's short axis.
            # Connectors sit mostly on the module with only a small stub protruding.
            _OVL    = 8     # how far connector rect starts inside the board edge
            USB_PRO = 22    # total rect length (square USB: 22 × 22)
            BJ_PRO  = 22    # same length as USB
            USB_H   = 22    # USB connector short side (square)
            BJ_H    = 14    # barrel jack short side (thinner than USB)
            GAP     = 6     # gap between USB and barrel jack

            # Centre of the board's SHORT axis for connector positioning.
            # In landscape body_h is the short dimension; in portrait body_w is.
            short_mid = body_h // 2 if landscape else body_w // 2
            stack   = USB_H + GAP + BJ_H          # 22 + 6 + 14 = 42 px
            usb_off = short_mid - stack // 2       # offset from body_y / body_x
            bj_off  = usb_off + USB_H + GAP

            dc.SetBrush(wx.Brush(wx.Colour('#d8d8d8')))
            dc.SetPen(wx.Pen(wx.Colour('#aaaaaa'), 1))
            if rot == 0:    # col-0 end is LEFT
                dc.DrawRectangle(body_x - USB_PRO + _OVL, body_y + usb_off, USB_PRO, USB_H)
            elif rot == 1:  # col-0 end is TOP
                dc.DrawRectangle(body_x + usb_off, body_y - USB_PRO + _OVL, USB_H, USB_PRO)
            elif rot == 2:  # col-0 end is RIGHT
                dc.DrawRectangle(body_x + body_w - _OVL, body_y + usb_off, USB_PRO, USB_H)
            else:           # col-0 end is BOTTOM
                dc.DrawRectangle(body_x + usb_off, body_y + body_h - _OVL, USB_H, USB_PRO)

            dc.SetBrush(wx.Brush(wx.Colour('#555555')))
            dc.SetPen(wx.Pen(wx.Colour('#333333'), 1))
            if rot == 0:
                dc.DrawRectangle(body_x - BJ_PRO + _OVL, body_y + bj_off,  BJ_PRO, BJ_H)
            elif rot == 1:
                dc.DrawRectangle(body_x + bj_off,  body_y - BJ_PRO + _OVL, BJ_H,   BJ_PRO)
            elif rot == 2:
                dc.DrawRectangle(body_x + body_w - _OVL, body_y + bj_off,  BJ_PRO, BJ_H)
            else:
                dc.DrawRectangle(body_x + bj_off,  body_y + body_h - _OVL, BJ_H,   BJ_PRO)

            # ── Label (drawn last so it renders on top of the chip) ───────────
            # Position relative to chip edge so chip never obscures the text.
            # Landscape: full name above (rot 0) or below (rot 2) the chip.
            # Portrait:  short name; above chip for rot 1, below for rot 3.
            dc.SetTextForeground('#ffffff')
            dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            _lbl = dname if landscape else 'Uno R3'
            _nw, _nh = dc.GetTextExtent(_lbl)
            if ref:
                dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                _rw, _rh = dc.GetTextExtent(ref)
                _tot = _nh + 2 + _rh
                dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
            else:
                _rw = _rh = 0
                _tot = _nh
            # rot 0 (landscape): chip near bottom → label above chip
            # rot 1 (portrait):  chip near bottom → label above chip
            # rot 2 (landscape): chip near top    → label below chip
            # rot 3 (portrait):  chip near top    → label below chip
            if rot in (0, 1):
                _ty = chip_cy_u - chip_h // 2 - _tot - 3
            else:
                _ty = chip_cy_u + chip_h // 2 + 3
            _tx = chip_cx_u if landscape else text_cx
            dc.DrawText(_lbl, _tx - _nw//2, _ty)
            if ref:
                dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
                dc.DrawText(ref, _tx - _rw//2, _ty + _nh + 2)

    # ------------------------------------------------------------------
    # Module pin synchronisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _body_h(comp_def: ComponentDef) -> int:
        """Return the inner body height (px) between the two horizontal pin rows."""
        return _MODULE_BODY_H.get(comp_def.type_id, MODULE_BODY_H)

    def _compute_module_pins(self, ref: str, comp_def: ComponentDef,
                              mx: int, my: int,
                              flipped: bool = False) -> Dict[Tuple[str, int], Tuple[int, int]]:
        """Return (ref, pin_num) → (canvas_x, canvas_y) for all module pins.

        (mx, my) = pin-1 canvas position.
        For RPi: flipped=True rotates 90° (col_delta → y, cross_gap → x).
        For others: col_delta → x, cross_gap → bottom row, flipped swaps rows.
        """
        result: Dict[Tuple[str, int], Tuple[int, int]] = {}
        if comp_def.type_id == 'RPi_Pico':
            GAP = self._body_h(comp_def)   # 8px row gap
            rot = int(flipped) % 4
            P  = MODULE_PIN_PITCH
            for pin_num, offset in comp_def.pin_offsets.items():
                inner = offset.cross_gap
                c = offset.col_delta
                if rot == 0:
                    x = mx + c * P;     y = my + (GAP if inner else 0)
                elif rot == 1:
                    x = mx - (GAP if inner else 0);  y = my + c * P
                elif rot == 2:
                    x = mx - c * P;     y = my - (GAP if inner else 0)
                else:   # rot == 3
                    x = mx + (GAP if inner else 0);  y = my - c * P
                result[(ref, pin_num)] = (x, y)
            return result
        bh = self._body_h(comp_def)
        P  = MODULE_PIN_PITCH
        rot = int(flipped) % 4
        for pin_num, offset in comp_def.pin_offsets.items():
            c, cg = offset.col_delta, offset.cross_gap
            if rot == 0:
                x, y = mx + c * P,          my + (bh if cg else 0)
            elif rot == 1:
                x, y = mx - (bh if cg else 0), my + c * P
            elif rot == 2:
                x, y = mx - c * P,          my - (bh if cg else 0)
            else:  # rot == 3
                x, y = mx + (bh if cg else 0), my - c * P
            result[(ref, pin_num)] = (x, y)
        return result

    def _sync_module_pins(self, ref: str) -> None:
        """Update CanvasLayout pin positions for one placed module."""
        p = self.board.get_placement(ref)
        pos = self.board.get_module_position(ref)
        if p is None or pos is None:
            return
        comp_def = ALL_DEFS.get(p.type_id)
        if comp_def is None or not comp_def.is_module:
            return
        mx, my = pos
        self.layout.clear_module_ref(ref)
        self.layout.set_module_pin_xy(
            self._compute_module_pins(ref, comp_def, mx, my, flipped=p.flipped)
        )

    def _populate_module_pins(self) -> None:
        """Sync layout pin positions for ALL placed modules (call after board reload)."""
        for ref in list(self.board.placements):
            self._sync_module_pins(ref)

    def _draw_axial_component(self, dc: wx.DC, comp_def: ComponentDef,
                              placed: PlacedComponent, ref: str,
                              p1: Tuple[int, int], p2: Tuple[int, int],
                              selected: bool) -> None:
        """
        Draw a 2-pin axial or round component between two pixel positions.
        Works at any angle: horizontal, vertical, or diagonal.
        Pin 1 is at p1, pin 2 is at p2.
        """
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1:
            return

        angle = math.atan2(dy, dx)
        ux, uy = dx / length, dy / length  # unit vector p1→p2
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2

        body_color = wx.Colour(placed.led_color if placed.led_color else comp_def.color)
        border_color = wx.Colour('#333333')
        pen_w = 2 if selected else 1

        if placed.type_id == 'LED':
            r = 13.0
            r_inner = 10.0  # inner lens ring radius (thin ring between r_inner and r)

            # Lead lines from each pin to the circle edge
            dc.SetPen(wx.Pen('#888888', 3))
            dc.DrawLine(int(x1), int(y1), int(mx - ux * r), int(my - uy * r))
            dc.DrawLine(int(mx + ux * r), int(my + uy * r), int(x2), int(y2))

            # Glow halo when simulation shows the LED is forward-biased
            vf = self._led_forward_voltage(ref)
            led_on = vf is not None and vf >= 1.0
            if led_on:
                intensity = min(1.0, (vf - 1.0) / 1.0)   # 0→1 over Vf 1V→2V
                gc_glow = _make_gc(dc)
                if gc_glow is not None:
                    bc = body_color
                    alpha = int(180 * intensity)
                    glow_r = 32.0
                    center_c = wx.Colour(bc.Red(), bc.Green(), bc.Blue(), alpha)
                    edge_c   = wx.Colour(bc.Red(), bc.Green(), bc.Blue(), 0)
                    glow_brush = gc_glow.CreateRadialGradientBrush(
                        mx, my, mx, my, glow_r, center_c, edge_c)
                    gc_glow.SetBrush(glow_brush)
                    gc_glow.SetPen(gc_glow.CreatePen(
                        wx.GraphicsPenInfo(wx.TransparentColour).Width(0)))
                    gc_glow.DrawEllipse(mx - glow_r, my - glow_r, 2 * glow_r, 2 * glow_r)

            # All rotated details via GC; -x direction = cathode (pin 1 = K)
            gc = _make_gc(dc)
            if gc is not None:
                gc.Translate(mx, my)
                gc.Rotate(angle)

                # Outer circle body
                gc.SetBrush(gc.CreateBrush(wx.Brush(body_color)))
                _circ = gc.CreatePath()
                _circ.AddEllipse(-r, -r, 2 * r, 2 * r)
                gc.FillPath(_circ)

                # Cathode marker: dark arc segment on the -x (cathode/K) side
                stripe_x = r * 0.62
                y_isect  = math.sqrt(r * r - stripe_x * stripe_x)
                theta    = math.atan2(y_isect, stripe_x)
                sp = gc.CreatePath()
                sp.MoveToPoint(-stripe_x, -y_isect)
                sp.AddArc(0, 0, r, -(math.pi - theta), math.pi - theta, False)
                sp.AddLineToPoint(-stripe_x, -y_isect)
                sp.CloseSubpath()
                gc.SetBrush(gc.CreateBrush(wx.Brush(wx.Colour('#111111'))))
                gc.FillPath(sp)

                # Inner lens: brighter when LED is on, dimmer when off
                bc = body_color
                lens_t    = 0.55 if led_on else 0.35
                hilite_t  = 0.97 if led_on else 0.82
                lens_color = wx.Colour(
                    min(255, int(bc.Red()   + (255 - bc.Red())   * lens_t)),
                    min(255, int(bc.Green() + (255 - bc.Green()) * lens_t)),
                    min(255, int(bc.Blue()  + (255 - bc.Blue())  * lens_t)),
                )
                highlight = wx.Colour(
                    min(255, int(bc.Red()   + (255 - bc.Red())   * hilite_t)),
                    min(255, int(bc.Green() + (255 - bc.Green()) * hilite_t)),
                    min(255, int(bc.Blue()  + (255 - bc.Blue())  * hilite_t)),
                )
                fx, fy = -r_inner * 0.25, -r_inner * 0.25
                lens_grad = gc.CreateRadialGradientBrush(
                    0, 0, fx, fy, r_inner, highlight, lens_color)
                gc.SetBrush(lens_grad)
                _lens = gc.CreatePath()
                _lens.AddEllipse(-r_inner, -r_inner, 2 * r_inner, 2 * r_inner)
                gc.FillPath(_lens)

                # Outer circle border (on top of everything)
                gc.SetBrush(gc.CreateBrush(_transparent_brush()))
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
                gc.DrawEllipse(-r, -r, 2 * r, 2 * r)

                # Inner concentric ring (lens edge)
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(1)))
                gc.DrawEllipse(-r_inner, -r_inner, 2 * r_inner, 2 * r_inner)
            else:
                # Fallback for SVGFileDC: plain circle + cathode stripe rectangle
                dc.SetBrush(wx.Brush(body_color))
                dc.SetPen(wx.Pen(border_color, pen_w))
                dc.DrawCircle(int(mx), int(my), int(r))
                # Cathode stripe: dark rect on pin-1 (x1) side
                stripe_x = int(r * 0.62)
                dc.SetBrush(wx.Brush('#111111'))
                dc.SetPen(wx.Pen('#111111', 0))
                dc.DrawRectangle(int(x1) - 1, int(my - math.sqrt(r*r - stripe_x**2)),
                                 int(stripe_x + 2), int(2 * math.sqrt(r*r - stripe_x**2)))
                dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(border_color, pen_w))
                dc.DrawCircle(int(mx), int(my), int(r))
        elif placed.type_id == 'C_POL':
            # Electrolytic capacitor — top-down view: circle with a black stripe
            # on the negative (pin-2) side and a "+" marker on the positive side.
            r = 13.0
            # Lead lines from pins to circle edge
            dc.SetPen(wx.Pen('#888888', 3))
            dc.DrawLine(int(x1), int(y1), int(mx - ux * r), int(my - uy * r))
            dc.DrawLine(int(mx + ux * r), int(my + uy * r), int(x2), int(y2))
            # Circle body (fill only; border redrawn after stripe in gc)
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawCircle(int(mx), int(my), int(r))
            # Rotated details via GraphicsContext
            stripe_x = r * 0.55
            gc = _make_gc(dc)
            if gc is not None:
                gc.Translate(mx, my)
                gc.Rotate(angle)
                # Black stripe on pin-2 (−) side — circular arc segment
                y_isect  = math.sqrt(r * r - stripe_x * stripe_x)
                theta    = math.atan2(y_isect, stripe_x)
                sp = gc.CreatePath()
                sp.MoveToPoint(stripe_x, -y_isect)
                sp.AddArc(0, 0, r, -theta, theta, True)
                sp.AddLineToPoint(stripe_x, -y_isect)
                sp.CloseSubpath()
                stripe = wx.Colour('#111111')
                gc.SetBrush(gc.CreateBrush(wx.Brush(stripe)))
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(stripe).Width(0)))
                gc.DrawPath(sp)
                # Redraw circle border on top
                gc.SetBrush(gc.CreateBrush(_transparent_brush()))
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
                gc.DrawEllipse(-r, -r, 2 * r, 2 * r)
                # "+" text on pin-1 (+) side
                font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD)
                gc.SetFont(gc.CreateFont(font, wx.WHITE))
                tw, th = gc.GetTextExtent('+')
                gc.DrawText('+', -r + 3, -th / 2)
            else:
                # Fallback: simple stripe rectangle on pin-2 side, then redraw border
                y_isect = math.sqrt(r * r - stripe_x * stripe_x)
                dc.SetBrush(wx.Brush('#111111'))
                dc.SetPen(wx.Pen('#111111', 0))
                dc.DrawRectangle(int(mx + stripe_x), int(my - y_isect),
                                 int(r - stripe_x), int(2 * y_isect))
                dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(border_color, pen_w))
                dc.DrawCircle(int(mx), int(my), int(r))
                dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
                dc.SetTextForeground('#ffffff')
                tw, th = dc.GetTextExtent('+')
                dc.DrawText('+', int(mx - r + 3), int(my - th // 2))
        else:
            # Axial pill (R, C, L, D, D_Zener, C_POL …)
            # Body occupies the middle of the span, capped so long angled
            # placements keep a normal component body with longer leads.
            body_half = min(max(length * 0.25, 8.0), 1.25 * PITCH)

            # Lead attachment points on the body surface
            bx1, by1 = mx - ux * body_half, my - uy * body_half   # near pin 1
            bx2, by2 = mx + ux * body_half, my + uy * body_half   # near pin 2

            # Lead lines
            dc.SetPen(wx.Pen('#888888', 3))
            dc.DrawLine(int(x1), int(y1), int(bx1), int(by1))
            dc.DrawLine(int(bx2), int(by2), int(x2), int(y2))

            # Body — via GraphicsContext for rotation; DC fallback for SVGFileDC
            gc = _make_gc(dc)
            body_w = body_half * 2
            body_h = 14.0
            bx = int(mx - body_half)
            by = int(my - body_h / 2)

            if gc is not None:
                gc.Translate(mx, my)
                gc.Rotate(angle)
                body_path = gc.CreatePath()
                body_path.AddRoundedRectangle(-body_half, -body_h / 2, body_w, body_h, 4)
                gc.SetBrush(gc.CreateBrush(wx.Brush(body_color)))
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))

                if placed.type_id == 'C':
                    gc.DrawRectangle(-body_half, -body_h / 2, body_w, body_h)
                    comp = self.netlist.components.get(ref) if self.netlist else None
                    font_small = wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                         wx.FONTWEIGHT_BOLD)
                    gc.SetFont(gc.CreateFont(font_small, wx.Colour('#ffffff')))
                    val_lbl = comp.value if comp else ''
                    label = f'{ref} {val_lbl}' if val_lbl else ref
                    lw, lh = gc.GetTextExtent(label)
                    gc.DrawText(label, -lw / 2, -lh / 2)
                elif placed.type_id == 'R':
                    _rp = _make_res_path(gc, body_half)
                    gc.FillPath(_rp)
                    gc.StrokePath(_rp)
                else:
                    gc.FillPath(body_path)
                    gc.StrokePath(body_path)

                if placed.type_id == 'R' and self.netlist:
                    comp = self.netlist.components.get(ref)
                    ohms = _parse_ohms(comp.value) if comp else None
                    bands = _resistor_bands(ohms) if ohms is not None else None
                    if bands:
                        positions = [
                            -body_half + 3,
                            -body_half + 9,
                            -body_half + 15,
                            body_half - 8,
                        ]
                        for bx_pos, bcolor in zip(positions, bands):
                            band_color = wx.Colour(bcolor)
                            bh = _res_body_half_height(bx_pos + 2.5, body_half)
                            band_path = gc.CreatePath()
                            band_path.AddRectangle(bx_pos, -bh, 5, 2 * bh)
                            gc.SetBrush(gc.CreateBrush(wx.Brush(band_color)))
                            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(band_color).Width(1)))
                            gc.FillPath(band_path)
                    # Redraw outer border on top to clean up band overflow at edges
                    gc.SetBrush(gc.CreateBrush(_transparent_brush()))
                    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
                    gc.StrokePath(_rp)

                elif placed.type_id == 'D':
                    _stripe = wx.Colour('#cccccc')
                    gc.SetBrush(gc.CreateBrush(wx.Brush(_stripe)))
                    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(_stripe).Width(0)))
                    gc.DrawRectangle(-body_half + 4, -body_h / 2, 4, body_h)
                    # Redraw body border on top so the stripe doesn't obscure it
                    gc.SetBrush(gc.CreateBrush(_transparent_brush()))
                    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
                    gc.StrokePath(body_path)
                elif placed.type_id == 'D_Zener':
                    # Zener cathode marker: same occupied area as the original
                    # cathode band, but rectangular so there is no rounded blob.
                    _stripe = wx.Colour('#cccccc')
                    gc.SetBrush(gc.CreateBrush(wx.Brush(_stripe)))
                    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(_stripe).Width(0)))
                    gc.DrawRectangle(-body_half + 4, -body_h / 2, 4, body_h)
                    gc.SetBrush(gc.CreateBrush(_transparent_brush()))
                    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
                    gc.StrokePath(body_path)

            else:
                # Fallback for SVGFileDC: components are always horizontal on a breadboard
                dc.SetBrush(wx.Brush(body_color))
                dc.SetPen(wx.Pen(border_color, pen_w))
                if placed.type_id == 'C':
                    dc.DrawRectangle(bx, by, int(body_w), int(body_h))
                    comp = self.netlist.components.get(ref) if self.netlist else None
                    val_lbl = comp.value if comp else ''
                    label = f'{ref} {val_lbl}' if val_lbl else ref
                    dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                       wx.FONTWEIGHT_BOLD))
                    dc.SetTextForeground('#ffffff')
                    tw, th = dc.GetTextExtent(label)
                    dc.DrawText(label, int(mx) - tw // 2, int(my) - th // 2)
                elif placed.type_id == 'R':
                    dc.DrawRoundedRectangle(int(mx - body_half), int(my - _R_END_HH),
                                             int(_R_CAP_W), int(2 * _R_END_HH), int(_R_CAP_R))
                    dc.DrawRectangle(int(mx - body_half + _R_CAP_W), int(my - _R_MID_HH),
                                     int(2 * (body_half - _R_CAP_W)), int(2 * _R_MID_HH))
                    dc.DrawRoundedRectangle(int(mx + body_half - _R_CAP_W), int(my - _R_END_HH),
                                             int(_R_CAP_W), int(2 * _R_END_HH), int(_R_CAP_R))
                else:
                    dc.DrawRoundedRectangle(bx, by, int(body_w), int(body_h), 4)

                if placed.type_id == 'R' and self.netlist:
                    comp = self.netlist.components.get(ref)
                    ohms = _parse_ohms(comp.value) if comp else None
                    bands = _resistor_bands(ohms) if ohms is not None else None
                    if bands:
                        positions = [
                            -body_half + 3,
                            -body_half + 9,
                            -body_half + 15,
                            body_half - 8,
                        ]
                        dc.SetPen(wx.Pen(wx.Colour(0, 0, 0, 0), 0))
                        for bx_pos, bcolor in zip(positions, bands):
                            bh = _res_body_half_height(bx_pos + 2.5, body_half)
                            dc.SetBrush(wx.Brush(wx.Colour(bcolor)))
                            dc.DrawRectangle(int(mx + bx_pos), int(my - bh), 5, int(2 * bh))
                    dc.SetBrush(_transparent_brush())
                    dc.SetPen(wx.Pen(border_color, pen_w))
                    dc.DrawRoundedRectangle(int(mx - body_half), int(my - _R_END_HH),
                                             int(_R_CAP_W), int(2 * _R_END_HH), int(_R_CAP_R))
                    dc.DrawRoundedRectangle(int(mx + body_half - _R_CAP_W), int(my - _R_END_HH),
                                             int(_R_CAP_W), int(2 * _R_END_HH), int(_R_CAP_R))

                elif placed.type_id == 'D':
                    # DC fallback: inset stripe stays clear of corner overflow
                    dc.SetBrush(wx.Brush('#cccccc'))
                    dc.SetPen(wx.Pen('#cccccc', 0))
                    dc.DrawRectangle(bx + 4, by, 4, int(body_h))
                    dc.SetBrush(_transparent_brush())
                    dc.SetPen(wx.Pen(border_color, pen_w))
                    dc.DrawRoundedRectangle(bx, by, int(body_w), int(body_h), 4)
                elif placed.type_id == 'D_Zener':
                    dc.SetBrush(wx.Brush('#cccccc'))
                    dc.SetPen(wx.Pen('#cccccc', 0))
                    dc.DrawRectangle(bx + 4, by, 4, int(body_h))
                    dc.SetBrush(_transparent_brush())
                    dc.SetPen(wx.Pen(border_color, pen_w))
                    dc.DrawRoundedRectangle(bx, by, int(body_w), int(body_h), 4)

    def _draw_pushbutton(self, dc: wx.DC, comp_def: ComponentDef,
                         placed: PlacedComponent, ref: str,
                         p1: Tuple[int, int], p2: Tuple[int, int],
                         selected: bool) -> None:
        """Draw an SPST momentary push button between two pin positions."""
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1:
            return

        angle = math.atan2(dy, dx)
        ux, uy = dx / length, dy / length
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2

        body_half = length * 0.30
        body_h = 16.0
        button_r = 7.0
        pen_w = 2 if selected else 1
        body_color = wx.Colour(comp_def.color)
        border_color = wx.Colour('#555555')

        bx1 = mx - ux * body_half
        by1 = my - uy * body_half
        bx2 = mx + ux * body_half
        by2 = my + uy * body_half

        # Lead lines from each pin to body edge
        dc.SetPen(wx.Pen('#888888', 3))
        dc.DrawLine(int(x1), int(y1), int(bx1), int(by1))
        dc.DrawLine(int(bx2), int(by2), int(x2), int(y2))

        gc = _make_gc(dc)
        if gc is not None:
            gc.Translate(mx, my)
            gc.Rotate(angle)
            # Grey housing body
            gc.SetBrush(gc.CreateBrush(wx.Brush(body_color)))
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(border_color).Width(pen_w)))
            gc.DrawRoundedRectangle(-body_half, -body_h / 2, body_half * 2, body_h, 3.0)
            # Round black button cap
            gc.SetBrush(gc.CreateBrush(wx.Brush(wx.Colour('#202020'))))
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(wx.Colour('#111111')).Width(1)))
            gc.DrawEllipse(-button_r, -button_r, button_r * 2, button_r * 2)
        else:
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen(border_color, pen_w))
            dc.DrawRoundedRectangle(int(mx - body_half), int(my - body_h / 2),
                                    int(body_half * 2), int(body_h), 3)
            dc.SetBrush(wx.Brush('#202020'))
            dc.SetPen(wx.Pen('#111111', 1))
            dc.DrawCircle(int(mx), int(my), int(button_r))

        # Ref label below the body (perpendicular to component axis, outward)
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground('#222222')
        tw, th = dc.GetTextExtent(ref)
        lx = int(mx + uy * (body_h / 2 + 3)) - tw // 2
        ly = int(my - ux * (body_h / 2 + 3)) - th // 2
        dc.DrawText(ref, lx, ly)

    def _draw_slider_switch(self, dc: wx.DC, comp_def: ComponentDef,
                             placed: PlacedComponent, ref: str,
                             x_min: int, x_max: int, y_min: int, y_max: int,
                             in_top: bool, selected: bool) -> None:
        """Draw an SPDT or SP3T slider switch."""
        lay = self.layout
        pen_w = 2 if selected else 1
        body_color = wx.Colour(comp_def.color)    # brown
        face_color = wx.Colour('#999999')          # grey faceplate
        slider_color = wx.Colour('#222222')        # black slider knob
        border_color = wx.Colour('#3a2a1a')        # dark brown outline
        BODY_H  = 18
        FACE_H  = 10
        FACE_INS = 2
        SLIDER_W = max(10, PITCH - 4)
        SLIDER_H = 6

        body_left  = x_min - 4
        body_right = x_max + 4
        body_w = body_right - body_left
        cx = (body_left + body_right) // 2

        if in_top:
            body_top    = y_min - BODY_H - 2
            body_bottom = y_min - 2
            # Pin stubs
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#555555', 1))
            for hole in placed.pin_holes.values():
                xy = lay.hole_xy(hole)
                if xy:
                    dc.DrawRectangle(xy[0] - 1, body_bottom, 3, 4)
            # Housing body
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen(border_color, pen_w))
            dc.DrawRoundedRectangle(body_left, body_top, body_w, BODY_H, 3)
            # Faceplate
            face_left = body_left + FACE_INS
            face_top  = body_top  + FACE_INS
            face_w    = body_w - 2 * FACE_INS
            dc.SetBrush(wx.Brush(face_color))
            dc.SetPen(wx.Pen('#777777', 1))
            dc.DrawRoundedRectangle(face_left, face_top, face_w, FACE_H, 2)
            # Slider knob (centred on faceplate)
            sx = cx - SLIDER_W // 2
            sy = face_top + (FACE_H - SLIDER_H) // 2
            dc.SetBrush(wx.Brush(slider_color))
            dc.SetPen(wx.Pen('#111111', 1))
            dc.DrawRoundedRectangle(sx, sy, SLIDER_W, SLIDER_H, 2)
            # Pin labels at holes
            dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#444444')
            for pin_num in sorted(placed.pin_holes):
                xy = lay.hole_xy(placed.pin_holes[pin_num])
                if xy:
                    name = comp_def.pin_names.get(pin_num, str(pin_num))
                    tw, th = dc.GetTextExtent(name)
                    dc.DrawText(name, xy[0] - tw // 2, body_bottom + 5)
            # Ref label below pin labels
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#222222')
            tw, th = dc.GetTextExtent(ref)
            dc.DrawText(ref, cx - tw // 2, body_bottom + 14)
        else:
            body_top    = y_max + 2
            body_bottom = y_max + 2 + BODY_H
            # Pin stubs
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#555555', 1))
            for hole in placed.pin_holes.values():
                xy = lay.hole_xy(hole)
                if xy:
                    dc.DrawRectangle(xy[0] - 1, y_max - 2, 3, 4)
            # Housing body
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen(border_color, pen_w))
            dc.DrawRoundedRectangle(body_left, body_top, body_w, BODY_H, 3)
            # Faceplate (at the far side, away from holes)
            face_left = body_left + FACE_INS
            face_top  = body_bottom - FACE_INS - FACE_H
            face_w    = body_w - 2 * FACE_INS
            dc.SetBrush(wx.Brush(face_color))
            dc.SetPen(wx.Pen('#777777', 1))
            dc.DrawRoundedRectangle(face_left, face_top, face_w, FACE_H, 2)
            # Slider knob
            sx = cx - SLIDER_W // 2
            sy = face_top + (FACE_H - SLIDER_H) // 2
            dc.SetBrush(wx.Brush(slider_color))
            dc.SetPen(wx.Pen('#111111', 1))
            dc.DrawRoundedRectangle(sx, sy, SLIDER_W, SLIDER_H, 2)
            # Pin labels
            dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#444444')
            for pin_num in sorted(placed.pin_holes):
                xy = lay.hole_xy(placed.pin_holes[pin_num])
                if xy:
                    name = comp_def.pin_names.get(pin_num, str(pin_num))
                    tw, th = dc.GetTextExtent(name)
                    dc.DrawText(name, xy[0] - tw // 2, y_max - th - 4)
            # Ref label above pin labels
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#222222')
            tw, th = dc.GetTextExtent(ref)
            dc.DrawText(ref, cx - tw // 2, y_max - th - 14)

    def _draw_probe_flag(self, dc: wx.DC,
                          hx: int, hy: int,
                          fcx: int, fcy_top: int,
                          label: str, color: str) -> None:
        """Draw a static oscilloscope probe icon: colored barrel with small pointer tip."""
        body_w, body_h = 28, 12
        stripe_w = 6
        tip_h = 5   # downward-pointing triangle height

        c       = wx.Colour(color)
        dark    = wx.Colour(42, 42, 42)
        outline = wx.Colour(90, 90, 90)

        body_left = fcx - body_w // 2
        body_bot  = fcy_top + body_h

        # Small downward triangle "tip" below the barrel
        tip_pts = [
            wx.Point(fcx - 4, body_bot),
            wx.Point(fcx + 4, body_bot),
            wx.Point(fcx,     body_bot + tip_h),
        ]
        dc.SetBrush(wx.Brush(dark))
        dc.SetPen(wx.Pen(outline, 1))
        dc.DrawPolygon(tip_pts)

        # Body dark background
        dc.SetBrush(wx.Brush(dark))
        dc.SetPen(wx.Pen(outline, 1))
        dc.DrawRoundedRectangle(body_left, fcy_top, body_w, body_h, 3)

        # Colored left stripe (rounded on left, straight on right)
        dc.SetBrush(wx.Brush(c))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRoundedRectangle(body_left, fcy_top, stripe_w, body_h, 3)
        dc.DrawRectangle(body_left + 3, fcy_top, stripe_w - 3, body_h)

        # Channel label
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground('#ffffff')
        tw, th = dc.GetTextExtent(label)
        text_x = body_left + stripe_w + 1
        text_w = body_w - stripe_w - 1
        dc.DrawText(label, text_x + (text_w - tw) // 2,
                    fcy_top + (body_h - th) // 2)

    def _probe_flag_pos(self, name: str, hx: int, hy: int) -> Tuple[int, int]:
        """Return (body_center_x, body_top_y) for the given probe, applying its offset."""
        dx, dy = self.board.get_probe_label_offset(name)
        return hx + dx, hy - 20 + dy

    def _draw_probes(self, dc: wx.DC) -> None:
        for name in PROBE_NAMES:
            hole = self.board.get_probe_hole(name)
            if hole is None:
                continue
            xy = self.layout.hole_xy(hole)
            if xy is None:
                continue
            hx, hy = int(xy[0]), int(xy[1])
            fcx, fcy = self._probe_flag_pos(name, hx, hy)
            meta = PROBE_META[name]
            if name == self._hover_probe_name:
                color = '#cc2222'
            elif name == self._selected_probe:
                color = '#ffffff'
            else:
                color = meta['color']
            self._draw_probe_flag(dc, hx, hy, fcx, fcy, meta['label'], color)

        # Placement preview
        if self.mode == MODE_PROBE and self._placing_probe and self._probe_hover:
            xy = self.layout.hole_xy(self._probe_hover)
            if xy:
                meta = PROBE_META[self._placing_probe]
                # Draw a faint preview
                c = wx.Colour(meta['color'])
                dc.SetPen(wx.Pen(wx.Colour(c.Red(), c.Green(), c.Blue(), 140), 2,
                                 wx.PENSTYLE_DOT))
                hx, hy = int(xy[0]), int(xy[1])
                flag_h = 14
                fy = hy - flag_h - 12
                dc.DrawLine(hx, hy - HOLE_R, hx, fy + flag_h)
                dc.SetBrush(wx.Brush(wx.Colour(c.Red(), c.Green(), c.Blue(), 140)))
                dc.SetPen(wx.Pen('#444444', 1, wx.PENSTYLE_DOT))
                dc.DrawRoundedRectangle(hx - 12, fy, 24, flag_h, 3)

    def _draw_scope_probes(self, dc: wx.DC) -> None:
        for ch_idx, (hole, color_hex, label) in self._scope_probes.items():
            xy = self.layout.hole_xy(hole)
            if xy is None:
                continue
            hx, hy = int(xy[0]), int(xy[1])
            self._draw_probe_flag(dc, hx, hy, hx, hy - 20, label, color_hex)

    def _baseboard_bg_is_dark(self) -> bool:
        """Return True if the area behind binding-post labels is perceptually dark."""
        hex_color = self.baseboard_color if self.show_baseboard else '#f0f0f0'
        c = wx.Colour(hex_color)
        lum = 0.299 * c.Red() + 0.587 * c.Green() + 0.114 * c.Blue()
        return lum < 128

    def _draw_terminals(self, dc: wx.DC) -> None:
        dark_bg = self._baseboard_bg_is_dark()
        lay = self.layout
        for name in TERMINAL_NAMES:
            t = Terminal(name)
            xy = lay.hole_xy(t)
            if xy is None:
                continue
            cx, cy = xy
            body_color, highlight_color = TERM_COLORS[name]
            assigned = self.board.get_terminal_net(name)

            # ── Drop shadow ──────────────────────────────────────────────
            dc.SetBrush(wx.Brush('#666666'))
            dc.SetPen(wx.Pen('#666666', 0))
            dc.DrawCircle(cx + 3, cy + 3, TERM_R)

            # ── Outer knurled body ────────────────────────────────────────
            # Bright white ring when assigned; dark border otherwise
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen('#ffffff' if assigned else '#111111',
                             3 if assigned else 2))
            dc.DrawCircle(cx, cy, TERM_R)

            # Knurl ticks — short radial lines around the outer rim
            n_ticks = 18
            r_tick_outer = TERM_R - 2
            r_tick_inner = TERM_R - 9
            dc.SetPen(wx.Pen('#111111', 2))
            for i in range(n_ticks):
                angle = 2 * math.pi * i / n_ticks
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                dc.DrawLine(
                    cx + int(r_tick_inner * cos_a),
                    cy + int(r_tick_inner * sin_a),
                    cx + int(r_tick_outer * cos_a),
                    cy + int(r_tick_outer * sin_a),
                )

            # ── Inner raised cap (the rotatable clamp nut) ───────────────
            r_cap = TERM_R - 10
            dc.SetBrush(wx.Brush(highlight_color))
            dc.SetPen(wx.Pen('#333333', 1))
            dc.DrawCircle(cx, cy, r_cap)

            # ── Central threaded post ─────────────────────────────────────
            r_post = TERM_R // 3
            dc.SetBrush(wx.Brush('#4a4a4a'))
            dc.SetPen(wx.Pen('#222222', 1))
            dc.DrawCircle(cx, cy, r_post)

            # ── Wire entry hole ───────────────────────────────────────────
            r_hole = TERM_R // 5
            dc.SetBrush(wx.Brush('#0a0a0a'))
            dc.SetPen(wx.Pen('#000000', 1))
            dc.DrawCircle(cx, cy, r_hole)

            # Glint on hole edge (top-left)
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#888888', 0))
            dc.DrawCircle(cx - r_hole // 2, cy - r_hole // 2, 2)

            # Name label and net assignment — direction depends on binding post side
            label_below = not lay.binding_post_side.startswith('bottom')
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#ffffff' if dark_bg else '#222222')
            tw, th = dc.GetTextExtent(name)
            if label_below:
                lbl_y  = cy + TERM_R + 3
                net_y  = cy + TERM_R + 15
            else:
                lbl_y  = cy - TERM_R - th - 3
                net_y  = cy - TERM_R - th - 15
            dc.DrawText(name, cx - tw // 2, lbl_y)

            # Net assignment (small)
            net_label = assigned if assigned else ''
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground(
                ('#ffffff' if assigned else '#aaaaaa') if dark_bg
                else ('#446644' if assigned else '#999999')
            )
            max_w = TERM_R * 2 + 8
            while dc.GetTextExtent(net_label).Width > max_w and len(net_label) > 4:
                net_label = net_label[:-2] + '…'
            nlw = dc.GetTextExtent(net_label).Width
            dc.DrawText(net_label, cx - nlw // 2, net_y)

    def _draw_ghost(self, dc: wx.DC) -> None:
        """Draw a semi-transparent component preview at the drag position."""
        ghost = self._ghost
        if ghost is None:
            return
        lay = self.layout
        comp_def = ghost.comp_def

        # Free-floating module: draw ghost at current mouse position
        if comp_def.is_module:
            mx, my = int(self._ghost_pos[0]), int(self._ghost_pos[1])
            self._draw_module_board_at(dc, comp_def, ref='', mx=mx, my=my,
                                       selected=False, ghost=True,
                                       flipped=ghost.flipped)
            return

        # Two-pin two-step placement: use locked first-click + hovered second-click.
        # For diode-family (LED/D/D_Zener) first-click = anode (pin 2); the
        # ghost body is drawn with cathode at p1 side, so swap for those types.
        _diode_family = comp_def.type_id in ('LED', 'D', 'D_Zener')
        if comp_def.pin_count == 2 and not comp_def.is_dip:
            if self._place_pin1 is not None:
                # First click is locked; second click (cathode for diodes) follows mouse
                p1_xy = lay.hole_xy(self._place_pin1)
                pin2_hole = ghost.anchor
                p2_xy = lay.hole_xy(pin2_hole) if pin2_hole else None
                if p2_xy is None:
                    p2_xy = self._ghost_pos
                if p1_xy:
                    # Swap so cathode stripe lands at the second-click (p2) side
                    if _diode_family:
                        self._draw_ghost_2pin(dc, comp_def, p2_xy, p1_xy)
                    else:
                        self._draw_ghost_2pin(dc, comp_def, p1_xy, p2_xy)
                    # Draw locked first-click indicator (anode for diodes)
                    dc.SetBrush(wx.Brush(wx.Colour(255, 200, 0, 180)))
                    dc.SetPen(wx.Pen('#ffcc00', 2))
                    dc.DrawCircle(p1_xy[0], p1_xy[1], HOLE_R + 5)
                return
            else:
                # First click not yet locked: preview at hovered hole
                if ghost.anchor is None:
                    return
                p1_xy = lay.hole_xy(ghost.anchor)
                if p1_xy is None:
                    return
                px_off = PITCH * 4 * (-1 if ghost.flipped else 1)
                p2_xy = (p1_xy[0] + px_off, p1_xy[1])
                # Swap so the hovered hole shows as anode (no stripe) for diodes
                if _diode_family:
                    self._draw_ghost_2pin(dc, comp_def, p2_xy, p1_xy)
                else:
                    self._draw_ghost_2pin(dc, comp_def, p1_xy, p2_xy)
                # Highlight the hover hole as the first-click target
                dc.SetBrush(wx.Brush(wx.Colour(255, 200, 0, 100)))
                dc.SetPen(wx.Pen('#ffcc0088', 2))
                dc.DrawCircle(p1_xy[0], p1_xy[1], HOLE_R + 5)
                return

        if ghost.anchor is None:
            return
        try:
            pin_holes = comp_def.place(ghost.anchor, flipped=ghost.flipped)
        except (AssertionError, IndexError, KeyError):
            return

        holes_xy = [lay.hole_xy(h) for h in pin_holes.values()]
        holes_xy = [xy for xy in holes_xy if xy is not None]
        if not holes_xy:
            return
        xs = [xy[0] for xy in holes_xy]
        ys = [xy[1] for xy in holes_xy]

        _TO92_TYPES    = frozenset({'NPN', 'PNP', 'JFET_N', 'JFET_P', 'BS170', 'NMOS', 'PMOS'})
        _SLIDER_TYPES  = frozenset({'SPDT', 'SP3T'})
        base_color = wx.Colour(comp_def.color)
        r0, g0, b0 = base_color.Red(), base_color.Green(), base_color.Blue()
        # Lighten without alpha: wx.Brush alpha is unreliable on GTK (may render transparent)
        ghost_color = wx.Colour((r0 * 2 + 255) // 3, (g0 * 2 + 255) // 3, (b0 * 2 + 255) // 3)
        _ghost_pen  = wx.GraphicsPenInfo(
            wx.Colour(max(0, r0 - 40), max(0, g0 - 40), max(0, b0 - 40))).Width(1)

        if comp_def.type_id in _SLIDER_TYPES:
            sample_hole = next(iter(pin_holes.values()))
            in_top = isinstance(sample_hole, TieHole) and sample_hole.row in TOP_ROWS
            x_min_g, x_max_g = min(xs), max(xs)
            y_min_g, y_max_g = min(ys), max(ys)
            BODY_H_G  = 18
            FACE_H_G  = 10
            FACE_INS_G = 2
            body_left_g  = float(x_min_g - 4)
            body_right_g = float(x_max_g + 4)
            body_w_g = body_right_g - body_left_g
            face_col = wx.Colour(0xcc, 0xcc, 0xcc)
            gc = _make_gc(dc)
            if gc is None:
                return
            dot_pen = gc.CreatePen(_ghost_pen)
            if in_top:
                body_top_g = float(y_min_g - BODY_H_G - 2)
                gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
                gc.SetPen(dot_pen)
                gc.DrawRoundedRectangle(body_left_g, body_top_g, body_w_g, float(BODY_H_G), 3.0)
                gc.SetBrush(gc.CreateBrush(wx.Brush(face_col)))
                gc.DrawRoundedRectangle(
                    body_left_g + FACE_INS_G, body_top_g + FACE_INS_G,
                    body_w_g - 2 * FACE_INS_G, float(FACE_H_G), 2.0)
            else:
                body_top_g = float(y_max_g + 2)
                gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
                gc.SetPen(dot_pen)
                gc.DrawRoundedRectangle(body_left_g, body_top_g, body_w_g, float(BODY_H_G), 3.0)
                face_top_g = body_top_g + BODY_H_G - FACE_INS_G - FACE_H_G
                gc.SetBrush(gc.CreateBrush(wx.Brush(face_col)))
                gc.DrawRoundedRectangle(
                    body_left_g + FACE_INS_G, face_top_g,
                    body_w_g - 2 * FACE_INS_G, float(FACE_H_G), 2.0)

        elif comp_def.type_id in _TO92_TYPES:
            # Ammo-pack ghost for TO-92
            sample_hole = next(iter(pin_holes.values()))
            in_top = isinstance(sample_hole, TieHole) and sample_hole.row in TOP_ROWS
            x_min_g, x_max_g = min(xs), max(xs)
            cx_mid_g    = float((x_min_g + x_max_g) // 2)
            body_half_g = 12.0
            r_body_g    = body_half_g
            pin_y_g     = min(ys) if in_top else max(ys)
            flat_y_g    = float(pin_y_g)

            inset_g     = 3.0
            step_g      = (2 * body_half_g - 2 * inset_g) / 2
            attach_xs_g = [cx_mid_g - body_half_g + inset_g + i * step_g for i in range(3)]
            pin_xs_g    = sorted(xy[0] for xy in holes_xy)
            dc.SetPen(wx.Pen(wx.Colour(0x88, 0x88, 0x88), 3))
            for px_g, ax_g in zip(pin_xs_g, attach_xs_g):
                dc.DrawLine(int(px_g), pin_y_g, int(ax_g), int(flat_y_g))

            dome_up_g = not bool(ghost.flipped)
            gc = _make_gc(dc)
            if gc is not None:
                path = gc.CreatePath()
                path.MoveToPoint(cx_mid_g - body_half_g, flat_y_g)
                path.AddLineToPoint(cx_mid_g + body_half_g, flat_y_g)
                path.AddArc(cx_mid_g, flat_y_g, r_body_g, 0.0, math.pi, not dome_up_g)
                path.CloseSubpath()
                gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
                gc.SetPen(gc.CreatePen(_ghost_pen))
                gc.DrawPath(path)
            else:
                dc.SetBrush(wx.Brush(ghost_color))
                dc.SetPen(wx.Pen(wx.Colour(max(0, r0 - 40), max(0, g0 - 40), max(0, b0 - 40)), 1))
                if dome_up_g:
                    dc.DrawArc(int(cx_mid_g + body_half_g), int(flat_y_g),
                               int(cx_mid_g - body_half_g), int(flat_y_g),
                               int(cx_mid_g), int(flat_y_g))
                else:
                    dc.DrawArc(int(cx_mid_g - body_half_g), int(flat_y_g),
                               int(cx_mid_g + body_half_g), int(flat_y_g),
                               int(cx_mid_g), int(flat_y_g))

            # Pin name labels on the side away from the body (ghost variant)
            dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#444444')
            label_gap_g = 4
            for pin_num_g, hole_g in pin_holes.items():
                pxy_g = lay.hole_xy(hole_g)
                if pxy_g is None:
                    continue
                name_g = comp_def.pin_names.get(pin_num_g, str(pin_num_g))
                ptw_g, pth_g = dc.GetTextExtent(name_g)
                if dome_up_g:
                    dc.DrawText(name_g, pxy_g[0] - ptw_g // 2,
                                int(pin_y_g) + label_gap_g)
                else:
                    dc.DrawText(name_g, pxy_g[0] - ptw_g // 2,
                                int(pin_y_g) - pth_g - label_gap_g)
        else:
            body_rect = wx.Rect(min(xs) - 4, min(ys) - 6,
                                max(xs) - min(xs) + 8, max(ys) - min(ys) + 12)

            if comp_def.is_dip and not comp_def.is_module:
                # Ghost legs — use a plain light colour; dc alpha is unreliable on GTK
                dc.SetBrush(wx.Brush(wx.Colour(0xaa, 0xaa, 0xaa)))
                dc.SetPen(wx.Pen(wx.Colour(0xaa, 0xaa, 0xaa), 1))
                for pin, hole in pin_holes.items():
                    xy = lay.hole_xy(hole)
                    if xy is None:
                        continue
                    hx, hy = xy
                    if isinstance(hole, TieHole) and hole.row in TOP_ROWS:
                        dc.DrawRectangle(hx - 1, body_rect.GetTop() - 6, 3, 7)
                    elif isinstance(hole, TieHole) and hole.row in BOT_ROWS:
                        dc.DrawRectangle(hx - 1, body_rect.GetBottom() - 1, 3, 7)

            # Body via GraphicsContext
            gc = _make_gc(dc)
            if gc is not None:
                gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
                gc.SetPen(gc.CreatePen(_ghost_pen))
                gc.DrawRoundedRectangle(float(body_rect.GetX()), float(body_rect.GetY()),
                                        float(body_rect.GetWidth()), float(body_rect.GetHeight()),
                                        4.0)

            # Pin-1 orientation marker (DIP: dot on body edge; POT: dot on top edge)
            p1_hole = pin_holes.get(1)
            p1_xy = lay.hole_xy(p1_hole) if p1_hole else None
            if p1_xy:
                dc.SetBrush(wx.Brush('#ffffff'))
                dc.SetPen(wx.Pen('#333333', 1))
                if comp_def.is_dip:
                    if isinstance(p1_hole, TieHole) and p1_hole.row in TOP_ROWS:
                        dot_y = body_rect.GetY() + 12
                    else:
                        dot_y = body_rect.GetBottom() - 12
                else:
                    dot_y = body_rect.GetTop() + 3
                dc.DrawCircle(p1_xy[0], dot_y, 3)

    def _draw_ghost_2pin(self, dc: wx.DC, comp_def: ComponentDef,
                         p1_xy: Tuple[int, int], p2_xy: Tuple[int, int]) -> None:
        """Draw a semi-transparent 2-pin ghost body between two pixel coordinates."""
        x1, y1 = float(p1_xy[0]), float(p1_xy[1])
        x2, y2 = float(p2_xy[0]), float(p2_xy[1])
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1:
            return

        angle = math.atan2(dy, dx)
        ux, uy = dx / length, dy / length
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2

        base = wx.Colour(comp_def.color)
        r0, g0, b0 = base.Red(), base.Green(), base.Blue()
        ghost_color = wx.Colour((r0 * 2 + 255) // 3, (g0 * 2 + 255) // 3, (b0 * 2 + 255) // 3)
        border_pen = gc_pen = wx.GraphicsPenInfo(
            wx.Colour(max(0, r0 - 40), max(0, g0 - 40), max(0, b0 - 40))).Width(1)

        if comp_def.type_id == 'LED':
            r = 13.0
            r_inner = 10.0
            dc.SetPen(wx.Pen(wx.Colour(0x88, 0x88, 0x88), 3))
            dc.DrawLine(int(x1), int(y1), int(mx - ux * r), int(my - uy * r))
            dc.DrawLine(int(mx + ux * r), int(my + uy * r), int(x2), int(y2))
            gc = _make_gc(dc)
            if gc is None:
                return
            gc.Translate(mx, my)
            gc.Rotate(angle)
            gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
            gc.SetPen(gc.CreatePen(border_pen))
            gc.DrawEllipse(-r, -r, 2 * r, 2 * r)
            # Cathode arc (K = pin 1, at -x after rotation)
            stripe_x = r * 0.62
            y_isect = math.sqrt(r * r - stripe_x * stripe_x)
            theta = math.atan2(y_isect, stripe_x)
            sp = gc.CreatePath()
            sp.MoveToPoint(-stripe_x, -y_isect)
            sp.AddArc(0, 0, r, -(math.pi - theta), math.pi - theta, False)
            sp.AddLineToPoint(-stripe_x, -y_isect)
            sp.CloseSubpath()
            k_col = wx.Colour(0x11, 0x11, 0x11)
            gc.SetBrush(gc.CreateBrush(wx.Brush(k_col)))
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(k_col).Width(0)))
            gc.DrawPath(sp)
            gc.SetBrush(gc.CreateBrush(_transparent_brush()))
            gc.SetPen(gc.CreatePen(border_pen))
            gc.DrawEllipse(-r_inner, -r_inner, 2 * r_inner, 2 * r_inner)
            return

        if comp_def.type_id == 'C_POL':
            r = 13.0
            dc.SetPen(wx.Pen(wx.Colour(0x88, 0x88, 0x88), 3))
            dc.DrawLine(int(x1), int(y1), int(mx - ux * r), int(my - uy * r))
            dc.DrawLine(int(mx + ux * r), int(my + uy * r), int(x2), int(y2))
            gc = _make_gc(dc)
            if gc is None:
                return
            gc.Translate(mx, my)
            gc.Rotate(angle)
            gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
            gc.SetPen(gc.CreatePen(border_pen))
            gc.DrawEllipse(-r, -r, 2 * r, 2 * r)
            # Negative stripe on pin-2 (+x) side
            stripe_x = r * 0.55
            y_isect = math.sqrt(r * r - stripe_x * stripe_x)
            theta = math.atan2(y_isect, stripe_x)
            sp = gc.CreatePath()
            sp.MoveToPoint(stripe_x, -y_isect)
            sp.AddArc(0, 0, r, -theta, theta, True)
            sp.AddLineToPoint(stripe_x, -y_isect)
            sp.CloseSubpath()
            stripe = wx.Colour(0x11, 0x11, 0x11)
            gc.SetBrush(gc.CreateBrush(wx.Brush(stripe)))
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(stripe).Width(0)))
            gc.DrawPath(sp)
            gc.SetBrush(gc.CreateBrush(_transparent_brush()))
            gc.SetPen(gc.CreatePen(border_pen))
            gc.DrawEllipse(-r, -r, 2 * r, 2 * r)
            font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_BOLD)
            gc.SetFont(gc.CreateFont(font, wx.Colour(0xff, 0xff, 0xff)))
            tw, th = gc.GetTextExtent('+')
            gc.DrawText('+', -r + 3, -th / 2)
            return

        if comp_def.type_id == 'SPST':
            # Push button ghost: grey rounded housing + dark button cap
            body_half_pb = length * 0.30
            button_r_pb = 7.0
            body_h_pb = 16.0
            bx1_pb = mx - ux * body_half_pb
            by1_pb = my - uy * body_half_pb
            bx2_pb = mx + ux * body_half_pb
            by2_pb = my + uy * body_half_pb
            dc.SetPen(wx.Pen(wx.Colour(0x88, 0x88, 0x88), 3))
            dc.DrawLine(int(x1), int(y1), int(bx1_pb), int(by1_pb))
            dc.DrawLine(int(bx2_pb), int(by2_pb), int(x2), int(y2))
            gc = _make_gc(dc)
            if gc is None:
                return
            gc.Translate(mx, my)
            gc.Rotate(angle)
            gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
            gc.SetPen(gc.CreatePen(border_pen))
            gc.DrawRoundedRectangle(-body_half_pb, -body_h_pb / 2,
                                    body_half_pb * 2, body_h_pb, 3.0)
            btn_col = wx.Colour(0x22, 0x22, 0x22)
            gc.SetBrush(gc.CreateBrush(wx.Brush(btn_col)))
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(btn_col).Width(1)))
            gc.DrawEllipse(-button_r_pb, -button_r_pb, button_r_pb * 2, button_r_pb * 2)
            return

        body_half = min(max(length * 0.25, 8.0), 1.25 * PITCH)

        bx1, by1 = mx - ux * body_half, my - uy * body_half
        bx2, by2 = mx + ux * body_half, my + uy * body_half

        dc.SetPen(wx.Pen(wx.Colour(0x88, 0x88, 0x88), 3))
        dc.DrawLine(int(x1), int(y1), int(bx1), int(by1))
        dc.DrawLine(int(bx2), int(by2), int(x2), int(y2))

        gc = _make_gc(dc)
        if gc is None:
            return
        gc.Translate(mx, my)
        gc.Rotate(angle)
        body_w = body_half * 2
        body_h = 14.0
        gc.SetBrush(gc.CreateBrush(wx.Brush(ghost_color)))
        gc.SetPen(gc.CreatePen(border_pen))
        if comp_def.type_id == 'C':
            gc.DrawRectangle(-body_half, -body_h / 2, body_w, body_h)
        elif comp_def.type_id == 'R':
            gc.DrawPath(_make_res_path(gc, body_half))
        else:
            gc.DrawRoundedRectangle(-body_half, -body_h / 2, body_w, body_h, 4)

    def _draw_wire_start_indicator(self, dc: wx.DC) -> None:
        xy = self.layout.hole_xy(self._wire_start)
        if xy:
            dc.SetBrush(wx.Brush(wx.Colour(255, 200, 0, 180)))
            dc.SetPen(wx.Pen('#ffcc00', 2))
            dc.DrawCircle(xy[0], xy[1], HOLE_R + 5)
            # Dashed preview line to current mouse position.
            # wx.PENSTYLE_DOT on a scaled wx.DC does not render on GTK/Linux,
            # so use a GraphicsContext (Cairo) which handles dash patterns correctly.
            mx, my = self._ghost_pos
            gc = _make_gc(dc)
            if gc is not None:
                gc.SetPen(gc.CreatePen(
                    wx.GraphicsPenInfo(wx.Colour(0xff, 0xcc, 0x00))
                    .Width(2)
                    .Style(wx.PENSTYLE_SHORT_DASH)
                ))
                gc.StrokeLine(xy[0], xy[1], mx, my)

    def _draw_annotations(self, dc: wx.DC) -> None:
        """Draw user annotation shapes (lines, rectangles, circles, text) on the canvas."""
        hover = self._hover_ann_idx
        selected = self._selected_ann_idx

        for i, ann in enumerate(self._annotations):
            is_hover = (i == hover)
            is_selected = (i == selected)
            if isinstance(ann, DrawLine):
                color = '#ff4444' if is_hover else ann.color
                dc.SetPen(wx.Pen(wx.Colour(color), ann.width + (2 if is_hover else 0)))
                dc.DrawLine(int(ann.x1), int(ann.y1), int(ann.x2), int(ann.y2))
            elif isinstance(ann, DrawRect):
                color = '#ff4444' if is_hover else ann.color
                if ann.fill:
                    dc.SetBrush(wx.Brush(wx.Colour('#ffcccc' if is_hover else ann.fill_color)))
                else:
                    dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(wx.Colour(color), ann.width + (2 if is_hover else 0)))
                x = int(min(ann.x1, ann.x2)); y = int(min(ann.y1, ann.y2))
                w = int(abs(ann.x2 - ann.x1)); h = int(abs(ann.y2 - ann.y1))
                dc.DrawRectangle(x, y, w, h)
            elif isinstance(ann, DrawCircle):
                color = '#ff4444' if is_hover else ann.color
                if ann.fill:
                    dc.SetBrush(wx.Brush(wx.Colour('#ffcccc' if is_hover else ann.fill_color)))
                else:
                    dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(wx.Colour(color), ann.width + (2 if is_hover else 0)))
                dc.DrawCircle(int(ann.cx), int(ann.cy), int(ann.r))
            elif isinstance(ann, DrawText):
                color = '#ff4444' if is_hover else ann.color
                weight = wx.FONTWEIGHT_BOLD   if ann.bold   else wx.FONTWEIGHT_NORMAL
                style  = wx.FONTSTYLE_ITALIC  if ann.italic else wx.FONTSTYLE_NORMAL
                dc.SetFont(wx.Font(ann.font_size, wx.FONTFAMILY_DEFAULT, style, weight))
                dc.SetTextForeground(wx.Colour(color))
                dc.SetBackgroundMode(wx.TRANSPARENT)
                dc.DrawText(ann.text, int(ann.x), int(ann.y))
            elif isinstance(ann, DrawTextBox):
                bx = int(min(ann.x1, ann.x2)); by = int(min(ann.y1, ann.y2))
                bw = int(abs(ann.x2 - ann.x1)); bh = int(abs(ann.y2 - ann.y1))
                border_color = '#ff4444' if is_hover else ann.color
                if ann.fill:
                    dc.SetBrush(wx.Brush(wx.Colour('#ffcccc' if is_hover else ann.fill_color)))
                else:
                    dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(wx.Colour(border_color), ann.width))
                dc.DrawRectangle(bx, by, bw, bh)
                # Draw wrapped text inside with padding
                PAD = 4
                if ann.text and bw > PAD * 2 and bh > PAD * 2:
                    weight = wx.FONTWEIGHT_BOLD   if ann.bold   else wx.FONTWEIGHT_NORMAL
                    style  = wx.FONTSTYLE_ITALIC  if ann.italic else wx.FONTSTYLE_NORMAL
                    dc.SetFont(wx.Font(ann.font_size, wx.FONTFAMILY_DEFAULT, style, weight))
                    dc.SetTextForeground(wx.Colour('#ff4444' if is_hover else ann.text_color))
                    dc.SetBackgroundMode(wx.TRANSPARENT)
                    _, line_h = dc.GetTextExtent('Ag')
                    max_w = bw - PAD * 2
                    ty = by + PAD
                    for raw_line in ann.text.split('\n'):
                        words = raw_line.split(' ') if raw_line else ['']
                        cur = ''
                        for word in words:
                            test = (cur + ' ' + word).lstrip() if cur else word
                            tw, _ = dc.GetTextExtent(test)
                            if tw > max_w and cur:
                                dc.DrawText(cur, bx + PAD, ty)
                                ty += line_h + 1
                                if ty + line_h > by + bh - PAD:
                                    break
                                cur = word
                            else:
                                cur = test
                        else:
                            if cur and ty + line_h <= by + bh - PAD:
                                dc.DrawText(cur, bx + PAD, ty)
                                ty += line_h + 1
                            continue
                        break
            # Resize handles for selected annotation
            if is_selected:
                dc.SetBrush(wx.Brush(wx.Colour('#1a8cff')))
                dc.SetPen(wx.Pen(wx.Colour('#ffffff'), 1))
                for hx, hy in self._ann_handles(ann):
                    dc.DrawRectangle(int(hx) - 4, int(hy) - 4, 8, 8)

        # In-progress shape preview (solid grey — avoid GTK scaled-DC PENSTYLE_DOT issue)
        if self._draw_start is not None and self._draw_preview is not None:
            x1, y1 = self._draw_start
            x2, y2 = self._draw_preview
            dc.SetPen(wx.Pen(wx.Colour('#999999'), 1))
            dc.SetBrush(_transparent_brush())
            if self.mode == MODE_DRAW_LINE:
                dc.DrawLine(int(x1), int(y1), int(x2), int(y2))
            elif self.mode == MODE_DRAW_RECT:
                x = int(min(x1, x2)); y = int(min(y1, y2))
                w = int(abs(x2 - x1)); h = int(abs(y2 - y1))
                dc.DrawRectangle(x, y, w, h)
            elif self.mode == MODE_DRAW_CIRCLE:
                r = int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
                dc.DrawCircle(int(x1), int(y1), r)
            elif self.mode == MODE_DRAW_TEXTBOX:
                x = int(min(x1, x2)); y = int(min(y1, y2))
                w = int(abs(x2 - x1)); h = int(abs(y2 - y1))
                dc.SetBrush(wx.Brush(wx.Colour(255, 251, 230, 120)))
                dc.DrawRectangle(x, y, w, h)

    def _draw_validation_icons(self, dc: wx.DC) -> None:
        """Draw ⚡ / ? icons at the centroid of each validation issue's holes."""
        if not self._validation_icons:
            return

        ICON_R = 11   # background circle radius
        for cx, cy, kind in self._validation_icons:
            if kind == IssueKind.SHORT:
                bg_color  = '#cc2222'
                symbol    = '⚡'
            elif kind == IssueKind.OPEN_NET:
                bg_color  = '#cc8800'
                symbol    = '?'
            else:
                continue   # UNPLACED has no hole location

            # White halo so the icon is readable over any background
            dc.SetBrush(wx.Brush('#ffffff'))
            dc.SetPen(wx.Pen('#ffffff', 3))
            dc.DrawCircle(cx, cy, ICON_R + 2)

            # Filled badge
            dc.SetBrush(wx.Brush(bg_color))
            dc.SetPen(wx.Pen('#ffffff', 1))
            dc.DrawCircle(cx, cy, ICON_R)

            # Symbol
            dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#ffffff')
            tw, th = dc.GetTextExtent(symbol)
            dc.DrawText(symbol, cx - tw // 2, cy - th // 2)

    def _draw_sim_overlay(self, dc: wx.DC) -> None:
        if not self.show_voltage_labels or self._sim_result is None:
            return
        if getattr(self._sim_result, 'net_voltages', None):
            self._draw_voltage_labels(dc)

    def _draw_voltage_labels(self, dc: wx.DC) -> None:
        """Draw voltage labels at each placed component's first-pin hole (.op results)."""
        if not self.netlist:
            return

        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_BOLD))

        # One label per net — pick the first hole encountered for each net name.
        net_hole: dict = {}
        for ref, placed in self.board.placements.items():
            nets_for = self.netlist.nets_for_ref(ref)
            for pin_num, net in nets_for.items():
                if net.name in net_hole:
                    continue
                voltage = self._sim_result.net_voltages.get(net.name)
                if voltage is None:
                    continue
                hole = placed.pin_holes.get(pin_num)
                if hole is None:
                    continue
                xy = self.layout.hole_xy(hole)
                if xy is None:
                    continue
                net_hole[net.name] = (xy, voltage)

        for net_name, (xy, voltage) in net_hole.items():
            cx, cy = xy
            label = f'{voltage:.2f}V'
            tw, th = dc.GetTextExtent(label)
            bx = cx + 4
            by = cy - th - 5

            # Indicator line from the measured hole to the label
            dc.SetPen(wx.Pen('#1a1a6a', 1))
            dc.DrawLine(cx, cy, bx, by + th + 2)

            dc.SetBrush(wx.Brush('#1a1a6a'))
            dc.SetPen(wx.Pen('#1a1a6a', 0))
            dc.DrawRoundedRectangle(bx, by, tw + 4, th + 2, 2)
            dc.SetTextForeground('#e0e0ff')
            dc.DrawText(label, bx + 2, by + 1)

    def _draw_net_labels(self, dc: wx.DC) -> None:
        """Draw a legend box in the bottom-right corner listing signal nets.

        Only single-endpoint named nets are shown (schematic labels with no
        other placed component on that net — e.g. /Vin, /Vout).
        Called after the zoom/pan transform is reset, so coordinates are
        plain screen pixels.
        """
        in_hl_mode = self.mode == MODE_NET_HIGHLIGHT
        if not self.netlist or (not self.show_net_labels and not in_hl_mode):
            self._net_label_rows = []
            return

        # Collect: net_name → ref of the sole placed component pin
        entries: List[Tuple[str, str, str]] = []   # (actual_name, display_name, ref_summary)
        for net in self.netlist.nets:
            name = net.name
            if name.startswith('Net-(') or name.startswith('unconnected-('):
                continue
            # Net named '0' is the SPICE ground node — show it as 'GND'
            display_name = 'GND' if name == '0' else name
            placed_refs: List[str] = []
            for pn in net.pins:
                h = self.board.hole_for_pin(pn.ref, pn.pin)
                if h is not None and pn.ref not in placed_refs:
                    placed_refs.append(pn.ref)
            if not placed_refs:
                continue
            if len(placed_refs) == 1:
                summary = placed_refs[0]
            else:
                summary = placed_refs[0] + ' +' + str(len(placed_refs) - 1)
            entries.append((name, display_name, summary))

        if not entries:
            self._net_label_rows = []
            return

        BG      = wx.Colour(0x00, 0x70, 0x70, 200)   # semi-transparent teal
        FG      = wx.Colour(0xff, 0xff, 0xff)
        HDR     = wx.Colour(0x00, 0x50, 0x50, 220)
        HL_ROW  = wx.Colour(0xff, 0xcc, 0x00, 200)   # amber highlight for active row

        # Larger, more prominent overlay when the user is actively in highlight mode
        if in_hl_mode:
            PAD     = 12
            ROW_GAP = 6
            _FSZ    = 11
        else:
            PAD     = 9
            ROW_GAP = 4
            _FSZ    = 9

        font_hdr  = wx.Font(_FSZ, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        font_body = wx.Font(_FSZ, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

        dc.SetFont(font_body)
        _, row_h = dc.GetTextExtent('Ag')

        # Measure column widths
        dc.SetFont(font_hdr)
        hdr_mark_w = dc.GetTextExtent('MARK')[0] if in_hl_mode else 0
        hdr_w1, _ = dc.GetTextExtent('Signal')
        hdr_w2, _ = dc.GetTextExtent('Component')
        dc.SetFont(font_body)
        col1_w = hdr_w1
        col2_w = hdr_w2
        for _actual_name, net_name, ref in entries:
            w1, _ = dc.GetTextExtent(net_name)
            w2, _ = dc.GetTextExtent(ref)
            col1_w = max(col1_w, w1)
            col2_w = max(col2_w, w2)

        col_gap = 16
        mark_w = max(hdr_mark_w, 34) if in_hl_mode else 0
        box_w = PAD
        if in_hl_mode:
            box_w += mark_w + col_gap
        box_w += col1_w + col_gap + col2_w + PAD
        n_rows = 1 + len(entries)   # header + data rows
        box_h = PAD + n_rows * (row_h + ROW_GAP) + PAD

        cw, ch = self.GetClientSize()
        MARGIN = 8
        bx = cw - box_w - MARGIN
        by = MARGIN

        # Background
        dc.SetBrush(wx.Brush(BG))
        dc.SetPen(wx.Pen(wx.Colour(0, 80, 80), 1))
        dc.DrawRoundedRectangle(bx, by, box_w, box_h, 4)

        # Header row background
        dc.SetBrush(wx.Brush(HDR))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRoundedRectangle(bx, by, box_w, row_h + ROW_GAP + PAD, 4)
        dc.DrawRectangle(bx, by + (row_h + ROW_GAP + PAD) // 2,
                         box_w, (row_h + ROW_GAP + PAD + 1) // 2)

        dc.SetTextForeground(FG)
        dc.SetFont(font_hdr)
        y = by + PAD
        x = bx + PAD
        if in_hl_mode:
            dc.DrawText('MARK', x, y)
            x += mark_w + col_gap
        sig_x = x
        comp_x = sig_x + col1_w + col_gap
        dc.DrawText('Signal', sig_x, y)
        dc.DrawText('Component', comp_x, y)
        y += row_h + ROW_GAP

        dc.SetFont(font_body)
        self._net_label_rows = []
        for actual_name, net_name, ref in entries:
            rh = row_h + ROW_GAP
            self._net_label_rows.append((bx, y - 1, box_w, rh, actual_name))
            is_selected = in_hl_mode and actual_name == self._net_hl_name
            if is_selected:
                dc.SetBrush(wx.Brush(HL_ROW))
                dc.SetPen(wx.TRANSPARENT_PEN)
                dc.DrawRectangle(bx + 1, y - 1, box_w - 2, rh)
                dc.SetTextForeground(wx.Colour(0x22, 0x22, 0x22))
            else:
                dc.SetTextForeground(FG)
            if in_hl_mode:
                cx = bx + PAD + mark_w // 2
                cy = y + row_h // 2
                ring = wx.Colour(0x22, 0x22, 0x22) if is_selected else FG
                fill = wx.Colour(0x22, 0x22, 0x22) if is_selected else wx.Colour(0xff, 0xff, 0xff)
                dc.SetBrush(_transparent_brush())
                dc.SetPen(wx.Pen(ring, 2))
                dc.DrawCircle(cx, cy, 6)
                if is_selected:
                    dc.SetBrush(wx.Brush(fill))
                    dc.SetPen(wx.TRANSPARENT_PEN)
                    dc.DrawCircle(cx, cy, 3)
            dc.DrawText(net_name, sig_x, y)
            dc.DrawText(ref, comp_x, y)
            y += rh

    def _draw_column_labels(self, dc: wx.DC) -> None:
        lay = self.layout
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground('#808080')
        for section in range(lay.sections):
            label_y = lay.section_row_y('j', section) + PITCH + 2
            for col in range(1, lay.columns + 1, 5):
                x = lay.col_x(col)
                label = str(col)
                dc.DrawText(label, x - dc.GetTextExtent(label).Width // 2, label_y)


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------

def _point_to_segment_dist(px, py, x1, y1, x2, y2) -> float:
    """Perpendicular distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
