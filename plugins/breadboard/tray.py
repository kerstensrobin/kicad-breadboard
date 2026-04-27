"""
Component tray — shows unplaced components from the netlist.

Each component is shown as a small card with its reference, value, and a
colour swatch.  The student clicks a card to begin placing it on the canvas.
Once placed, the card is greyed out but stays visible.

Two rendering implementations are selected at import time based on platform:

  _PaintComponentTray  — used on GTK/Linux: all cards drawn in the
    ScrolledWindow's own EVT_PAINT handler via wx.AutoBufferedPaintDC.
    No native child panels, which avoids the GTK/X11 issue where native
    sub-windows overflow their parent's clip region.

  _NativeComponentTray — used on Windows: each card is a wx.Panel with
    wx.StaticText children.  ScrolledWindow custom painting is unreliable
    on Windows regardless of DC or background-style configuration;
    native widgets render correctly on all Windows versions.

ComponentTray is an alias that resolves to the correct class at runtime.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

import wx

from .model import (
    ComponentDef, ALL_DEFS, Netlist, NetlistComponent,
    guess_type_id, Breadboard, TO92_PINOUT_VARIANTS,
)

CARD_W      = 110
CARD_H      = 36
TO92_CARD_H = 48   # taller to accommodate the pinout row
CARD_PAD    = 4
SWATCH_W    = 12

# Maximum pixel width available for text inside a card.
# card spans CARD_PAD … CARD_PAD+CARD_W; text starts at SWATCH_W+8 from card left,
# with a 4 px right margin before the card border.
_TEXT_MAX_W = CARD_W - SWATCH_W - 8 - 4   # = 86 px

# Cycle-button dimensions (TO-92 cards only)
_BTN_W = 16
_BTN_H = 12
_BTN_RIGHT_PAD = 4   # gap between button right edge and card right edge


def _netlist_pinout_idx(type_id: str, pfmap: dict) -> int:
    """Return the variant index whose computed physical order matches its label."""
    if not pfmap or type_id not in TO92_PINOUT_VARIANTS:
        return 0
    variants = TO92_PINOUT_VARIANTS[type_id]
    for i, (label, pin_offsets) in enumerate(variants):
        physical = {}
        for pin, offset in pin_offsets.items():
            if pin in pfmap:
                physical[offset.col_delta] = pfmap[pin]
        if len(physical) != len(pin_offsets):
            continue
        computed = '-'.join(physical[j] for j in sorted(physical))
        if computed == label:
            return i
    return 0


def _pinout_label(type_id: str, pinout_idx: int, pfmap: dict) -> str:
    """Physical pin order label, derived from pfmap+variant when available."""
    variants = TO92_PINOUT_VARIANTS.get(type_id, [])
    if not variants or pinout_idx >= len(variants):
        return ''
    label, pin_offsets = variants[pinout_idx]
    if pfmap:
        physical = {}
        for pin, offset in pin_offsets.items():
            if pin in pfmap:
                physical[offset.col_delta] = pfmap[pin]
        if len(physical) == len(pin_offsets):
            return '-'.join(physical[j] for j in sorted(physical))
    return label


def _clip_text(dc: wx.DC, text: str, max_w: int) -> str:
    """Return text truncated with '…' so it fits within max_w pixels on dc."""
    if dc.GetTextExtent(text)[0] <= max_w:
        return text
    ellipsis = '\u2026'
    budget = max_w - dc.GetTextExtent(ellipsis)[0]
    while text and dc.GetTextExtent(text)[0] > budget:
        text = text[:-1]
    return text + ellipsis


# ── Custom-paint implementation (GTK / Linux) ─────────────────────────────

class _PaintCard:
    """Pure data — no wx widget."""
    __slots__ = ('ref', 'comp', 'comp_def', 'y', 'height', 'pinout_idx', 'rpi_long_labels', 'pfmap')

    def __init__(self, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], y: int, height: int):
        self.ref             = ref
        self.comp            = comp
        self.comp_def        = comp_def
        self.y               = y       # top-left y in virtual (unscrolled) coordinates
        self.height          = height
        self.pinout_idx      = 0       # index into TO92_PINOUT_VARIANTS[type_id]
        self.rpi_long_labels = False   # RPi extended alt-function pin names
        self.pfmap           = {}      # {pin_num: pinfunction} from netlist


class _PaintComponentTray(wx.ScrolledWindow):
    """Custom EVT_PAINT tray — reliable on GTK/Linux."""

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_PaintCard] = []
        self.on_pick = None
        self.on_rpi_label_mode = None   # callback(bool)

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT,     self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)

        if netlist:
            self._build_cards(netlist)

    def load_netlist(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self._build_cards(netlist)

    def refresh_placed(self) -> None:
        """Re-check which components are placed and redraw."""
        self.Refresh()

    # ------------------------------------------------------------------

    def _build_cards(self, netlist: Netlist) -> None:
        self._cards.clear()
        y = CARD_PAD
        for ref, comp in sorted(netlist.components.items()):
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description, comp.pin_count)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            h = TO92_CARD_H if type_id in TO92_PINOUT_VARIANTS else CARD_H
            card = _PaintCard(ref=ref, comp=comp, comp_def=comp_def, y=y, height=h)
            if type_id in TO92_PINOUT_VARIANTS:
                card.pfmap      = netlist.pinfunction_map(ref)
                card.pinout_idx = _netlist_pinout_idx(type_id, card.pfmap)
            self._cards.append(card)
            y += h + CARD_PAD

        total_h = y if self._cards else CARD_PAD
        self.SetVirtualSize(CARD_W + CARD_PAD * 2, total_h)
        self.Scroll(0, 0)
        self.Refresh()

    def _card_at(self, virt_y: int) -> Optional[_PaintCard]:
        """Return the card whose bounding box contains virtual y-coordinate virt_y."""
        for card in self._cards:
            if card.y <= virt_y < card.y + card.height:
                return card
        return None

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def _on_paint(self, _evt) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        dc.SetBackgroundMode(wx.TRANSPARENT)

        # Compute scroll offset in pixels (no PrepareDC — same approach as canvas)
        _, y_unit  = self.GetScrollPixelsPerUnit()
        _, y_start = self.GetViewStart()
        scroll_y   = y_start * y_unit
        client_h   = self.GetClientSize().height

        font_bold   = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        font_normal = wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

        font_pinout = wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        font_btn    = wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        for card in self._cards:
            placed = self.board.get_placement(card.ref) is not None
            x = CARD_PAD
            y = card.y - scroll_y   # virtual → screen coordinates
            if y + card.height < 0 or y > client_h:
                continue            # outside visible area
            bg = '#b8b8b8' if placed else '#f8f8f8'

            # Background
            dc.SetBrush(wx.Brush(bg))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(x, y, CARD_W, card.height)

            # Colour swatch
            color = card.comp_def.color if card.comp_def else '#aaaaaa'
            dc.SetBrush(wx.Brush(color if not placed else '#888888'))
            dc.SetPen(wx.Pen('#666666', 1))
            dc.DrawRectangle(x + 4, y + 4, SWATCH_W, card.height - 8)

            # Text
            fg = '#888888' if placed else '#222222'
            dc.SetTextForeground(fg)

            type_suffix = (f' - {card.comp_def.type_id}'
                           if card.comp_def and not card.comp_def.is_module else '')
            dc.SetFont(font_bold)
            dc.DrawText(_clip_text(dc, f'{card.ref}{type_suffix}', _TEXT_MAX_W),
                        x + SWATCH_W + 8, y + 4)

            dc.SetFont(font_normal)
            dc.DrawText(_clip_text(dc, card.comp.value, _TEXT_MAX_W),
                        x + SWATCH_W + 8, y + 18)

            # Pinout row (TO-92 only)
            if card.height > CARD_H and card.comp_def:
                variants = TO92_PINOUT_VARIANTS.get(card.comp_def.type_id, [])
                if variants:
                    pinout_label = _pinout_label(card.comp_def.type_id,
                                                 card.pinout_idx, card.pfmap)
                    dc.SetFont(font_pinout)
                    dc.SetTextForeground(fg)
                    dc.DrawText(pinout_label, x + SWATCH_W + 8, y + 32)

                    # Cycle button (only when not placed and multiple variants exist)
                    if len(variants) > 1 and not placed:
                        btn_x = x + CARD_W - _BTN_W - _BTN_RIGHT_PAD
                        btn_y = y + 30
                        dc.SetBrush(wx.Brush('#d8d8d8'))
                        dc.SetPen(wx.Pen('#888888', 1))
                        dc.DrawRoundedRectangle(btn_x, btn_y, _BTN_W, _BTN_H, 2)
                        dc.SetFont(font_btn)
                        dc.SetTextForeground('#333333')
                        tw, th = dc.GetTextExtent('>')
                        dc.DrawText('>', btn_x + (_BTN_W - tw) // 2,
                                    btn_y + (_BTN_H - th) // 2)



            # Border
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(wx.Pen('#aaaaaa' if placed else '#888888', 1))
            dc.DrawRectangle(x, y, CARD_W, card.height)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        click_x = evt.GetX()
        _, virt_y = self.CalcUnscrolledPosition(evt.GetX(), evt.GetY())
        card = self._card_at(virt_y)
        if card is None:
            return
        placed = self.board.get_placement(card.ref) is not None

        # Check if click landed on the cycle-pinout button (TO-92 only)
        if (not placed and card.comp_def and
                card.comp_def.type_id in TO92_PINOUT_VARIANTS):
            variants = TO92_PINOUT_VARIANTS[card.comp_def.type_id]
            if len(variants) > 1:
                btn_x = CARD_PAD + CARD_W - _BTN_W - _BTN_RIGHT_PAD
                if btn_x <= click_x < btn_x + _BTN_W:
                    card.pinout_idx = (card.pinout_idx + 1) % len(variants)
                    self.Refresh()
                    return



        if placed or card.comp_def is None:
            return
        if self.on_pick is not None:
            comp_def = card.comp_def
            if (card.comp_def.type_id in TO92_PINOUT_VARIANTS
                    and card.pinout_idx > 0):
                _, variant_offsets = TO92_PINOUT_VARIANTS[
                    card.comp_def.type_id][card.pinout_idx]
                comp_def = dataclasses.replace(card.comp_def,
                                               pin_offsets=variant_offsets)
            self.on_pick(comp_def, card.ref)


# ── Native widget implementation (Windows) ────────────────────────────────

class _NativeCard(wx.Panel):
    """wx.Panel card with native StaticText children — reliable on Windows."""

    def __init__(self, parent, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], board: Breadboard,
                 is_to92: bool = False, pfmap: dict = None):
        h = TO92_CARD_H if is_to92 else CARD_H
        super().__init__(parent, size=(CARD_W, h), style=wx.BORDER_SIMPLE)
        self.ref              = ref
        self.comp             = comp
        self.comp_def         = comp_def
        self.board            = board
        self._is_to92         = is_to92
        self._swatch_color    = comp_def.color if comp_def else '#aaaaaa'
        self.pfmap            = pfmap or {}
        type_id               = comp_def.type_id if comp_def else ''
        self.pinout_idx       = _netlist_pinout_idx(type_id, self.pfmap)

        self._swatch = wx.Panel(self, pos=(4, 4), size=(SWATCH_W, h - 8))

        type_suffix = (f' - {comp_def.type_id}'
                       if comp_def and not comp_def.is_module else '')
        self._ref_lbl = wx.StaticText(
            self, label=f'{ref}{type_suffix}', pos=(SWATCH_W + 8, 3),
            size=(_TEXT_MAX_W, -1), style=wx.ST_ELLIPSIZE_END)
        self._ref_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))

        self._val_lbl = wx.StaticText(
            self, label=comp.value, pos=(SWATCH_W + 8, 18),
            size=(_TEXT_MAX_W, -1), style=wx.ST_ELLIPSIZE_END)
        self._val_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self._pinout_lbl: Optional[wx.StaticText] = None
        self._cycle_lbl:  Optional[wx.StaticText] = None
        if is_to92 and comp_def:
            variants = TO92_PINOUT_VARIANTS.get(comp_def.type_id, [])
            if variants:
                self._pinout_lbl = wx.StaticText(
                    self, label=_pinout_label(comp_def.type_id, self.pinout_idx, self.pfmap),
                    pos=(SWATCH_W + 8, 32))
                self._pinout_lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                                                  wx.FONTSTYLE_NORMAL,
                                                  wx.FONTWEIGHT_NORMAL))
                if len(variants) > 1:
                    btn_x = CARD_W - _BTN_W - _BTN_RIGHT_PAD
                    self._cycle_lbl = wx.Button(
                        self, label='>', pos=(btn_x, 28),
                        size=(_BTN_W, _BTN_H + 2))
                    self._cycle_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                                     wx.FONTSTYLE_NORMAL,
                                                     wx.FONTWEIGHT_BOLD))
        for w in filter(None, [self, self._swatch, self._ref_lbl, self._val_lbl,
                                self._pinout_lbl]):
            w.Bind(wx.EVT_LEFT_DOWN, self._on_click)
        if self._cycle_lbl is not None:
            self._cycle_lbl.Bind(wx.EVT_BUTTON, self._on_cycle_btn)

        self._apply_colors()

    # ------------------------------------------------------------------

    def _apply_colors(self) -> None:
        placed = self.board.get_placement(self.ref) is not None
        bg = '#b8b8b8' if placed else '#f8f8f8'
        fg = '#888888' if placed else '#222222'
        sw = '#888888' if placed else self._swatch_color

        self.SetBackgroundColour(bg)
        self._swatch.SetBackgroundColour(sw)
        for lbl in filter(None, [self._ref_lbl, self._val_lbl,
                                  self._pinout_lbl, self._cycle_lbl]):
            lbl.SetBackgroundColour(bg)
            lbl.SetForegroundColour(fg)
        self.Refresh()

    def update(self, board: Breadboard) -> None:
        self.board = board
        self._apply_colors()

    def _cycle_pinout(self) -> None:
        if not self.comp_def:
            return
        variants = TO92_PINOUT_VARIANTS.get(self.comp_def.type_id, [])
        if len(variants) > 1:
            self.pinout_idx = (self.pinout_idx + 1) % len(variants)
            if self._pinout_lbl:
                self._pinout_lbl.SetLabel(
                    _pinout_label(self.comp_def.type_id, self.pinout_idx, self.pfmap))

    def _on_cycle_btn(self, _evt) -> None:
        placed = self.board.get_placement(self.ref) is not None
        if not placed:
            self._cycle_pinout()

    def _on_click(self, evt: wx.MouseEvent) -> None:
        placed = self.board.get_placement(self.ref) is not None
        if placed or self.comp_def is None:
            return

        comp_def = self.comp_def
        if (self.comp_def.type_id in TO92_PINOUT_VARIANTS and self.pinout_idx > 0):
            _, variant_offsets = TO92_PINOUT_VARIANTS[
                self.comp_def.type_id][self.pinout_idx]
            comp_def = dataclasses.replace(self.comp_def, pin_offsets=variant_offsets)

        tray = self.GetParent()
        if hasattr(tray, 'on_pick') and tray.on_pick is not None:
            tray.on_pick(comp_def, self.ref)


class _NativeComponentTray(wx.ScrolledWindow):
    """Native-widget tray — reliable on Windows."""

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_NativeCard] = []
        self.on_pick = None
        self.on_rpi_label_mode = None   # callback(bool)

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')

        if netlist:
            self._build_cards(netlist)

    def load_netlist(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self._build_cards(netlist)

    def refresh_placed(self) -> None:
        self.Freeze()
        for card in self._cards:
            card.update(self.board)
        self.Thaw()

    # ------------------------------------------------------------------

    def _build_cards(self, netlist: Netlist) -> None:
        self.Freeze()
        for card in self._cards:
            card.Destroy()
        self._cards.clear()

        y = CARD_PAD
        for ref, comp in sorted(netlist.components.items()):
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description, comp.pin_count)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            is_to92  = type_id in TO92_PINOUT_VARIANTS
            h        = TO92_CARD_H if is_to92 else CARD_H
            pfmap    = netlist.pinfunction_map(ref) if is_to92 else {}
            card = _NativeCard(self, ref=ref, comp=comp, comp_def=comp_def,
                               board=self.board, is_to92=is_to92, pfmap=pfmap)
            card.SetPosition(wx.Point(CARD_PAD, y))
            self._cards.append(card)
            y += h + CARD_PAD

        total_h = y if self._cards else CARD_PAD
        self.SetVirtualSize(CARD_W + CARD_PAD * 2, total_h)
        self.Scroll(0, 0)
        self.Thaw()


# ── Platform selection ────────────────────────────────────────────────────

ComponentTray = (_NativeComponentTray if wx.Platform == '__WXMSW__'
                 else _PaintComponentTray)
