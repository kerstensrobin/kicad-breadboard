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

# Cycle-button dimensions (TO-92 cards only)
_BTN_W = 16
_BTN_H = 12
_BTN_RIGHT_PAD = 4   # gap between button right edge and card right edge


# ── Custom-paint implementation (GTK / Linux) ─────────────────────────────

class _PaintCard:
    """Pure data — no wx widget."""
    __slots__ = ('ref', 'comp', 'comp_def', 'y', 'height', 'pinout_idx')

    def __init__(self, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], y: int, height: int):
        self.ref        = ref
        self.comp       = comp
        self.comp_def   = comp_def
        self.y          = y       # top-left y in virtual (unscrolled) coordinates
        self.height     = height
        self.pinout_idx = 0       # index into TO92_PINOUT_VARIANTS[type_id]


class _PaintComponentTray(wx.ScrolledWindow):
    """Custom EVT_PAINT tray — reliable on GTK/Linux."""

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_PaintCard] = []
        self.on_pick = None

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
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            h = TO92_CARD_H if type_id in TO92_PINOUT_VARIANTS else CARD_H
            self._cards.append(_PaintCard(ref=ref, comp=comp, comp_def=comp_def,
                                          y=y, height=h))
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

            type_suffix = f' - {card.comp_def.type_id}' if card.comp_def else ''
            dc.SetFont(font_bold)
            dc.DrawText(f'{card.ref}{type_suffix}', x + SWATCH_W + 8, y + 4)

            dc.SetFont(font_normal)
            dc.DrawText(card.comp.value[:14], x + SWATCH_W + 8, y + 18)

            # Pinout row (TO-92 only)
            if card.height > CARD_H and card.comp_def:
                variants = TO92_PINOUT_VARIANTS.get(card.comp_def.type_id, [])
                if variants:
                    pinout_label = variants[card.pinout_idx][0]
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
                 is_to92: bool = False):
        h = TO92_CARD_H if is_to92 else CARD_H
        super().__init__(parent, size=(CARD_W, h), style=wx.BORDER_SIMPLE)
        self.ref          = ref
        self.comp         = comp
        self.comp_def     = comp_def
        self.board        = board
        self._is_to92     = is_to92
        self._swatch_color = comp_def.color if comp_def else '#aaaaaa'
        self.pinout_idx   = 0

        self._swatch = wx.Panel(self, pos=(4, 4), size=(SWATCH_W, h - 8))

        type_suffix = f' - {comp_def.type_id}' if comp_def else ''
        self._ref_lbl = wx.StaticText(
            self, label=f'{ref}{type_suffix}', pos=(SWATCH_W + 8, 3))
        self._ref_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))

        self._val_lbl = wx.StaticText(
            self, label=comp.value[:14], pos=(SWATCH_W + 8, 18))
        self._val_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self._pinout_lbl: Optional[wx.StaticText] = None
        self._cycle_lbl:  Optional[wx.StaticText] = None
        if is_to92 and comp_def:
            variants = TO92_PINOUT_VARIANTS.get(comp_def.type_id, [])
            if variants:
                self._pinout_lbl = wx.StaticText(
                    self, label=variants[0][0], pos=(SWATCH_W + 8, 32))
                self._pinout_lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                                                  wx.FONTSTYLE_NORMAL,
                                                  wx.FONTWEIGHT_NORMAL))
                if len(variants) > 1:
                    btn_x = CARD_W - _BTN_W - _BTN_RIGHT_PAD
                    self._cycle_lbl = wx.StaticText(
                        self, label='>', pos=(btn_x + 3, 31))
                    self._cycle_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                                     wx.FONTSTYLE_NORMAL,
                                                     wx.FONTWEIGHT_BOLD))

        for w in filter(None, [self, self._swatch, self._ref_lbl, self._val_lbl,
                                self._pinout_lbl, self._cycle_lbl]):
            w.Bind(wx.EVT_LEFT_DOWN, self._on_click)

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
                self._pinout_lbl.SetLabel(variants[self.pinout_idx][0])

    def _on_click(self, evt: wx.MouseEvent) -> None:
        placed = self.board.get_placement(self.ref) is not None
        if placed or self.comp_def is None:
            return

        if evt.GetEventObject() is self._cycle_lbl:
            self._cycle_pinout()
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
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            is_to92  = type_id in TO92_PINOUT_VARIANTS
            h        = TO92_CARD_H if is_to92 else CARD_H
            card = _NativeCard(self, ref=ref, comp=comp, comp_def=comp_def,
                               board=self.board, is_to92=is_to92)
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
