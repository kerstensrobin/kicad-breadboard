"""
Main breadboard window (wx.Frame).

Layout:
  ┌──────────────────────────────────────────────────────────┐
  │  Toolbar: [Select] [Wire] [Delete] | [Validate] [Clear]  │
  ├──────────────────────────────┬───────────────────────────┤
  │                              │  Component tray           │
  │    BreadboardCanvas          │  (scrollable list of      │
  │                              │   netlist components)     │
  ├──────────────────────────────┴───────────────────────────┤
  │  Status bar                                              │
  └──────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import wx

_RESOURCES = Path(__file__).parent / 'resources'
import wx.lib.stattext

from .canvas import (BreadboardCanvas, CanvasLayout,
                     MODE_SELECT, MODE_WIRE, MODE_DELETE, MODE_NET_HIGHLIGHT,
                     MODE_DRAW_LINE, MODE_DRAW_RECT, MODE_DRAW_TEXT, MODE_DRAW_CIRCLE,
                     MODE_DRAW_TEXTBOX, WIRE_COLORS)
from .tray import ComponentTray
from .prefs import Preferences, save_prefs, load_prefs
from .model import (
    Breadboard, Netlist,
    parse_netlist, find_netlist, find_schematic,
    simulate, simulate_transient, SimResult, VsinSource, find_vsin_sources,
    initial_terminal_voltages,
    validate, IssueKind,
    ALL_DEFS, guess_type_id,
    save_session, load_session,
    PROBE_NAMES, PROBE_META,
)

PLUGIN_VERSION = '1.1.0'
REPO           = 'kerstensrobin/kicad-breadboard'

# Toolbar button IDs
ID_SELECT = wx.NewIdRef()
ID_WIRE   = wx.NewIdRef()
ID_DELETE = wx.NewIdRef()
ID_UPDATE      = wx.NewIdRef()
ID_EXPORT      = wx.NewIdRef()
ID_VALIDATE    = wx.NewIdRef()
ID_CLEAR_WARNINGS = wx.NewIdRef()
ID_CLEAR       = wx.NewIdRef()
ID_OPEN        = wx.NewIdRef()
ID_SAVE        = wx.NewIdRef()
ID_LOAD        = wx.NewIdRef()
ID_PREFS       = wx.NewIdRef()
ID_HELP_UPDATES = wx.NewIdRef()
ID_HELP_ISSUE   = wx.NewIdRef()
ID_PIN_FN       = wx.NewIdRef()
ID_ZOOM_IN      = wx.NewIdRef()
ID_ZOOM_OUT     = wx.NewIdRef()
ID_ZOOM_FIT     = wx.NewIdRef()
ID_EESCHEMA     = wx.NewIdRef()
ID_UNDO         = wx.NewIdRef()
ID_REDO         = wx.NewIdRef()
ID_SIMULATE     = wx.NewIdRef()

# Right vertical toolbar — drawing/editing tool palette (mirrors Eeschema right toolbar)
ID_NET_HIGHLIGHT = wx.NewIdRef()
ID_NOCONN        = wx.NewIdRef()
ID_ADD_LABEL     = wx.NewIdRef()
ID_ADD_GLABEL    = wx.NewIdRef()
ID_ADD_POWER     = wx.NewIdRef()
ID_ADD_JUNCTION  = wx.NewIdRef()
ID_MEASURE       = wx.NewIdRef()
ID_DRAW_LINE     = wx.NewIdRef()
ID_DRAW_RECT     = wx.NewIdRef()
ID_DRAW_TEXT     = wx.NewIdRef()
ID_DRAW_CIRCLE   = wx.NewIdRef()
ID_DRAW_TEXTBOX  = wx.NewIdRef()

# Wire color picker — labels mirror WIRE_COLORS order; first entry means "cycle automatically"
_WIRE_COLOR_NAMES = ['Yellow', 'Red', 'Blue', 'Green', 'Orange', 'Purple', 'Cyan', 'Grey', 'Black']
_WIRE_COLOR_LABELS = ['Auto'] + _WIRE_COLOR_NAMES

_ICONS_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / 'kicad_icons' / 'images'
_REPO_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / 'images'
_icon_cache: dict = {}


_DARK_PANEL_COLOUR = wx.Colour(0x47, 0x47, 0x47)


def _focus_is_text_entry() -> bool:
    """Return True when keyboard input should be handled by the focused editor."""
    ctrl = wx.Window.FindFocus()
    text_entry_types = tuple(
        t for t in (
            getattr(wx, 'TextCtrl', None),
            getattr(wx, 'SpinCtrl', None),
            getattr(wx, 'SpinCtrlDouble', None),
            getattr(wx, 'ComboBox', None),
            getattr(wx, 'ComboCtrl', None),
            getattr(wx, 'SearchCtrl', None),
        ) if t is not None
    )
    while ctrl is not None:
        if isinstance(ctrl, text_entry_types):
            return True
        ctrl = ctrl.GetParent()
    return False


def _is_dark_mode() -> bool:
    bg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)
    return (int(bg.Red()) + int(bg.Green()) + int(bg.Blue())) < 382


def _panel_bg() -> wx.Colour:
    return _DARK_PANEL_COLOUR if _is_dark_mode() else wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)


def _local_icon(name: str, size: int = 24, scale: float = 1.0) -> 'wx.Bitmap':
    """Load a repo/local PNG and scale to size×size."""
    for path in (_REPO_IMAGES_DIR / name, _ICONS_IMAGES_DIR / name):
        try:
            if not path.exists():
                continue
            img = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
            if img.GetWidth() != size or img.GetHeight() != size:
                img.Rescale(size, size, wx.IMAGE_QUALITY_HIGH)
            return wx.Bitmap(img)
        except Exception:
            continue
    return wx.NullBitmap


def _kicad_icon_archives() -> list[Path]:
    candidates: list[Path] = []

    def add(path) -> None:
        if path:
            p = Path(path)
            candidates.append(p if p.name == 'images.tar.gz' else p / 'images.tar.gz')

    add(os.getenv('KICAD_RESOURCES_DIR'))
    add(os.getenv('KICAD_RESOURCE_DIR'))

    for root in (
        '/usr/share/kicad/resources',
        '/usr/local/share/kicad/resources',
        '/opt/kicad/share/kicad/resources',
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/resources',
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/kicad/resources',
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/share/kicad/resources',
    ):
        add(root)

    for base in (os.getenv('ProgramFiles'), os.getenv('ProgramFiles(x86)'), os.getenv('LOCALAPPDATA')):
        if not base:
            continue
        for path in Path(base).glob('KiCad*/**/share/kicad/resources/images.tar.gz'):
            candidates.append(path)
        for path in (Path(base) / 'KiCad').glob('*/share/kicad/resources/images.tar.gz'):
            candidates.append(path)

    seen = set()
    result = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _fallback_icon(label: str, size: int) -> 'wx.Bitmap':
    bmp = wx.Bitmap(size, size, 32)
    dc = wx.MemoryDC(bmp)
    bg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)
    fg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT)
    dc.SetBackground(wx.Brush(bg))
    dc.Clear()
    dc.SetPen(wx.Pen(fg, 1))
    dc.SetBrush(wx.Brush(bg, wx.BRUSHSTYLE_TRANSPARENT))
    dc.DrawRectangle(1, 1, max(1, size - 2), max(1, size - 2))
    dc.SetFont(wx.Font(max(7, size // 2), wx.FONTFAMILY_DEFAULT,
                       wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
    dc.SetTextForeground(fg)
    text = (label[:1] or '?').upper()
    tw, th = dc.GetTextExtent(text)
    dc.DrawText(text, (size - tw) // 2, (size - th) // 2)
    dc.SelectObject(wx.NullBitmap)
    return bmp


def _kicad_icon(name: str, size: int = 20, scale: float = 1.0) -> 'wx.Bitmap':
    """Load a PNG from the KiCad icon archive, scaled to size×size.

    In dark mode, automatically tries the _dark_ variant first
    (e.g. save_24.png → save_dark_24.png), then falls back to the
    regular icon.  Uses platform-specific KiCad install locations.
    """
    import re as _re
    names = []
    if _is_dark_mode():
        dark_name = _re.sub(r'_(\d+\.png)$', r'_dark_\1', name)
        if dark_name != name:
            names.append(dark_name)
    names.append(name)

    def sized_names(icon_name: str) -> list[str]:
        m = _re.match(r'^(.*_)(\d+)(\.png)$', icon_name)
        if not m:
            return [icon_name]
        prefix, _, suffix = m.groups()
        sizes = (16, 24, 32, 48, 64)
        larger = [s for s in sizes if s >= size]
        smaller = [s for s in sizes if s < size]
        ordered = sorted(larger, key=lambda s: s - size) + sorted(
            smaller, key=lambda s: size - s)
        candidates = [f'{prefix}{s}{suffix}' for s in ordered]
        if icon_name not in candidates:
            candidates.append(icon_name)
        return candidates

    names = [candidate for icon_name in names for candidate in sized_names(icon_name)]

    cache_key = (name, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]
    import io, tarfile
    for archive in _kicad_icon_archives():
        if not archive.exists():
            continue
        try:
            with tarfile.open(archive) as tf:
                members = set(tf.getnames())
                for icon_name in names:
                    member_name = f'./{icon_name}'
                    if member_name not in members and icon_name not in members:
                        continue
                    member = tf.extractfile(member_name if member_name in members else icon_name)
                    if member is None:
                        continue
                    img = wx.Image(io.BytesIO(member.read()), wx.BITMAP_TYPE_PNG)
                    if img.GetWidth() != size or img.GetHeight() != size:
                        img.Rescale(size, size, wx.IMAGE_QUALITY_HIGH)
                    bmp = wx.Bitmap(img)
                    _icon_cache[cache_key] = bmp
                    return bmp
        except Exception:
            continue

    bmp = _fallback_icon(name, size)
    _icon_cache[cache_key] = bmp
    return bmp


class BreadboardWindow(wx.Frame):

    def __init__(self, parent=None, project_path: Optional[str] = None):
        super().__init__(
            parent,
            title='Breadboard Builder',
            size=(1300, 600),
            style=wx.DEFAULT_FRAME_STYLE,
        )

        self.prefs = load_prefs()
        self.board = Breadboard(layout=self.prefs.board_layout, rail_split=self.prefs.rail_split)
        self.netlist: Optional[Netlist] = None
        self._project_path: Optional[str] = project_path
        self._netlist_path: Optional[str] = None   # last successfully loaded .net file
        self._refreshing_choices: bool = False     # suppress EVT_CHOICE during SetItems
        self._validation_active: bool = False      # True once Validate has been run and not yet cleared
        self._sim_pane: Optional['SimPane'] = None
        self._waveform_frame: Optional['WaveformFrame'] = None

        self._build_ui()
        self._init_canvas_from_prefs()
        self._bind_events()

        self._set_icon()

        if project_path:
            self._auto_load_netlist(project_path)

        self.Centre()
        self.Show()
        self.Raise()

    def _set_icon(self) -> None:
        ico_path = _RESOURCES / 'icon.ico'
        png_path = _RESOURCES / 'icon.png'
        if ico_path.exists():
            self.SetIcon(wx.Icon(str(ico_path)))
        elif png_path.exists():
            bmp = wx.Bitmap(str(png_path), wx.BITMAP_TYPE_PNG)
            icon = wx.Icon()
            icon.CopyFromBitmap(bmp)
            self.SetIcon(icon)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_menu()
        self._build_toolbar()

        # Outer layout: fixed-width left panel + resizable canvas/right area.
        # Use a plain BoxSizer rather than a SplitterWindow so that GTK does
        # not re-composite the left panel at a wrong position during rapid
        # canvas repaints (a wx.SplitterWindow rendering artefact on GTK).
        main_panel = wx.Panel(self)
        _toolbar_sep = wx.StaticLine(main_panel)

        inner_splitter = wx.SplitterWindow(main_panel, style=wx.SP_LIVE_UPDATE)

        # Wrap canvas + right vtoolbar together so the vtoolbar hugs the equipment pane
        canvas_area = wx.Panel(inner_splitter)
        self.canvas = BreadboardCanvas(canvas_area, self.board, self.netlist)
        self._vtoolbar = self._build_vtoolbar(canvas_area)
        _vtb_sep = wx.StaticLine(canvas_area, style=wx.LI_VERTICAL)
        ca_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ca_sizer.Add(self.canvas, 1, wx.EXPAND)
        ca_sizer.Add(_vtb_sep, 0, wx.EXPAND)
        ca_sizer.Add(self._vtoolbar, 0, wx.EXPAND)
        canvas_area.SetSizer(ca_sizer)

        # --- Left panel: component tray only ---
        left_panel = wx.Panel(main_panel)
        left_panel.SetBackgroundColour(_panel_bg())
        left_panel.SetMinSize((130, -1))
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        comp_label = wx.StaticText(left_panel, label='Components')
        comp_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
        self._pin_fn_cb = wx.CheckBox(left_panel, label='Pin functions')
        self._pin_fn_cb.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                        wx.FONTWEIGHT_NORMAL))
        self._pin_fn_cb.SetToolTip(
            'Show pin function labels on DIP ICs and module headers')
        self._pin_fn_cb.Bind(wx.EVT_CHECKBOX, self._on_pin_fn)
        self.tray = ComponentTray(left_panel, self.board, self.netlist)
        left_sizer.Add(comp_label, 0, wx.ALL, 6)
        left_sizer.Add(self._pin_fn_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        left_sizer.Add(self.tray, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        left_panel.SetSizer(left_sizer)

        _left_sep = wx.StaticLine(main_panel, style=wx.LI_VERTICAL)

        main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        main_sizer.Add(left_panel, 0, wx.EXPAND)
        main_sizer.Add(_left_sep, 0, wx.EXPAND)
        main_sizer.Add(inner_splitter, 1, wx.EXPAND)

        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(_toolbar_sep, 0, wx.EXPAND)
        outer_sizer.Add(main_sizer, 1, wx.EXPAND)
        main_panel.SetSizer(outer_sizer)

        # --- Right panel: binding posts, instruments, hotkeys ---
        tray_panel = wx.Panel(inner_splitter)
        tray_panel.SetBackgroundColour(_panel_bg())
        tray_sizer = wx.BoxSizer(wx.VERTICAL)

        # --- Binding-post assignment section ---
        self._binding_panel = wx.Panel(tray_panel)
        binding_sizer = wx.BoxSizer(wx.VERTICAL)

        term_label = wx.StaticText(self._binding_panel, label='Binding posts')
        term_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
        binding_sizer.Add(term_label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 6)

        _TERM_COLORS = {'GND': '#3a3a3a', 'V1': '#bb2020', 'V2': '#1a7a30', 'V3': '#1a5a8a'}
        self._term_choices: dict = {}
        self._term_row_panels: dict = {}
        term_rows_sizer = wx.BoxSizer(wx.VERTICAL)
        for name in ('GND', 'V1', 'V2', 'V3'):
            row = wx.Panel(self._binding_panel)
            row_sz = wx.BoxSizer(wx.HORIZONTAL)
            lbl = wx.StaticText(row, label=name)
            lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
            lbl.SetForegroundColour(_TERM_COLORS[name])
            ch = wx.Choice(row, choices=['(unassigned)'])
            ch.SetSelection(0)
            self._term_choices[name] = ch
            row_sz.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 6)
            row_sz.Add(ch, 1, wx.EXPAND)
            row.SetSizer(row_sz)
            term_rows_sizer.Add(row, 0, wx.EXPAND | wx.BOTTOM, 4)
            self._term_row_panels[name] = row
        binding_sizer.Add(term_rows_sizer, 0, wx.EXPAND | wx.ALL, 6)
        self._term_rows_sizer = term_rows_sizer
        self._binding_panel.SetSizer(binding_sizer)
        tray_sizer.Add(self._binding_panel, 0, wx.EXPAND)

        # --- Instruments section (wrapped in a panel so it can be shown/hidden) ---
        self._instr_panel = wx.Panel(tray_panel)
        instr_sizer = wx.BoxSizer(wx.VERTICAL)

        instr_sizer.Add(wx.StaticLine(self._instr_panel), 0,
                        wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        instr_label = wx.StaticText(self._instr_panel, label='Instruments')
        instr_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                    wx.FONTWEIGHT_BOLD))
        instr_sizer.Add(instr_label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 6)

        self._probe_choices: dict = {}
        self._probe_place_btns: dict = {}
        self._probe_labels: dict = {}   # CH1–CH4 label widgets for warning icons
        # widget refs for dynamic show/hide
        self._ch2_widgets = []      # [lbl, btn, choice] for the CH2 row
        self._ch3_widgets = []      # widgets for CH3 row
        self._ch4_widgets = []      # widgets for CH4 row
        self._psu2_widgets = []     # widgets for PSU2+, PSU2- rows
        self._psu3_widgets = []     # widgets for PSU3+, PSU3- rows
        self._scope_grid = None
        self._psu_grid = None

        _INSTRUMENT_GROUPS = [
            ('Function generator', ('FG+', 'FG_GND')),
            ('Oscilloscope',       ('CH1', 'CH2', 'CH3', 'CH4', 'SCOPE_GND')),
            ('Power supply (PSU)', ('PSU1+', 'PSU1-', 'PSU2+', 'PSU2-', 'PSU3+', 'PSU3-')),
        ]
        for group_label, probe_list in _INSTRUMENT_GROUPS:
            sub = wx.StaticText(self._instr_panel, label=group_label)
            sub.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                                wx.FONTWEIGHT_NORMAL))
            sub.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            instr_sizer.Add(sub, 0, wx.LEFT | wx.TOP, 8)

            grid = wx.FlexGridSizer(rows=len(probe_list), cols=3, vgap=3, hgap=4)
            grid.AddGrowableCol(2)
            for name in probe_list:
                meta = PROBE_META[name]
                lbl = wx.StaticText(self._instr_panel, label=meta['label'])
                lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                    wx.FONTWEIGHT_BOLD))
                lbl.SetForegroundColour(meta['color'])
                btn = wx.Button(self._instr_panel, label='Place', size=(54, -1))
                ch = wx.Choice(self._instr_panel, choices=['(unassigned)'])
                ch.SetSelection(0)
                self._probe_place_btns[name] = btn
                self._probe_choices[name] = ch
                if name in ('CH1', 'CH2', 'CH3', 'CH4'):
                    self._probe_labels[name] = lbl
                grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(btn, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(ch,  1, wx.EXPAND | wx.ALIGN_CENTRE_VERTICAL)
                btn.Bind(wx.EVT_BUTTON, lambda e, n=name: self._on_probe_place_btn(n))
                ch.Bind(wx.EVT_CHOICE,  lambda e, n=name: self._on_probe_choice(n, e))

                # capture variable-visibility rows
                if name == 'CH2':
                    self._ch2_widgets = [lbl, btn, ch]
                elif name == 'CH3':
                    self._ch3_widgets = [lbl, btn, ch]
                elif name == 'CH4':
                    self._ch4_widgets = [lbl, btn, ch]
                elif name in ('PSU2+', 'PSU2-'):
                    self._psu2_widgets += [lbl, btn, ch]
                elif name in ('PSU3+', 'PSU3-'):
                    self._psu3_widgets += [lbl, btn, ch]

            instr_sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
            if group_label == 'Oscilloscope':
                self._scope_grid = grid
            elif group_label.startswith('Power'):
                self._psu_grid = grid

        # Apply initial visibility from loaded prefs
        for w in self._ch2_widgets:
            self._scope_grid.Show(w, self.prefs.scope_channels >= 2)
        for w in self._ch3_widgets:
            self._scope_grid.Show(w, self.prefs.scope_channels >= 3)
        for w in self._ch4_widgets:
            self._scope_grid.Show(w, self.prefs.scope_channels >= 4)

        self._instr_panel.SetSizer(instr_sizer)
        tray_sizer.Add(self._instr_panel, 0, wx.EXPAND)

        self._hotkey_line = wx.StaticLine(tray_panel)
        tray_sizer.Add(self._hotkey_line, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        hk_font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        hk_bold = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        info_font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL)

        # Left grid: Edit + File
        left_grid = wx.GridBagSizer(hgap=4, vgap=1)

        def hk_head(text, grid, row):
            lbl = wx.StaticText(tray_panel, label=text)
            lbl.SetFont(hk_bold)
            grid.Add(lbl, pos=(row, 0), span=(1, 2), flag=wx.TOP, border=4)
            return row + 1

        def hk_row(key, desc, grid, row):
            k = wx.StaticText(tray_panel, label=key)
            k.SetFont(hk_font)
            d = wx.StaticText(tray_panel, label=desc)
            d.SetFont(hk_font)
            d.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            grid.Add(k, pos=(row, 0), flag=wx.ALIGN_RIGHT)
            grid.Add(d, pos=(row, 1))
            return row + 1

        r = 0
        r = hk_head('Edit', left_grid, r)
        r = hk_row('W', 'Wire', left_grid, r)
        r = hk_row('D', 'Delete', left_grid, r)
        r = hk_row('R', 'Rotate', left_grid, r)
        r = hk_row('Esc', 'Select', left_grid, r)
        r = hk_row('Del', 'Delete sel.', left_grid, r)
        r = hk_row('R-click', 'Rotate', left_grid, r)
        r = hk_head('File', left_grid, r)
        r = hk_row('Ctrl+O', 'Open', left_grid, r)
        r = hk_row('Ctrl+S', 'Save', left_grid, r)
        r = hk_row('Ctrl+L', 'Load', left_grid, r)

        # Right sizer: View grid + info text directly below it
        right_grid = wx.GridBagSizer(hgap=4, vgap=1)
        r = 0
        r = hk_head('View', right_grid, r)
        r = hk_row('Scroll', 'Zoom', right_grid, r)
        r = hk_row('Sh+Scroll', 'Pan V', right_grid, r)
        r = hk_row('Ctrl+Scroll', 'Pan H', right_grid, r)
        r = hk_row('Mid drag', 'Pan', right_grid, r)
        r = hk_row('Ctrl+Home', 'Fit', right_grid, r)
        r = hk_row('+/\u2212', 'Zoom', right_grid, r)

        def _info_link(label, url):
            lbl = wx.lib.stattext.GenStaticText(tray_panel, label=label)
            lbl.SetFont(info_font)
            lbl.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            lbl.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            lbl.Bind(wx.EVT_LEFT_DOWN, lambda _e: wx.LaunchDefaultBrowser(url))
            return lbl

        info_top = wx.lib.stattext.GenStaticText(tray_panel,
                                  label=f'\nRelease: {PLUGIN_VERSION}\nMade with \u2665 by')
        info_top.SetFont(info_font)
        info_top.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        info_sizer = wx.BoxSizer(wx.VERTICAL)
        info_sizer.Add(info_top, 0)
        info_sizer.Add(_info_link('nacho.works and', 'https://nacho.works'), 0)
        info_sizer.Add(_info_link('uantwerpen.be', 'https://www.uantwerpen.be/en/'), 0)

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(right_grid, 0)
        right_sizer.Add(info_sizer, 0, wx.TOP, 6)

        left_col = wx.BoxSizer(wx.VERTICAL)
        left_col.Add(left_grid, 0)
        left_col.AddStretchSpacer(1)

        self._hotkey_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._hotkey_sizer.Add(left_col, 0, wx.RIGHT | wx.EXPAND, 12)
        self._hotkey_sizer.Add(right_sizer, 0)

        tray_sizer.AddStretchSpacer(1)
        tray_sizer.Add(self._hotkey_sizer, 0, wx.ALL, 6)

        self._tray_panel = tray_panel
        self._tray_sizer = tray_sizer
        _right_sep = wx.StaticLine(tray_panel, style=wx.LI_VERTICAL)
        tray_outer = wx.BoxSizer(wx.HORIZONTAL)
        tray_outer.Add(_right_sep, 0, wx.EXPAND)
        tray_outer.Add(tray_sizer, 1, wx.EXPAND)
        tray_panel.SetSizer(tray_outer)

        inner_splitter.SplitVertically(canvas_area, tray_panel, sashPosition=-260)
        inner_splitter.SetMinimumPaneSize(150)
        inner_splitter.SetSashGravity(1.0)

        # Connect tray → canvas placement flow
        self.tray.on_pick = lambda comp_def, ref: self.canvas.begin_place(comp_def, ref)
        self.tray.on_rpi_label_mode = lambda v: self.canvas.set_rpi_long_labels(v)
        self.tray.on_color_changed = lambda: self.canvas.Refresh()
        self.canvas.on_placed = self._on_canvas_placed
        self.canvas.on_probe_placed = self._on_probe_placed
        self.canvas.on_history_change = self._on_history_change
        self.canvas.on_restore = self._on_restore
        self.canvas.on_terminal_right_click = self._on_terminal_right_click
        self.canvas.on_board_changed = self._revalidate_live

        self.SetStatusBar(wx.StatusBar(self))
        self.GetStatusBar().SetFieldsCount(2)
        self.GetStatusBar().SetStatusWidths([-3, -1])
        self.SetStatusText('Load a netlist, then click a component in the tray to place it.', 0)
        self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)

    def _build_vtoolbar(self, parent: wx.Window) -> wx.ToolBar:
        """Right-side vertical tool palette, mirroring Eeschema's right toolbar."""
        vt = wx.ToolBar(parent, style=wx.TB_VERTICAL | wx.TB_NODIVIDER)
        _ico = self.FromDIP(24)
        vt.SetToolBitmapSize((_ico, _ico))

        # --- Pointer / view tools ---
        vt.AddTool(ID_SELECT, 'Select', _kicad_icon('cursor_24.png', _ico),
                   shortHelp='Select / Move  [Esc]', kind=wx.ITEM_CHECK)
        vt.AddTool(ID_NET_HIGHLIGHT, 'Highlight Net',
                   _kicad_icon('net_highlight_schematic_24.png', _ico),
                   shortHelp='Highlight net — click a hole to show its connections', kind=wx.ITEM_CHECK)
        vt.AddSeparator()

        # --- Wiring ---
        vt.AddTool(ID_WIRE, 'Wire', _kicad_icon('add_line_24.png', _ico),
                   shortHelp='Draw jumper wire  [W]', kind=wx.ITEM_CHECK)
        vt.AddSeparator()

        # --- Annotation drawing tools ---
        vt.AddTool(ID_DRAW_LINE, 'Draw Line',
                   _kicad_icon('add_graphical_segments_24.png', _ico),
                   shortHelp='Draw annotation line — click start, click end', kind=wx.ITEM_CHECK)
        vt.AddTool(ID_DRAW_RECT, 'Draw Rectangle',
                   _kicad_icon('add_rectangle_24.png', _ico),
                   shortHelp='Draw annotation rectangle — click corner, click opposite corner', kind=wx.ITEM_CHECK)
        vt.AddTool(ID_DRAW_CIRCLE, 'Draw Circle',
                   _kicad_icon('add_circle_24.png', _ico),
                   shortHelp='Draw annotation circle — click center, click radius', kind=wx.ITEM_CHECK)
        vt.AddTool(ID_DRAW_TEXT, 'Add Text',
                   _kicad_icon('text_24.png', _ico),
                   shortHelp='Place text annotation — click position, type text', kind=wx.ITEM_CHECK)
        vt.AddTool(ID_DRAW_TEXTBOX, 'Text Box',
                   _kicad_icon('add_textbox_24.png', _ico),
                   shortHelp='Draw a text box — drag to define the box, then type text', kind=wx.ITEM_CHECK)
        vt.AddSeparator()

        # --- Delete ---
        vt.AddTool(ID_DELETE, 'Delete', _kicad_icon('delete_cursor_24.png', _ico),
                   shortHelp='Delete component, wire, or annotation  [D]', kind=wx.ITEM_CHECK)

        vt.Realize()

        # Reflect initial mode (SELECT)
        vt.ToggleTool(ID_SELECT, True)
        return vt

    def _build_menu(self) -> None:
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(ID_OPEN,   'Open netlist…\tCtrl+O',
                         'Load a KiCad .net file')
        file_menu.Append(ID_UPDATE, 'Update from schematic',
                         'Re-export and reload netlist via kicad-cli')
        file_menu.AppendSeparator()
        file_menu.Append(ID_SAVE,   'Save session…\tCtrl+S',
                         'Save current placements and wires to a .kicad_bbrd file')
        file_menu.Append(ID_LOAD,   'Load session…\tCtrl+L',
                         'Restore placements and wires from a .kicad_bbrd file')
        file_menu.AppendSeparator()
        file_menu.Append(ID_PREFS, 'Preferences…', 'Configure instruments, display, and export options')
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, 'Quit\tAlt+F4')
        menu_bar.Append(file_menu, '&File')

        help_menu = wx.Menu()
        help_menu.Append(ID_HELP_UPDATES, 'Check for updates…',
                         'Compare installed version with the latest release on GitHub')
        help_menu.Append(ID_HELP_ISSUE, 'Report issue…',
                         'Open a pre-filled GitHub issue with your system information')
        menu_bar.Append(help_menu, '&Help')

        self.SetMenuBar(menu_bar)

    def _build_toolbar(self) -> None:
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_NODIVIDER)
        tb.SetBackgroundColour(_panel_bg())
        _ico = self.FromDIP(24)
        tb.SetToolBitmapSize((_ico, _ico))

        # File + prefs
        tb.AddTool(ID_OPEN, 'Open', _kicad_icon('directory_open_24.png', _ico),
                   shortHelp='Open a KiCad netlist (.net)  [Ctrl+O]')
        tb.AddTool(ID_SAVE, 'Save', _kicad_icon('save_24.png', _ico),
                   shortHelp='Save current session (.kicad_bbrd)  [Ctrl+S]')
        _export_icon = ('export_svg_24.png' if self.prefs.export_format == 'svg'
                        else 'export_png_24.png')
        tb.AddTool(ID_EXPORT, 'Export', _kicad_icon(_export_icon, _ico),
                   shortHelp='Save the breadboard as an image')
        tb.AddTool(ID_PREFS, 'Preferences', _local_icon('preficon.png', _ico),
                   shortHelp='Open preferences')
        tb.AddSeparator()

        # Edit history + clear
        tb.AddTool(ID_UNDO, 'Undo', _kicad_icon('undo_24.png', _ico),
                   shortHelp='Undo  [Ctrl+Z]')
        tb.AddTool(ID_REDO, 'Redo', _kicad_icon('redo_24.png', _ico),
                   shortHelp='Redo  [Ctrl+Y]')
        _clear_icon = wx.NullBitmap
        try:
            _img = wx.Image(str(_RESOURCES / 'icon.png'), wx.BITMAP_TYPE_PNG)
            _img.Rescale(_ico, _ico, wx.IMAGE_QUALITY_HIGH)
            _clear_icon = wx.Bitmap(_img)
        except Exception:
            pass
        tb.AddTool(ID_CLEAR, 'Clear Board', _clear_icon,
                   shortHelp='Remove all placed components and wires')
        tb.AddSeparator()

        # Zoom
        tb.AddTool(ID_ZOOM_IN,  'Zoom In',  _kicad_icon('zoom_in_24.png', _ico),
                   shortHelp='Zoom in  [+]')
        tb.AddTool(ID_ZOOM_OUT, 'Zoom Out', _kicad_icon('zoom_out_24.png', _ico),
                   shortHelp='Zoom out  [-]')
        tb.AddTool(ID_ZOOM_FIT, 'Fit View', _kicad_icon('zoom_fit_in_page_24.png', _ico),
                   shortHelp='Fit board in view  [Ctrl+Home]')
        tb.AddSeparator()

        # Schematic sync
        _update_icon_name = ('update_bbrd_from_sch_dark_64.png' if _is_dark_mode()
                             else 'update_bbrd_from_sch_64.png')
        tb.AddTool(ID_UPDATE, 'Update', _local_icon(_update_icon_name, _ico),
                   shortHelp='Re-export netlist from .kicad_sch and reload (requires kicad-cli)')
        tb.AddTool(ID_EESCHEMA, 'Schematic', _kicad_icon('icon_eeschema_24_24.png', _ico),
                   shortHelp='Open schematic in Eeschema')
        tb.AddSeparator()

        # Interaction modes
        tb.AddTool(ID_SELECT, 'Select', _kicad_icon('cursor_24.png', _ico),
                   shortHelp='Select and move placed components  [Esc]',
                   kind=wx.ITEM_RADIO)
        tb.AddTool(ID_WIRE, 'Wire', _kicad_icon('add_line_24.png', _ico),
                   shortHelp='Draw a jumper wire between two holes  [W]',
                   kind=wx.ITEM_RADIO)
        tb.AddControl(wx.StaticText(tb, label=' '))
        self._wire_color_choice = wx.Choice(tb, choices=_WIRE_COLOR_LABELS)
        self._wire_color_choice.SetSelection(0)
        self._wire_color_choice.SetToolTip(
            'Wire colour — Auto cycles through colours each wire; pick one to fix it.')
        self._wire_color_choice.Bind(wx.EVT_CHOICE, self._on_wire_color_choice)
        tb.AddControl(self._wire_color_choice)
        tb.AddTool(ID_DELETE, 'Delete', _kicad_icon('delete_cursor_24.png', _ico),
                   shortHelp='Delete a component or wire  [D]',
                   kind=wx.ITEM_CHECK)
        tb.AddSeparator()

        # Validate / simulate / clear labels
        tb.AddTool(ID_VALIDATE, 'Validate', _kicad_icon('erc_24.png', _ico),
                   shortHelp='Check if your circuit matches the schematic')
        tb.AddTool(ID_SIMULATE, 'Simulate', _kicad_icon('simulator_24.png', _ico),
                   shortHelp='Run SPICE DC simulation via ngspice')
        tb.AddTool(ID_CLEAR_WARNINGS, 'Clear Labels', _kicad_icon('ercwarn_24.png', _ico),
                   shortHelp='Dismiss validation warning/short markers')

        tb.Realize()

        tb.EnableTool(ID_UNDO, False)
        tb.EnableTool(ID_REDO, False)
        tb.EnableTool(ID_EESCHEMA, False)

        self.toolbar = tb

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_MENU, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_MENU, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_MENU, self._on_save,     id=ID_SAVE)
        self.Bind(wx.EVT_MENU, self._on_load,     id=ID_LOAD)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_TOOL, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_TOOL, self._on_save,     id=ID_SAVE)
        self.Bind(wx.EVT_TOOL, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_TOOL, self._on_export,   id=ID_EXPORT)
        self.Bind(wx.EVT_TOOL, self._on_select,        id=ID_SELECT)
        self.Bind(wx.EVT_TOOL, self._on_wire,          id=ID_WIRE)
        self.Bind(wx.EVT_TOOL, self._on_delete,        id=ID_DELETE)
        self.Bind(wx.EVT_TOOL, self._on_net_highlight, id=ID_NET_HIGHLIGHT)
        self.Bind(wx.EVT_TOOL, self._on_draw_line,     id=ID_DRAW_LINE)
        self.Bind(wx.EVT_TOOL, self._on_draw_rect,     id=ID_DRAW_RECT)
        self.Bind(wx.EVT_TOOL, self._on_draw_circle,   id=ID_DRAW_CIRCLE)
        self.Bind(wx.EVT_TOOL, self._on_draw_text,     id=ID_DRAW_TEXT)
        self.Bind(wx.EVT_TOOL, self._on_draw_textbox,  id=ID_DRAW_TEXTBOX)
        self.Bind(wx.EVT_TOOL, self._on_zoom_in,  id=ID_ZOOM_IN)
        self.Bind(wx.EVT_TOOL, self._on_zoom_out, id=ID_ZOOM_OUT)
        self.Bind(wx.EVT_TOOL, self._on_zoom_fit, id=ID_ZOOM_FIT)
        self.Bind(wx.EVT_TOOL, self._on_eeschema, id=ID_EESCHEMA)
        self.Bind(wx.EVT_TOOL, lambda _: self.canvas.undo(), id=ID_UNDO)
        self.Bind(wx.EVT_TOOL, lambda _: self.canvas.redo(), id=ID_REDO)
        self.Bind(wx.EVT_MENU, self._on_prefs,          id=ID_PREFS)
        self.Bind(wx.EVT_TOOL, self._on_prefs,          id=ID_PREFS)
        self.Bind(wx.EVT_MENU, self._on_check_updates,  id=ID_HELP_UPDATES)
        self.Bind(wx.EVT_MENU, self._on_report_issue,   id=ID_HELP_ISSUE)
        self.Bind(wx.EVT_TOOL, self._on_validate,       id=ID_VALIDATE)
        self.Bind(wx.EVT_TOOL, self._on_clear_warnings, id=ID_CLEAR_WARNINGS)
        self.Bind(wx.EVT_TOOL, self._on_simulate,       id=ID_SIMULATE)
        self.Bind(wx.EVT_TOOL, self._on_clear,          id=ID_CLEAR)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        for name, ch in self._term_choices.items():
            ch.Bind(wx.EVT_CHOICE, lambda evt, n=name: self._on_term_choice(n, evt))

    # ------------------------------------------------------------------
    # Toolbar handlers
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """Switch canvas mode and keep both toolbars in sync."""
        self.canvas.set_mode(mode)
        # Top toolbar: Select/Wire are ITEM_RADIO; Delete is ITEM_CHECK
        self.toolbar.ToggleTool(ID_SELECT, mode == MODE_SELECT)
        self.toolbar.ToggleTool(ID_WIRE,   mode == MODE_WIRE)
        self.toolbar.ToggleTool(ID_DELETE, mode == MODE_DELETE)
        # Right vtoolbar: all ITEM_CHECK, managed manually
        self._vtoolbar.ToggleTool(ID_SELECT,        mode == MODE_SELECT)
        self._vtoolbar.ToggleTool(ID_NET_HIGHLIGHT,  mode == MODE_NET_HIGHLIGHT)
        self._vtoolbar.ToggleTool(ID_WIRE,           mode == MODE_WIRE)
        self._vtoolbar.ToggleTool(ID_DRAW_LINE,      mode == MODE_DRAW_LINE)
        self._vtoolbar.ToggleTool(ID_DRAW_RECT,      mode == MODE_DRAW_RECT)
        self._vtoolbar.ToggleTool(ID_DRAW_CIRCLE,    mode == MODE_DRAW_CIRCLE)
        self._vtoolbar.ToggleTool(ID_DRAW_TEXT,      mode == MODE_DRAW_TEXT)
        self._vtoolbar.ToggleTool(ID_DRAW_TEXTBOX,   mode == MODE_DRAW_TEXTBOX)
        self._vtoolbar.ToggleTool(ID_DELETE,         mode == MODE_DELETE)
        if mode == MODE_SELECT:
            self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)
        elif mode == MODE_NET_HIGHLIGHT:
            self.SetStatusText('Mode: Highlight Net — click MARK or any hole to highlight its net  [Esc] exit', 1)
        elif mode == MODE_WIRE:
            self.SetStatusText('Mode: Draw Wire — click start, click end  [Esc] cancel', 1)
        elif mode == MODE_DRAW_LINE:
            self.SetStatusText('Mode: Draw Line — click start point, click end point  [Esc] cancel', 1)
        elif mode == MODE_DRAW_RECT:
            self.SetStatusText('Mode: Draw Rectangle — click one corner, click opposite corner  [Esc] cancel', 1)
        elif mode == MODE_DRAW_CIRCLE:
            self.SetStatusText('Mode: Draw Circle — click center, click to set radius  [Esc] cancel', 1)
        elif mode == MODE_DRAW_TEXT:
            self.SetStatusText('Mode: Add Text — click where to place the annotation  [Esc] exit', 1)
        elif mode == MODE_DRAW_TEXTBOX:
            self.SetStatusText('Mode: Text Box — drag to define box, release to enter text  [Esc] cancel', 1)
        elif mode == MODE_DELETE:
            self.SetStatusText('Mode: Delete — click component, wire, or annotation  [Esc] cancel', 1)

    def _on_select(self, _evt) -> None:
        self._set_mode(MODE_SELECT)

    def _on_net_highlight(self, _evt) -> None:
        if self.canvas.mode == MODE_NET_HIGHLIGHT:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_NET_HIGHLIGHT)

    def _on_draw_line(self, _evt) -> None:
        if self.canvas.mode == MODE_DRAW_LINE:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DRAW_LINE)

    def _on_draw_rect(self, _evt) -> None:
        if self.canvas.mode == MODE_DRAW_RECT:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DRAW_RECT)

    def _on_draw_circle(self, _evt) -> None:
        if self.canvas.mode == MODE_DRAW_CIRCLE:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DRAW_CIRCLE)

    def _on_draw_text(self, _evt) -> None:
        if self.canvas.mode == MODE_DRAW_TEXT:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DRAW_TEXT)

    def _on_draw_textbox(self, _evt) -> None:
        if self.canvas.mode == MODE_DRAW_TEXTBOX:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DRAW_TEXTBOX)

    def _on_wire(self, _evt) -> None:
        self._set_mode(MODE_WIRE)

    def _on_wire_color_choice(self, _evt) -> None:
        idx = self._wire_color_choice.GetSelection()
        # idx 0 = Auto (cycle); idx 1..N = specific color from WIRE_COLORS
        self.canvas.set_wire_color(WIRE_COLORS[idx - 1] if idx > 0 else None)

    def _on_delete(self, _evt) -> None:
        if self.canvas.mode == MODE_DELETE:
            self._set_mode(MODE_SELECT)
        else:
            self._set_mode(MODE_DELETE)

    def _on_zoom_in(self, _evt) -> None:
        self.canvas.zoom_center(1.2)

    def _on_zoom_out(self, _evt) -> None:
        self.canvas.zoom_center(1 / 1.2)

    def _on_zoom_fit(self, _evt) -> None:
        self.canvas._fit_view()

    def _on_eeschema(self, _evt) -> None:
        import subprocess, shutil, sys
        sch = find_schematic(self._project_path) if self._project_path else None
        if not sch:
            wx.MessageBox(
                'No schematic (.kicad_sch) found.\nOpen a netlist first to set the project folder.',
                'Open Schematic', wx.OK | wx.ICON_INFORMATION, self,
            )
            return

        # Pass the .kicad_pro project file so eeschema opens within project
        # context and KiCad's IPC single-instance socket can raise an already-
        # running window (works on both X11 and Wayland).  Fall back to the
        # .kicad_sch if no project file exists alongside it.
        pro = sch.with_suffix('.kicad_pro')
        target = pro if pro.exists() else sch
        exe = shutil.which('eeschema') or 'eeschema'

        if sys.platform.startswith('linux'):
            # Try wmctrl regardless of display server — it works on X11 and
            # XWayland, and fails gracefully (rc != 0) for native Wayland windows.
            if shutil.which('wmctrl'):
                rc = subprocess.call(
                    ['wmctrl', '-x', '-a', 'eeschema'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if rc == 0:
                    return

        # On X11 without wmctrl, and on Wayland, KiCad's own IPC single-instance
        # socket handles focus: if eeschema is already running it raises that window;
        # otherwise a new instance opens.  Passing the .kicad_pro file is required
        # for the IPC check to match the correct project instance.
        try:
            subprocess.Popen([exe, str(target)])
        except FileNotFoundError:
            wx.MessageBox(
                f'eeschema not found on PATH.\nSchematic: {sch}',
                'Open Schematic', wx.OK | wx.ICON_ERROR, self,
            )

    def _on_history_change(self, can_undo: bool, can_redo: bool) -> None:
        self.toolbar.EnableTool(ID_UNDO, can_undo)
        self.toolbar.EnableTool(ID_REDO, can_redo)

    def _on_restore(self) -> None:
        """Full UI refresh after undo/redo."""
        self.tray.refresh_placed()
        self._refresh_probe_buttons()
        self._refresh_terminal_choices()
        self._refresh_probe_choices()
        self._revalidate_live()

    def _on_canvas_placed(self, ref: str) -> None:
        self.tray.refresh_placed()
        self._revalidate_live()

    def _revalidate_live(self) -> None:
        """Keep displayed validation markers in sync with the board after an edit.

        Only acts once the user has explicitly run Validate — otherwise a half-built
        circuit would light up with OPEN_NET markers before the user asked to check.
        """
        if not self._validation_active or self.netlist is None:
            return
        result = validate(self.board, self.netlist)
        if result.ok:
            self.canvas.clear_highlights()
        else:
            self.canvas.set_validation_result(result)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        if _focus_is_text_entry():
            evt.Skip()
            return

        key = evt.GetKeyCode()
        if key in (ord('W'), ord('w')):
            self._set_mode(MODE_WIRE)
        elif key in (ord('D'), ord('d')):
            self._set_mode(MODE_DELETE)
        elif key == wx.WXK_ESCAPE:
            self._set_mode(MODE_SELECT)
        elif key in (wx.WXK_DELETE, wx.WXK_BACK):
            self.canvas.delete_selection()
        elif key == wx.WXK_HOME and evt.ControlDown():
            self.canvas._fit_view()
        elif key in (ord('+'), ord('='), wx.WXK_NUMPAD_ADD):
            self.canvas.zoom_center(1.2)
        elif key in (ord('-'), wx.WXK_NUMPAD_SUBTRACT):
            self.canvas.zoom_center(1 / 1.2)
        elif key in (ord('Z'), ord('z')) and evt.ControlDown():
            if evt.ShiftDown():
                self.canvas.redo()
            else:
                self.canvas.undo()
        elif key in (ord('Y'), ord('y')) and evt.ControlDown():
            self.canvas.redo()
        else:
            evt.Skip()

    def _on_open(self, _evt) -> None:
        with wx.FileDialog(
            self,
            message='Open KiCad netlist, schematic, or session',
            wildcard='All supported files (*.net;*.kicad_sch;*.kicad_bbrd)|*.net;*.kicad_sch;*.kicad_bbrd'
                     '|Breadboard session (*.kicad_bbrd)|*.kicad_bbrd'
                     '|KiCad netlist (*.net)|*.net'
                     '|KiCad schematic (*.kicad_sch)|*.kicad_sch'
                     '|All files (*)|*',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        if path.endswith('.kicad_bbrd'):
            self._on_load(path=path)
            return

        # Loading a different netlist while the board has content → clear first
        if path != self._netlist_path and (self.board.placements or self.board.wires):
            if wx.MessageBox(
                'Loading a different netlist will clear the current board.\nContinue?',
                'Clear board?',
                wx.YES_NO | wx.ICON_QUESTION, self,
            ) != wx.YES:
                return
            self.board = Breadboard(layout=self.prefs.board_layout, rail_split=self.prefs.rail_split)
            self.canvas.reload_board(self.board)
            self.tray.board = self.board
            self.tray.refresh_placed()
            self._validation_active = False
            self.canvas.clear_highlights()

        self._project_path = str(Path(path).parent)
        self._netlist_path = path
        self._load_netlist(path)

    @staticmethod
    def _find_kicad_cli() -> str:
        import shutil
        import sys
        cli = shutil.which('kicad-cli')
        if cli:
            return cli
        if sys.platform == 'darwin':
            bundle = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
            if os.path.isfile(bundle):
                return bundle
        return 'kicad-cli'

    def _export_netlist(self, silent: bool = False) -> Optional[Path]:
        """
        Run kicad-cli to export the netlist from the project schematic.
        Returns the .net path on success, or None on failure.
        If silent=True, errors are written to the status bar instead of a dialog.
        """
        import subprocess

        sch = find_schematic(self._project_path)
        if not sch:
            msg = f'No .kicad_sch file found in:\n{self._project_path}'
            if silent:
                self.SetStatusText(msg, 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        net_path = sch.with_suffix('.net')
        self.SetStatusText('Exporting netlist from schematic…', 0)
        self.Update()

        try:
            result = subprocess.run(
                [self._find_kicad_cli(), 'sch', 'export', 'netlist',
                 '--format', 'kicadsexpr', '-o', str(net_path), str(sch)],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            msg = ('kicad-cli not found on PATH.\n'
                   'Make sure KiCad is installed and kicad-cli is accessible.')
            if silent:
                self.SetStatusText(msg.replace('\n', ' '), 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None
        except subprocess.TimeoutExpired:
            msg = 'kicad-cli timed out.'
            if silent:
                self.SetStatusText(msg, 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        if result.returncode != 0:
            msg = f'kicad-cli failed (exit {result.returncode}):\n{result.stderr}'
            if silent:
                self.SetStatusText(msg.replace('\n', ' '), 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        return net_path

    def _on_update(self, _evt) -> None:
        """Re-export the netlist from the .kicad_sch via kicad-cli and reload."""
        if not self._project_path:
            wx.MessageBox(
                'No project loaded yet.\n'
                'Use "Open netlist" first, or launch the plugin from KiCad.',
                'Update from schematic', wx.OK | wx.ICON_INFORMATION, self)
            return

        net_path = self._export_netlist(silent=False)
        if net_path is None:
            return

        # Reload the freshly-written netlist, keeping existing placements
        self._load_netlist(str(net_path))

        # Remove placements for refs that no longer exist, or whose type changed
        if self.netlist:
            removed = []
            type_changed = []
            for ref in list(self.board.placements):
                comp = self.netlist.components.get(ref)
                if comp is None:
                    self.board.remove(ref)
                    removed.append(ref)
                else:
                    new_type = guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description, comp.pin_count)
                    old_type = self.board.get_placement(ref).type_id
                    if new_type != old_type:
                        self.board.remove(ref)
                        type_changed.append(ref)
            msgs = []
            if removed:
                msgs.append(f'removed orphaned: {", ".join(removed)}')
            if type_changed:
                msgs.append(f'type changed — re-place: {", ".join(type_changed)}')
            if msgs:
                self.tray.refresh_placed()
                self._revalidate_live()
                self.canvas.Refresh()
                self.SetStatusText(f'Netlist updated. {"; ".join(msgs).capitalize()}.', 0)

    def _on_export(self, _evt) -> None:
        use_svg = self.prefs.export_format == 'svg'
        ext = 'svg' if use_svg else 'png'
        default_dir = self._project_path or ''
        default_file = f'breadboard.{ext}'
        wildcard = ('SVG image (*.svg)|*.svg' if use_svg
                    else 'PNG image (*.png)|*.png')
        with wx.FileDialog(
            self,
            message='Save breadboard image',
            defaultDir=default_dir,
            defaultFile=default_file,
            wildcard=wildcard,
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        try:
            if use_svg:
                self.canvas.render_to_svg(path)
            else:
                bmp = self.canvas.render_to_bitmap()
                if not bmp.SaveFile(path, wx.BITMAP_TYPE_PNG):
                    raise RuntimeError('SaveFile returned False')
        except Exception as exc:
            wx.MessageBox(f'Failed to save image to:\n{path}\n\n{exc}',
                          'Export image', wx.OK | wx.ICON_ERROR, self)
            return
        self.SetStatusText(f'Image saved to {path}', 0)

    def _on_save(self, _evt) -> None:
        default_dir = self._project_path or ''
        default_file = 'breadboard.kicad_bbrd'
        with wx.FileDialog(
            self,
            message='Save session',
            defaultDir=default_dir,
            defaultFile=default_file,
            wildcard='Breadboard session (*.kicad_bbrd)|*.kicad_bbrd|All files (*)|*',
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            ann_json = [self.canvas._ann_to_json(a) for a in self.canvas._annotations]
            save_session(self.board, self._netlist_path, path, annotations=ann_json)
            self.SetStatusText(f'Session saved to {path}', 0)
        except Exception as exc:
            wx.MessageBox(f'Failed to save session:\n{exc}', 'Save session',
                          wx.OK | wx.ICON_ERROR, self)

    def _on_load(self, _evt=None, *, path: str = '') -> None:
        if not path:
            default_dir = self._project_path or ''
            with wx.FileDialog(
                self,
                message='Load session',
                defaultDir=default_dir,
                wildcard='Breadboard session (*.kicad_bbrd)|*.kicad_bbrd|All files (*)|*',
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
            ) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                path = dlg.GetPath()
        try:
            result = load_session(path)
        except Exception as exc:
            wx.MessageBox(f'Failed to load session:\n{exc}', 'Load session',
                          wx.OK | wx.ICON_ERROR, self)
            return

        # Restore board state
        self.board = result['board']
        self.canvas.reload_board(self.board)
        self.tray.board = self.board
        self._validation_active = False
        self.canvas.clear_highlights()
        # Restore annotations
        ann_raw = result.get('annotations', [])
        self.canvas._annotations = [a for d in ann_raw
                                     if (a := self.canvas._ann_from_json(d)) is not None]

        # Sync layout prefs from saved session
        saved_layout = result.get('board_layout', 'full')
        if saved_layout != self.prefs.board_layout:
            self.prefs.board_layout = saved_layout
        self.canvas.layout = CanvasLayout(saved_layout, self.prefs.binding_post_side,
                                          self.prefs.show_branding, self.prefs.rail_split,
                                          self.prefs.num_terminals)
        self.canvas._populate_module_pins()
        self.canvas._pan_initialized = False

        # Reload the netlist from the saved path (if present and netlist not yet loaded)
        saved_netlist = result.get('netlist_path')
        if saved_netlist and self.netlist is None:
            try:
                self._load_netlist(saved_netlist)
            except Exception:
                pass  # carry on without netlist; user can open manually
        elif self.netlist:
            self.tray.refresh_placed()

        # Always resync terminal and probe dropdowns from the restored board state
        self._refresh_terminal_choices()
        self._refresh_probe_choices()
        self._refresh_probe_buttons()

        self.canvas.clear_history()
        self.canvas.Refresh()
        self.SetStatusText(f'Session loaded from {path}', 0)

    def _on_prefs(self, _evt) -> None:
        dlg = PreferencesDialog(self, self.prefs)
        if dlg.ShowModal() == wx.ID_OK:
            old = self.prefs
            self.prefs = dlg.get_prefs()
            self._apply_prefs(old)
        dlg.Destroy()

    def _apply_prefs(self, old: Preferences) -> None:
        p = self.prefs

        # Signal labels
        if p.show_net_labels != old.show_net_labels:
            self.canvas.show_net_labels = p.show_net_labels

        # Binding posts on canvas and sidebar
        if p.show_binding_posts != old.show_binding_posts:
            self.canvas.show_binding_posts = p.show_binding_posts
            self._tray_sizer.Show(self._binding_panel, p.show_binding_posts)
            self._tray_panel.Layout()

        # Number of terminals
        if p.num_terminals != old.num_terminals:
            self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                              p.show_branding, p.rail_split,
                                              p.num_terminals)
            self._refresh_terminal_rows(p.num_terminals)
            self._tray_panel.Layout()

        # Instruments panel visibility
        if p.instruments_enabled != old.instruments_enabled:
            self._tray_sizer.Show(self._instr_panel, p.instruments_enabled)
            self._tray_panel.Layout()

        # Hotkeys panel visibility
        if p.show_hotkeys != old.show_hotkeys:
            self._tray_sizer.Show(self._hotkey_line,  p.show_hotkeys)
            self._tray_sizer.Show(self._hotkey_sizer, p.show_hotkeys)
            self._tray_panel.Layout()

        # Oscilloscope channel count
        if p.scope_channels != old.scope_channels:
            for w in self._ch2_widgets:
                self._scope_grid.Show(w, p.scope_channels >= 2)
            for w in self._ch3_widgets:
                self._scope_grid.Show(w, p.scope_channels >= 3)
            for w in self._ch4_widgets:
                self._scope_grid.Show(w, p.scope_channels >= 4)
            self._instr_panel.Layout()
            if self._sim_pane:
                self._sim_pane.set_scope_channels(p.scope_channels, self.board)
            if self._waveform_frame and self._waveform_frame.IsShown():
                self._reopen_waveform_frame(p.scope_channels)

        # PSU channel count
        if p.psu_channels != old.psu_channels:
            show_psu2 = p.psu_channels >= 2
            show_psu3 = p.psu_channels >= 3
            for w in self._psu2_widgets:
                self._psu_grid.Show(w, show_psu2)
            for w in self._psu3_widgets:
                self._psu_grid.Show(w, show_psu3)
            self._instr_panel.Layout()

        # Board layout
        if p.board_layout != old.board_layout:
            # Changing layout clears the board — confirm if there's existing work
            has_work = bool(self.board.placements or self.board.wires)
            proceed = True
            if has_work:
                ans = wx.MessageBox(
                    'Changing the board layout will clear all current placements and wires.\n'
                    'Continue?',
                    'Clear board?', wx.YES_NO | wx.ICON_WARNING, self,
                )
                proceed = (ans == wx.YES)
            if proceed:
                from .model import Breadboard
                self.board = Breadboard(layout=p.board_layout, rail_split=p.rail_split)
                self.canvas.reload_board(self.board)
                self.tray.board = self.board
                self.tray.refresh_placed()
                self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                                  p.show_branding, p.rail_split,
                                                  p.num_terminals)
                self.canvas._pan_initialized = False

        # Rail split toggle — rebuilds static topology and layout, placements unchanged
        if p.rail_split != old.rail_split:
            self.board.set_rail_split(p.rail_split)
            self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                              p.show_branding, p.rail_split,
                                              p.num_terminals)

        # Binding post side or branding (canvas layout only, no data change)
        if p.binding_post_side != old.binding_post_side or p.show_branding != old.show_branding:
            self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                              p.show_branding, p.rail_split,
                                              p.num_terminals)
            self.canvas._pan_initialized = False

        # Baseboard
        if p.show_baseboard != old.show_baseboard:
            self.canvas.show_baseboard = p.show_baseboard
        if p.baseboard_color != old.baseboard_color:
            self.canvas.baseboard_color = p.baseboard_color
        if p.show_branding != old.show_branding:
            self.canvas.show_branding = p.show_branding
        if p.branding_image != old.branding_image:
            self.canvas.branding_image = p.branding_image
        if p.rail_style != old.rail_style:
            self.canvas.rail_style = p.rail_style

        if p.export_format != old.export_format:
            icon_name = ('export_svg_24.png' if p.export_format == 'svg'
                         else 'export_png_24.png')
            self.toolbar.SetToolNormalBitmap(ID_EXPORT, _kicad_icon(icon_name, self.FromDIP(24)))
            self.toolbar.Refresh()

        self.canvas.Refresh()

    def _init_canvas_from_prefs(self) -> None:
        """Sync all canvas properties from self.prefs (called once at startup)."""
        p = self.prefs
        self.canvas.show_net_labels    = p.show_net_labels
        self.canvas.show_binding_posts = p.show_binding_posts
        self.canvas.show_baseboard     = p.show_baseboard
        self.canvas.baseboard_color    = p.baseboard_color
        self.canvas.show_branding      = p.show_branding
        self.canvas.branding_image     = p.branding_image
        self.canvas.rail_style         = p.rail_style
        self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                          p.show_branding, p.rail_split,
                                          p.num_terminals)
        self.canvas._pan_initialized = False
        self._tray_sizer.Show(self._binding_panel, p.show_binding_posts)
        self._tray_sizer.Show(self._hotkey_line,   p.show_hotkeys)
        self._tray_sizer.Show(self._hotkey_sizer,  p.show_hotkeys)
        self._refresh_terminal_rows(p.num_terminals)
        self._tray_panel.Layout()

    def _refresh_terminal_rows(self, num_terminals: int) -> None:
        """Show/hide terminal rows in the binding panel based on num_terminals."""
        for i, name in enumerate(('GND', 'V1', 'V2', 'V3')):
            row = self._term_row_panels.get(name)
            if row:
                row.Show(i < num_terminals)
        self._binding_panel.Layout()

    # ------------------------------------------------------------------
    # Help menu handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_sysinfo() -> str:
        import platform, sys, os
        lines = [
            f'**Plugin version:** {PLUGIN_VERSION}',
            f'**OS:** {platform.system()} {platform.release()} ({platform.machine()})',
            f'**Python:** {sys.version.split()[0]}',
        ]
        try:
            import wx as _wx
            lines.append(f'**wxPython:** {_wx.version()}')
        except Exception:
            pass
        try:
            import pcbnew
            lines.append(f'**KiCad:** {pcbnew.GetMajorMinorVersion()}')
        except Exception:
            lines.append('**KiCad:** standalone / not available')
        # CPU
        try:
            cpu = platform.processor()
            if not cpu and platform.system() == 'Linux':
                with open('/proc/cpuinfo') as f:
                    for line in f:
                        if line.startswith('model name'):
                            cpu = line.split(':', 1)[1].strip()
                            break
            cores = os.cpu_count()
            lines.append(f'**CPU:** {cpu or "unknown"} ({cores} cores)')
        except Exception:
            pass
        # GPU
        try:
            import subprocess
            result = subprocess.run(
                ['lspci'], capture_output=True, text=True, timeout=5)
            gpus = [l.split(':', 2)[-1].strip()
                    for l in result.stdout.splitlines()
                    if any(k in l for k in ('VGA', '3D', 'Display'))]
            if gpus:
                lines.append(f'**GPU:** {"; ".join(gpus)}')
        except Exception:
            pass
        return '\n'.join(lines)

    def _on_check_updates(self, _evt) -> None:
        import urllib.request, json, webbrowser
        releases_url = f'https://github.com/{REPO}/releases'
        api_url      = f'https://api.github.com/repos/{REPO}/releases/latest'
        wx.BeginBusyCursor()
        try:
            req = urllib.request.Request(api_url,
                                         headers={'User-Agent': 'kicad-breadboard'})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
            latest = data.get('tag_name') if data else None
        except Exception:
            latest = None
        finally:
            wx.EndBusyCursor()

        if latest is None:
            if wx.MessageBox(
                'Could not reach GitHub to check for updates.\n'
                'Open the releases page in your browser?',
                'Check for updates', wx.YES_NO | wx.ICON_QUESTION, self,
            ) == wx.YES:
                webbrowser.open(releases_url)
            return

        def _ver(s):
            try:
                return tuple(int(x) for x in s.lower().lstrip('v').split('.'))
            except ValueError:
                return (0,)

        if _ver(latest) <= _ver(PLUGIN_VERSION):
            wx.MessageBox(
                f'You are running the latest version: {PLUGIN_VERSION}.',
                'Check for updates', wx.OK | wx.ICON_INFORMATION, self,
            )
        else:
            if wx.MessageBox(
                f'A newer version is available: {latest}\n'
                f'(installed: {PLUGIN_VERSION})\n\n'
                'Open the releases page?',
                'Update available', wx.YES_NO | wx.ICON_INFORMATION, self,
            ) == wx.YES:
                webbrowser.open(releases_url)

    def _on_report_issue(self, _evt) -> None:
        import urllib.parse, webbrowser
        sysinfo = self._collect_sysinfo()
        body = (
            f'{sysinfo}\n\n'
            '---\n\n'
            '**Describe the issue:**\n'
            '<!-- What went wrong? -->\n\n'
            '**Steps to reproduce:**\n'
            '1. \n'
            '2. \n\n'
            '**Expected behaviour:**\n'
            '<!-- What did you expect to happen? -->'
        )
        url = (f'https://github.com/{REPO}/issues/new'
               f'?body={urllib.parse.quote(body)}')
        webbrowser.open(url)

    def _on_validate(self, _evt) -> None:
        if self.netlist is None:
            self.SetStatusText('No netlist loaded.', 0)
            return

        self._validation_active = True
        result = validate(self.board, self.netlist)

        if result.ok:
            self.canvas.clear_highlights()
            self.SetStatusText('Circuit OK — all nets match the schematic.', 0)
            wx.MessageBox('Circuit is correct!', 'Validation', wx.OK | wx.ICON_INFORMATION, self)
        else:
            self.canvas.set_validation_result(result)
            lines = [str(i) for i in result.issues]
            summary = f"{len(result.issues)} issue(s) found."
            self.SetStatusText(summary, 0)
            wx.MessageBox('\n'.join(lines), 'Validation issues',
                          wx.OK | wx.ICON_WARNING, self)

    def _on_pin_fn(self, evt) -> None:
        on = self._pin_fn_cb.GetValue()
        self.canvas.set_dip_fn_labels(on)
        self.canvas.set_rpi_long_labels(on)

    def _on_clear_warnings(self, _evt) -> None:
        self._validation_active = False
        self.canvas.clear_highlights()
        self.SetStatusText('Validation markers cleared.', 0)

    def _on_simulate(self, _evt) -> None:
        if self.netlist is None:
            wx.MessageBox('Load a netlist or schematic before simulating.',
                          'Simulate', wx.OK | wx.ICON_INFORMATION, self)
            return
        if not self.board.terminal_nets.get('GND'):
            wx.MessageBox('Assign the GND binding post to a schematic net before simulating.',
                          'Simulate', wx.OK | wx.ICON_INFORMATION, self)
            return
        if self._sim_pane is None:
            self._sim_pane = SimPane(
                self.canvas, self.board, self.netlist,
                on_run=self._run_simulation,
                on_close=self._close_sim_pane,
                on_clear=self._clear_simulation,
                on_volt_labels_toggle=self._on_volt_labels_toggle,
                on_run_transient=self._run_transient_simulation,
                scope_channels=self.prefs.scope_channels,
            )
        self._sim_pane.Show()
        self._sim_pane.Raise()

    def _run_simulation(self, terminal_voltages: dict) -> None:
        if not self.board.placements:
            self._sim_pane.show_error('No components placed on the board.')
            return

        # Validate board wiring before simulating — open nets and shorts make
        # SPICE results meaningless (it simulates the schematic, not the board).
        if self.netlist:
            vresult = validate(self.board, self.netlist)
            blocking = [i for i in vresult.issues
                        if i.kind in (IssueKind.OPEN_NET, IssueKind.SHORT)]
            if blocking:
                self.canvas.set_validation_result(vresult)
                lines = ['Wiring errors — fix before simulating:\n']
                for issue in blocking:
                    icon = '⚡' if issue.kind == IssueKind.SHORT else '?'
                    lines.append(f'  {icon}  {issue.description}')
                self._sim_pane.show_error('\n'.join(lines))
                self.SetStatusText(
                    f'Simulation blocked — {len(blocking)} wiring error(s).', 0)
                return

        # Clear any stale results from a previous run before starting
        self.canvas.clear_simulation()
        self._sim_pane.show_running()
        self.SetStatusText('Running ngspice simulation…', 0)
        self.Update()
        try:
            result = simulate(self.board, self.netlist, terminal_voltages)
        except Exception as exc:
            self._sim_pane.show_error(f'Unexpected error:\n{exc}')
            self.SetStatusText('Simulation failed.', 0)
            return
        if result.error:
            self._sim_pane.show_error(result.error, result)
            self.SetStatusText('Simulation failed.', 0)
        else:
            self.canvas.set_simulation_result(result)
            self._sim_pane.show_results(result)
            n = len(result.net_voltages)
            w = len(result.warnings)
            status = f'Simulation complete — {n} node voltage(s)'
            if w:
                status += f', {w} warning(s)'
            self.SetStatusText(status, 0)

    def _clear_simulation(self) -> None:
        self.canvas.clear_simulation()
        if self._sim_pane:
            self._sim_pane.clear_results()
        self.SetStatusText('', 0)

    def _close_sim_pane(self) -> None:
        if self._sim_pane:
            self._sim_pane.Destroy()
            self._sim_pane = None
        if self._waveform_frame:
            self._waveform_frame.Destroy()
            self._waveform_frame = None
        self.canvas.end_net_probe()
        self.canvas.clear_all_scope_probes()
        self.canvas.clear_simulation()
        self.canvas.Refresh()
        self.SetStatusText('', 0)

    def _run_transient_simulation(self, terminal_voltages: dict) -> None:
        if not self.board.placements:
            self._open_waveform_frame({}, [])
            self.SetStatusText('KiScope opened — place components and run transient analysis to show signals.', 0)
            return

        if self.netlist:
            vresult = validate(self.board, self.netlist)
            blocking = [i for i in vresult.issues
                        if i.kind in (IssueKind.OPEN_NET, IssueKind.SHORT)]
            if blocking:
                self.canvas.set_validation_result(vresult)
                lines = ['Wiring errors — fix before simulating:\n']
                for issue in blocking:
                    icon = '⚡' if issue.kind == IssueKind.SHORT else '?'
                    lines.append(f'  {icon}  {issue.description}')
                self._sim_pane.show_error('\n'.join(lines))
                self.SetStatusText(
                    f'Simulation blocked — {len(blocking)} wiring error(s).', 0)
                return

        self.canvas.clear_simulation()
        self._sim_pane.show_running()
        self.SetStatusText('Running transient simulation…', 0)
        self.Update()

        try:
            result = simulate_transient(
                self.board, self.netlist, terminal_voltages,
                plot_nets=None,   # all nets — probes can move after run
            )
        except Exception as exc:
            self._sim_pane.show_error(f'Unexpected error:\n{exc}')
            self.SetStatusText('Transient simulation failed.', 0)
            return

        if result.error:
            self._sim_pane.show_error(result.error, result)
            self.SetStatusText('Transient simulation failed.', 0)
            return

        board_nets = self._board_signal_nets()
        result.transient_traces = {
            net: trace for net, trace in result.transient_traces.items()
            if net in board_nets
        }

        self.canvas.set_simulation_result(result)
        self._sim_pane.show_results(result)
        n = len(result.transient_traces)
        w = len(result.warnings)
        status = f'Transient complete — {n} trace(s)'
        if w:
            status += f', {w} warning(s)'
        self.SetStatusText(status, 0)

        self._open_waveform_frame(result.transient_traces, result.warnings or [])

    def _open_waveform_frame(self, traces: dict, warnings: list) -> None:
        if self._waveform_frame:
            self._waveform_frame.Destroy()
        self.canvas.clear_all_scope_probes()
        from .waveform import WaveformFrame
        self._waveform_frame = WaveformFrame(
            self, traces,
            on_probe_toggle=self._on_waveform_probe_toggle,
            on_clear_probes=self.canvas.clear_all_scope_probes,
            warnings=warnings,
            num_channels=self.prefs.scope_channels,
            initial_channel_nets=self._scope_channel_nets(),
        )
        self._waveform_frame.Show()
        self._refresh_waveform_probe_markers()

    def _board_signal_nets(self) -> set:
        """Return schematic nets represented by placed component pins on the board."""
        if not self.board or not self.netlist:
            return set()
        nets = set()
        for ref, placed in self.board.placements.items():
            nets_dict = self.netlist.nets_for_ref(ref)
            for pin_num, net in nets_dict.items():
                if placed.pin_holes.get(pin_num) is not None and net.name:
                    nets.add(net.name)
        return nets

    def _scope_channel_nets(self) -> list:
        return [self.board.get_probe_net(f'CH{i}') or None for i in range(1, 5)]

    def _reopen_waveform_frame(self, num_channels: int) -> None:
        from .waveform import WaveformFrame

        old = self._waveform_frame
        traces = old._traces
        warnings = old._warnings
        channel_nets = old.channel_net_names
        old.Destroy()
        self._waveform_frame = WaveformFrame(
            self, traces,
            on_probe_toggle=self._on_waveform_probe_toggle,
            on_clear_probes=self.canvas.clear_all_scope_probes,
            warnings=warnings,
            num_channels=num_channels,
            initial_channel_nets=channel_nets,
        )
        self._waveform_frame.Show()
        self._refresh_waveform_probe_markers()

    def _refresh_waveform_probe_markers(self) -> None:
        if not self._waveform_frame:
            return
        from .waveform import _CH_COLORS

        self.canvas.clear_all_scope_probes()
        for i, net_name in enumerate(self._waveform_frame.channel_net_names):
            if net_name:
                ch_name = f'CH{i + 1}'
                # Prefer the instrument-panel probe hole for this channel.
                hole = self.board.get_probe_hole(ch_name)
                if hole is None:
                    hole = self._find_probe_hole_for_net(net_name)
                if hole is not None:
                    self.canvas.set_scope_probe(
                        i, _CH_COLORS[i % len(_CH_COLORS)],
                        ch_name, hole,
                    )
        self.canvas.Refresh()

    def _on_waveform_probe_toggle(self, active: bool) -> None:
        """Called by WaveformFrame when the Probe button is toggled."""
        if active:
            self.canvas.begin_net_probe(self._on_net_probed)
            self.SetStatusText('Probe mode — click a net on the breadboard', 0)
        else:
            self.canvas.end_net_probe()
            self.SetStatusText('', 0)

    def _on_net_probed(self, net_name: str, hole=None) -> None:
        """Called by canvas when a net is clicked in probe mode."""
        if not (self._waveform_frame and self._waveform_frame.IsShown()):
            return
        ch_idx = self._waveform_frame.probing_channel
        self._waveform_frame.toggle_net(net_name)
        if ch_idx is not None and hole is not None:
            from .waveform import _CH_COLORS
            ch_name = f'CH{ch_idx + 1}'
            self.canvas.set_scope_probe(
                ch_idx, _CH_COLORS[ch_idx % len(_CH_COLORS)],
                ch_name, hole,
            )
            self.board.place_probe(ch_name, hole)
            self.board.assign_probe_net(ch_name, net_name)
            self._refresh_probe_buttons()
            self._refresh_probe_choices()

    def _find_probe_hole_for_net(self, net_name: str):
        """Return any placed-component hole connected to net_name."""
        if not self.board or not self.netlist:
            return None
        for ref, pc in self.board.components.items():
            comp = self.netlist.components.get(ref)
            if not comp:
                continue
            for pin in comp.pins:
                if pin.net != net_name:
                    continue
                for pin_num, hole in pc.pin_holes.items():
                    if str(pin_num) == str(pin.number):
                        return hole
        return None

    def _on_volt_labels_toggle(self, show: bool) -> None:
        self.canvas.show_voltage_labels = show
        self.canvas.Refresh()

    def _on_clear(self, _evt) -> None:
        if wx.MessageBox(
            'Clear all placed components and wires?', 'Confirm',
            wx.YES_NO | wx.ICON_QUESTION, self
        ) == wx.YES:
            self.canvas.push_undo()
            self.board = Breadboard(layout=self.prefs.board_layout, rail_split=self.prefs.rail_split)
            # Re-apply GND assignments
            _gnd_net = next((n for n in ('0', 'GND') if self.netlist and self.netlist.net_by_name(n)), None)
            if _gnd_net:
                self.board.assign_terminal('GND', _gnd_net)
                if self.prefs.auto_gnd:
                    for _pname in ('FG_GND', 'SCOPE_GND'):
                        self.board.assign_probe_net(_pname, _gnd_net)
            self.canvas.reload_board(self.board)
            self.tray.board = self.board
            self.tray.refresh_placed()
            self._refresh_terminal_choices()
            self._refresh_probe_choices()
            self._refresh_probe_buttons()
            self._validation_active = False
            self.canvas.clear_highlights()
            if self._sim_pane:
                self._sim_pane.refresh_sources(self.board)
                self._sim_pane.clear_results()
            self.canvas.Refresh()
            self.SetStatusText('Board cleared.', 0)

    # ------------------------------------------------------------------
    # Netlist loading
    # ------------------------------------------------------------------

    def _auto_load_netlist(self, project_path: str) -> None:
        self._project_path = project_path
        net_path = find_netlist(project_path)
        if not net_path:
            net_path = self._export_netlist(silent=True)
        else:
            # Re-export silently if the schematic is newer than the saved netlist.
            sch = find_schematic(project_path)
            if sch and sch.stat().st_mtime > net_path.stat().st_mtime:
                exported = self._export_netlist(silent=True)
                if exported:
                    net_path = exported
        if net_path:
            self._load_netlist(str(net_path))

    def _on_term_choice(self, term_name: str, evt) -> None:
        if self._refreshing_choices:
            return
        ch = self._term_choices[term_name]
        sel = ch.GetSelection()
        # item 0 is "(unassigned)"; items 1..n are net names
        net = ch.GetString(sel) if sel > 0 else ''
        self.canvas.push_undo()
        self.board.assign_terminal(term_name, net)
        if self._sim_pane:
            self._sim_pane.refresh_sources(self.board)
        self._revalidate_live()
        self.canvas.Refresh()

    def _refresh_terminal_choices(self) -> None:
        """Repopulate the binding-post dropdowns from the loaded netlist."""
        if self.netlist is None:
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        choices = ['(unassigned)'] + net_names
        self._refreshing_choices = True
        try:
            for name, ch in self._term_choices.items():
                ch.SetItems(choices)
                current = self.board.get_terminal_net(name)
                if current in net_names:
                    ch.SetSelection(net_names.index(current) + 1)
                else:
                    ch.SetSelection(0)
        finally:
            self._refreshing_choices = False

    def _on_terminal_right_click(self, term_name: str, screen_pos) -> None:
        """Show a net-assignment context menu for a binding post."""
        if self.netlist is None:
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        current = self.board.get_terminal_net(term_name) or ''

        menu = wx.Menu()
        menu.SetTitle(f'{term_name}')

        # Heading item (disabled, just shows the terminal name)
        head = menu.Append(wx.ID_ANY, f'Assign {term_name} to net:')
        head.Enable(False)
        menu.AppendSeparator()

        # "Unassign" at the top
        id_unassign = wx.NewIdRef()
        item = menu.AppendCheckItem(id_unassign, '(unassigned)')
        item.Check(current == '')
        menu.Bind(wx.EVT_MENU, lambda _: self._assign_terminal_from_menu(term_name, ''), id_unassign)

        for net in net_names:
            item_id = wx.NewIdRef()
            item = menu.AppendCheckItem(item_id, net)
            item.Check(net == current)
            menu.Bind(wx.EVT_MENU,
                      lambda _, n=net: self._assign_terminal_from_menu(term_name, n),
                      item_id)

        self.canvas.PopupMenu(menu, screen_pos)
        menu.Destroy()

    def _assign_terminal_from_menu(self, term_name: str, net: str) -> None:
        self.canvas.push_undo()
        self.board.assign_terminal(term_name, net)
        self._refresh_terminal_choices()
        if self._sim_pane:
            self._sim_pane.refresh_sources(self.board)
        self._revalidate_live()
        self.canvas.Refresh()

    # ------------------------------------------------------------------
    # Instrument probe handlers
    # ------------------------------------------------------------------

    def _net_at_hole(self, hole) -> Optional[str]:
        """Return the schematic net name at hole via board connectivity + netlist."""
        if self.netlist is None or hole is None:
            return None
        uf = self.board.build_connectivity()
        target = uf.find(hole)
        for ref, placed in self.board.placements.items():
            nets_dict = self.netlist.nets_for_ref(ref)
            for pin_num, net in nets_dict.items():
                pin_hole = placed.pin_holes.get(pin_num)
                if pin_hole is not None and uf.find(pin_hole) == target:
                    return net.name
        return None

    def _on_probe_placed(self, name: str) -> None:
        """Called after a probe is placed. Always syncs net to the hole's actual net."""
        if name.startswith('CH') or name == 'FG+':
            hole = self.board.get_probe_hole(name)
            net = self._net_at_hole(hole) or ''
            # GND (net '0') is always 0 V — clear assignment rather than track it
            gnd = self.board.terminal_nets.get('GND', '')
            if net == '0' or (gnd and net == gnd):
                net = ''
            self.board.assign_probe_net(name, net)
        self._refresh_probe_buttons()
        self._refresh_probe_choices()
        if self._sim_pane:
            self._sim_pane.refresh_probes(self.board)
        self._revalidate_live()
        if name.startswith('CH') and self._waveform_frame and self._waveform_frame.IsShown():
            self._refresh_waveform_probe_markers()
        else:
            self.canvas.Refresh()

    def _on_probe_choice(self, probe_name: str, _evt) -> None:
        if self._refreshing_choices:
            return
        ch = self._probe_choices[probe_name]
        sel = ch.GetSelection()
        net = ch.GetString(sel) if sel > 0 else ''
        self.board.assign_probe_net(probe_name, net)
        self._refresh_probe_warnings()
        if self._sim_pane:
            self._sim_pane.refresh_probes(self.board)
        self._revalidate_live()
        self.canvas.Refresh()

    def _on_probe_place_btn(self, probe_name: str) -> None:
        if self.board.get_probe_hole(probe_name) is not None:
            # Already placed — remove it
            self.board.remove_probe(probe_name)
            self._refresh_probe_buttons()
            if self._sim_pane:
                self._sim_pane.refresh_probes(self.board)
            self._revalidate_live()
            self.canvas.Refresh()
        else:
            # Start placement mode
            self.canvas.begin_probe_place(probe_name)
            self.SetStatusText(
                f'Click a hole to place {PROBE_META[probe_name]["label"]} probe. '
                'Esc to cancel.', 0)

    def _refresh_probe_buttons(self) -> None:
        for name, btn in self._probe_place_btns.items():
            placed = self.board.get_probe_hole(name) is not None
            btn.SetLabel('Remove' if placed else 'Place')
        self._refresh_probe_warnings()

    def _refresh_probe_choices(self) -> None:
        """Repopulate probe net dropdowns from the loaded netlist."""
        if self.netlist is None:
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        choices = ['(unassigned)'] + net_names
        self._refreshing_choices = True
        try:
            for name, ch in self._probe_choices.items():
                ch.SetItems(choices)
                current = self.board.get_probe_net(name)
                if current in net_names:
                    ch.SetSelection(net_names.index(current) + 1)
                else:
                    ch.SetSelection(0)
        finally:
            self._refreshing_choices = False

    def _refresh_probe_warnings(self) -> None:
        """Update ⚠ indicator on each CH label when its placed hole doesn't match its assigned net."""
        for name, lbl in self._probe_labels.items():
            meta = PROBE_META[name]
            assigned = self.board.get_probe_net(name)
            hole = self.board.get_probe_hole(name)
            mismatch = False
            if assigned and hole is not None:
                actual = self._net_at_hole(hole)
                mismatch = actual is not None and actual != assigned
            if mismatch:
                lbl.SetLabel('⚠ ' + meta['label'])
                lbl.SetForegroundColour(wx.Colour(200, 130, 0))
                lbl.SetToolTip(f'Probe is on net "{self._net_at_hole(hole)}", not "{assigned}"')
            else:
                lbl.SetLabel(meta['label'])
                lbl.SetForegroundColour(wx.Colour(meta['color']))
                lbl.SetToolTip('')

    def _load_netlist(self, path: str) -> None:
        if path.endswith('.kicad_sch'):
            # Export via kicad-cli first; direct parsing is not used
            from pathlib import Path as _Path
            self._project_path = str(_Path(path).parent)
            net_path = self._export_netlist(silent=False)
            if not net_path:
                return
            path = str(net_path)
        try:
            self.netlist = parse_netlist(path)
        except Exception as exc:
            wx.MessageBox(f'Failed to load netlist:\n{exc}',
                          'Error', wx.OK | wx.ICON_ERROR, self)
            return
        self._netlist_path = path

        self.canvas.netlist = self.netlist
        self.tray.load_netlist(self.netlist)

        # Auto-assign GND terminal and instrument grounds to the ground net ("0" or "GND")
        _gnd_net = next((n for n in ('0', 'GND') if self.netlist.net_by_name(n)), None)
        if _gnd_net:
            self.board.assign_terminal('GND', _gnd_net)
            if self.prefs.auto_gnd:
                for _pname in ('FG_GND', 'SCOPE_GND'):
                    self.board.assign_probe_net(_pname, _gnd_net)

        self._refresh_terminal_choices()
        self._refresh_probe_choices()
        if self._sim_pane:
            self._sim_pane._build(self.board, self.netlist)
            wx.CallAfter(self._sim_pane.Fit)

        n_total = len(self.netlist.components)
        n_shown = sum(
            1 for ref, comp in self.netlist.components.items()
            if guess_type_id(ref, comp.value, comp.symbol, comp.lib, comp.description, comp.pin_count) is not None
        )
        if n_total == 0:
            self.SetStatusText(
                f'No components found in {Path(path).name} — '
                'save your schematic in Eeschema first, then use "Update from schematic".', 0
            )
        else:
            note = ''
            if n_total > n_shown:
                note = (f'  ({n_total - n_shown} power/virtual component(s) '
                        'not shown — assign via binding posts.)')
            self.SetStatusText(
                f'Loaded {n_shown} component(s) from {Path(path).name}.{note}  '
                'Click a component in the tray to place it.', 0
            )


# ---------------------------------------------------------------------------
# Simulation pane (persistent canvas overlay)
# ---------------------------------------------------------------------------

class SimPane(wx.Panel):
    """Floating panel pinned to the top-left of BreadboardCanvas."""

    _W = 230

    def __init__(self, parent_canvas, board, netlist, *,
                 on_run, on_close, on_clear, on_volt_labels_toggle, on_run_transient=None,
                 scope_channels=2):
        super().__init__(parent_canvas, style=wx.BORDER_SIMPLE)
        self._on_run_cb            = on_run
        self._on_close_cb          = on_close
        self._on_clear_cb          = on_clear
        self._on_volt_labels_toggle = on_volt_labels_toggle
        self._on_run_transient_cb  = on_run_transient
        self._netlist              = netlist
        self._scope_channels       = scope_channels
        self._volt_ctrls: dict     = {}
        self._result_label         = None
        self._result_text          = None
        self._console_text         = None
        self._console_btn          = None
        self._console_open         = False
        self._volt_labels_cb       = None
        self._build(board)
        self.SetPosition(wx.Point(8, 8))

    # ------------------------------------------------------------------

    def _make_button(self, label: str, handler, *, style: int = 0) -> wx.Button:
        btn = wx.Button(self, label=label, style=wx.BORDER_SIMPLE | style)
        btn.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
        btn.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT))
        btn.Bind(wx.EVT_BUTTON, handler)
        return btn

    def _build(self, board, netlist=None) -> None:
        if netlist is not None:
            self._netlist = netlist
        self._board = board
        self.DestroyChildren()
        self._volt_ctrls = {}
        self.SetBackgroundColour(_panel_bg())

        outer = wx.BoxSizer(wx.VERTICAL)

        # Header
        hdr = wx.Panel(self)
        hdr.SetBackgroundColour(wx.Colour(45, 45, 55))
        hdr_sz = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(hdr, label='⚡  Simulation')
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        close_btn = wx.Button(hdr, label='×', size=(22, 22),
                              style=wx.BORDER_NONE | wx.BU_EXACTFIT)
        close_btn.SetBackgroundColour(wx.Colour(45, 45, 55))
        close_btn.SetForegroundColour(wx.Colour(180, 180, 180))
        close_btn.Bind(wx.EVT_BUTTON, lambda _: self._on_close_cb())
        hdr_sz.Add(title, 1, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 8)
        hdr_sz.Add(close_btn, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 2)
        hdr.SetSizer(hdr_sz)
        outer.Add(hdr, 0, wx.EXPAND)

        body = wx.BoxSizer(wx.VERTICAL)

        # Sources
        src_lbl = wx.StaticText(self, label='Operating Point Analysis')
        src_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
        src_lbl.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        body.Add(src_lbl, 0, wx.LEFT | wx.TOP, 8)

        _TC = {'GND': '#3a3a3a', 'V1': '#bb2020', 'V2': '#1a7a30'}
        initial_voltages = initial_terminal_voltages(board, self._netlist) if self._netlist else {}
        grid = wx.FlexGridSizer(cols=3, vgap=4, hgap=6)
        grid.AddGrowableCol(1)
        for term in ('GND', 'V1', 'V2'):
            net = board.terminal_nets.get(term, '')
            lbl = wx.StaticText(self, label=term)
            lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
            lbl.SetForegroundColour(wx.Colour(_TC[term]))
            net_lbl = wx.StaticText(self, label=net or '—')
            net_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                                    wx.FONTWEIGHT_NORMAL))
            net_lbl.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            if term == 'GND' or not net:
                fixed = wx.StaticText(self, label='0 V' if term == 'GND' else '')
                fixed.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                      wx.FONTWEIGHT_NORMAL))
                grid.Add(lbl,   0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(fixed, 0, wx.ALIGN_CENTRE_VERTICAL)
                self._volt_ctrls[term] = None
            else:
                sp = wx.SpinCtrlDouble(self, min=-100.0, max=100.0,
                                       initial=initial_voltages.get(term, 5.0),
                                       inc=0.5, size=(68, -1))
                sp.SetDigits(2)
                if term in initial_voltages:
                    sp.SetToolTip('Initial value from schematic voltage source')
                grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(sp, 0, wx.ALIGN_CENTRE_VERTICAL)
                self._volt_ctrls[term] = sp
        body.Add(grid, 0, wx.EXPAND | wx.ALL, 8)

        # Run / Clear buttons
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        run_btn = self._make_button('▶  Run', self._on_run)
        clr_btn = self._make_button('Clear', lambda _: self._on_clear_cb())
        btn_row.Add(run_btn, 1, wx.EXPAND | wx.RIGHT, 4)
        btn_row.Add(clr_btn, 0)
        body.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        # Voltage labels toggle
        self._volt_labels_cb = wx.CheckBox(self, label='Show voltage labels on board')
        self._volt_labels_cb.SetValue(True)
        self._volt_labels_cb.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                             wx.FONTWEIGHT_NORMAL))
        self._volt_labels_cb.Bind(wx.EVT_CHECKBOX,
                                  lambda _: self._on_volt_labels_toggle(self._volt_labels_cb.GetValue()))
        body.Add(self._volt_labels_cb, 0, wx.LEFT | wx.BOTTOM, 8)

        # Transient Analysis section (only shown when VSIN sources are found)
        if self._netlist and self._on_run_transient_cb:
            vsin_list = find_vsin_sources(self._netlist)
            if vsin_list:
                body.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
                tran_lbl = wx.StaticText(self, label='Transient Analysis')
                tran_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                         wx.FONTWEIGHT_BOLD))
                tran_lbl.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
                body.Add(tran_lbl, 0, wx.LEFT | wx.TOP, 8)

                tran_btn = self._make_button('▶  Open KiScope', self._on_run_transient)
                body.Add(tran_btn, 0, wx.EXPAND | wx.ALL, 8)

        # Results
        self._result_label = wx.StaticText(self, label='Simulation output')
        self._result_label.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                           wx.FONTWEIGHT_NORMAL))
        self._result_label.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        self._result_label.Hide()
        body.Add(self._result_label, 0, wx.LEFT | wx.TOP, 8)
        self._result_text = wx.TextCtrl(
            self, value='',
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE,
            size=(-1, 120),
        )
        self._result_text.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        self._result_text.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        self._result_text.SetFont(wx.Font(8, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                          wx.FONTWEIGHT_NORMAL))
        self._result_text.Hide()
        body.Add(self._result_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        # Console expander
        self._console_btn = self._make_button('▶  Console', self._on_console_toggle,
                                              style=wx.BU_LEFT)
        self._console_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                          wx.FONTWEIGHT_NORMAL))
        self._console_btn.Hide()
        body.Add(self._console_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        self._console_text = wx.TextCtrl(
            self, value='',
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.BORDER_NONE,
            size=(-1, 160),
        )
        self._console_text.SetBackgroundColour(wx.Colour(28, 28, 36))
        self._console_text.SetForegroundColour(wx.Colour(180, 220, 180))
        self._console_text.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                           wx.FONTWEIGHT_NORMAL))
        self._console_text.Hide()
        body.Add(self._console_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        outer.Add(body, 0, wx.EXPAND)
        self.SetSizer(outer)
        self.SetMinSize(wx.Size(self._W, -1))
        self.SetMaxSize(wx.Size(self._W, 9999))
        self.Fit()

    def refresh_sources(self, board) -> None:
        self._build(board)

    def refresh_probes(self, board) -> None:
        self._build(board)

    def set_scope_channels(self, n: int, board) -> None:
        self._scope_channels = n
        self._build(board)

    def _on_run(self, _evt) -> None:
        tv = {t: sp.GetValue() for t, sp in self._volt_ctrls.items() if sp is not None}
        self._on_run_cb(tv)

    def _on_run_transient(self, _evt) -> None:
        tv = {t: sp.GetValue() for t, sp in self._volt_ctrls.items() if sp is not None}
        if self._on_run_transient_cb:
            self._on_run_transient_cb(tv)

    def _on_console_toggle(self, _evt) -> None:
        self._console_open = not self._console_open
        self._console_btn.SetLabel('▼  Console' if self._console_open else '▶  Console')
        self._console_text.Show(self._console_open)
        wx.CallAfter(self.Fit)

    def _populate_console(self, result) -> None:
        parts = []
        if result.spice_netlist:
            parts.append('--- Netlist ---')
            parts.append(result.spice_netlist)
        if result.spice_output:
            parts.append('--- ngspice output ---')
            parts.append(result.spice_output)
        self._console_text.SetValue('\n'.join(parts))
        self._console_btn.Show()
        if self._console_open:
            self._console_text.Show()

    def show_running(self) -> None:
        self._result_label.Show()
        self._result_text.SetValue('Running simulation…')
        self._result_text.Show()
        wx.CallAfter(self.Fit)

    def show_results(self, result) -> None:
        lines = []
        if getattr(result, 'warnings', None):
            lines.append('⚠ Warnings')
            lines.append('─' * 28)
            for w in result.warnings:
                lines.append(f'  {w}')
            lines.append('')
        if getattr(result, 'transient_traces', None):
            lines.append('Transient traces')
            lines.append('─' * 28)
            for net in sorted(result.transient_traces):
                tr = result.transient_traces[net]
                n_pts = len(tr.times)
                lines.append(f'  {net:<20s}  {n_pts} pts')
        if result.net_voltages:
            lines.append('Node voltages')
            lines.append('─' * 28)
            for net, v in sorted(result.net_voltages.items()):
                lines.append(f'  {net:<18s}  {v:+.4f} V')
        if result.branch_currents:
            lines.append('')
            lines.append('Branch currents')
            lines.append('─' * 28)
            for ref, i_a in sorted(result.branch_currents.items()):
                ma = i_a * 1000
                lines.append(f'  {ref:<18s}  {ma:+.3f} mA')
        self._result_label.Show()
        self._result_text.SetValue('\n'.join(lines))
        self._result_text.Show()
        self._populate_console(result)
        wx.CallAfter(self.Fit)

    def show_error(self, msg: str, result=None) -> None:
        lines = [f'Error:\n{msg}']
        if result is not None and getattr(result, 'warnings', None):
            lines.append('')
            lines.append('⚠ Warnings')
            lines.append('─' * 28)
            for w in result.warnings:
                lines.append(f'  {w}')
        self._result_label.Show()
        self._result_text.SetValue('\n'.join(lines))
        self._result_text.Show()
        if result is not None:
            self._populate_console(result)
        wx.CallAfter(self.Fit)

    def clear_results(self) -> None:
        if self._result_label:
            self._result_label.Hide()
        if self._result_text:
            self._result_text.Hide()
        if self._console_btn:
            self._console_btn.Hide()
        if self._console_text:
            self._console_text.Hide()
        self.Fit()


# ---------------------------------------------------------------------------
# Simulation dialog
# ---------------------------------------------------------------------------

class SimulationDialog(wx.Dialog):
    """Ask the user for terminal voltages, then show simulation results."""

    def __init__(self, parent: wx.Window, board, netlist):
        super().__init__(parent, title='SPICE Simulation',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._board   = board
        self._netlist = netlist

        sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Terminal voltage inputs ----
        sizer.Add(wx.StaticText(self, label='Set terminal voltages for DC analysis:'),
                  0, wx.ALL, 10)

        grid = wx.FlexGridSizer(rows=0, cols=3, vgap=6, hgap=8)
        grid.AddGrowableCol(1)
        _TERM_COLORS = {'GND': '#3a3a3a', 'V1': '#bb2020', 'V2': '#1a7a30'}
        self._volt_ctrls: dict = {}
        for term in ('GND', 'V1', 'V2'):
            net = board.terminal_nets.get(term, '')
            if not net and term != 'GND':
                continue
            lbl = wx.StaticText(self, label=term)
            lbl.SetForegroundColour(_TERM_COLORS.get(term, '#000000'))
            lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
            net_lbl = wx.StaticText(self, label=f'= {net or "(unassigned)"}')
            net_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                                    wx.FONTWEIGHT_NORMAL))
            net_lbl.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            if term == 'GND':
                val_lbl = wx.StaticText(self, label='0 V  (fixed)')
                grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(val_lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
            else:
                sp = wx.SpinCtrlDouble(self, min=-100.0, max=100.0,
                                       initial=5.0, inc=0.5, size=(80, -1))
                sp.SetDigits(2)
                self._volt_ctrls[term] = sp
                grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                v_row = wx.BoxSizer(wx.HORIZONTAL)
                v_row.Add(sp, 0, wx.ALIGN_CENTRE_VERTICAL)
                v_row.Add(wx.StaticText(self, label=' V'), 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(v_row, 0, wx.ALIGN_CENTRE_VERTICAL)

        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # ---- Info ----
        info = wx.StaticText(self,
            label='\nDC operating point (.op) — resistors, capacitors,\n'
                  'inductors, diodes, LEDs, and BJTs are supported.')
        info.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                             wx.FONTWEIGHT_NORMAL))
        info.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(info, 0, wx.LEFT | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Buttons ----
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL), 0, wx.RIGHT, 8)
        run_btn = wx.Button(self, wx.ID_OK, label='Run simulation')
        run_btn.SetDefault()
        btn_sizer.Add(run_btn, 0)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)

        self.SetSizer(sizer)
        sizer.Fit(self)
        self.CentreOnParent()

    def get_terminal_voltages(self) -> dict:
        return {term: sp.GetValue() for term, sp in self._volt_ctrls.items()}


# ---------------------------------------------------------------------------
# Preferences dialog
# ---------------------------------------------------------------------------

class PreferencesDialog(wx.Dialog):
    """Modal dialog for all user preferences."""

    def __init__(self, parent: wx.Window, prefs: Preferences):
        super().__init__(parent, title='Preferences',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        sizer = wx.BoxSizer(wx.VERTICAL)

        def section(label: str) -> wx.StaticText:
            t = wx.StaticText(self, label=label)
            t.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
            return t

        def radio_row(label: str, choices, selected: int):
            """Return (sizer, list[RadioButton]) for a labelled inline radio group."""
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(self, label=label), 0,
                    wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
            buttons = []
            for i, ch in enumerate(choices):
                rb = wx.RadioButton(self, label=ch,
                                    style=wx.RB_GROUP if i == 0 else 0)
                rb.SetValue(i == selected)
                row.Add(rb, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 10)
                buttons.append(rb)
            return row, buttons

        _layout_map = ['mini', 'half', 'full', 'double', 'triple', 'double_rails']
        _side_map   = ['left', 'right', 'top_left', 'top_center', 'top_right',
                       'bottom_left', 'bottom_center', 'bottom_right']

        # ---- Instruments ----
        sizer.Add(section('Instruments'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self._cb_instr = wx.CheckBox(self, label='Enable instruments panel')
        self._cb_instr.SetValue(prefs.instruments_enabled)
        sizer.Add(self._cb_instr, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self._cb_auto_gnd = wx.CheckBox(
            self, label='Auto-assign schematic ground to instrument grounds')
        self._cb_auto_gnd.SetValue(prefs.auto_gnd)
        sizer.Add(self._cb_auto_gnd, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        scope_row, self._rb_scope = radio_row(
            'Oscilloscope channels:', ['1', '2', '3', '4'], prefs.scope_channels - 1)
        sizer.Add(scope_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        psu_row, self._rb_psu = radio_row(
            'PSU channels:', ['1', '2', '3'], prefs.psu_channels - 1)
        sizer.Add(psu_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Display ----
        sizer.Add(section('Display'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self._cb_labels = wx.CheckBox(self, label='Show signal labels')
        self._cb_labels.SetValue(prefs.show_net_labels)
        sizer.Add(self._cb_labels, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self._cb_hotkeys = wx.CheckBox(self, label='Show hotkey reference panel')
        self._cb_hotkeys.SetValue(prefs.show_hotkeys)
        sizer.Add(self._cb_hotkeys, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Export ----
        sizer.Add(section('Export'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        fmt_row, self._rb_fmt = radio_row(
            'Format:', ['PNG', 'SVG'], 1 if prefs.export_format == 'svg' else 0)
        sizer.Add(fmt_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Board ----
        sizer.Add(section('Board'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        layout_row = wx.BoxSizer(wx.HORIZONTAL)
        layout_row.Add(wx.StaticText(self, label='Layout:'), 0,
                       wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._ch_layout = wx.Choice(self, choices=[
            'Mini (170 holes, no rails)', 'Half (400 holes)', 'Full (830 holes)',
            'Double (2× full, stacked)', 'Triple (3× full + vertical rails)',
            'Double + side rails (2× full, left & right rails)',
        ])
        self._ch_layout.SetSelection(
            _layout_map.index(prefs.board_layout) if prefs.board_layout in _layout_map else 2)
        layout_row.Add(self._ch_layout, 1, wx.EXPAND)
        sizer.Add(layout_row, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self._cb_rail_split = wx.CheckBox(
            self, label='Power rails split in the middle (electrically disconnected)')
        self._cb_rail_split.SetValue(prefs.rail_split)
        sizer.Add(self._cb_rail_split, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        _style_map = ['bbrd_classic', 'bbrd_modern', 'solid_line', 'none']
        style_row, self._rb_rail_style = radio_row(
            'Power rail style:',
            ['bbrd classic', 'bbrd modern', 'solid line', 'no markings'],
            _style_map.index(prefs.rail_style) if prefs.rail_style in _style_map else 0,
        )
        sizer.Add(style_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self._cb_binding = wx.CheckBox(self, label='Show binding posts on board')
        self._cb_binding.SetValue(prefs.show_binding_posts)
        sizer.Add(self._cb_binding, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        post_row = wx.BoxSizer(wx.HORIZONTAL)
        post_row.Add(wx.StaticText(self, label='Binding posts side:'), 0,
                     wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._ch_post_side = wx.Choice(self, choices=[
            'Left', 'Right',
            'Top Left', 'Top Center', 'Top Right',
            'Bottom Left', 'Bottom Center', 'Bottom Right',
        ])
        _ps = prefs.binding_post_side
        if _ps == 'top':    _ps = 'top_right'
        if _ps == 'bottom': _ps = 'bottom_right'
        self._ch_post_side.SetSelection(_side_map.index(_ps) if _ps in _side_map else 0)
        post_row.Add(self._ch_post_side, 0)
        sizer.Add(post_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        term_row, self._rb_num_terminals = radio_row(
            'Number of binding posts:', ['2', '3', '4'], prefs.num_terminals - 2)
        sizer.Add(term_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        def _update_terminal_choices(_evt=None):
            layout  = _layout_map[self._ch_layout.GetSelection()]
            side    = _side_map[self._ch_post_side.GetSelection()]
            allow_4 = side not in ('left', 'right') or layout in ('double', 'triple', 'double_rails')
            btn4 = self._rb_num_terminals[2]
            if not allow_4:
                if btn4.GetValue():
                    self._rb_num_terminals[1].SetValue(True)
                btn4.Enable(False)
            else:
                btn4.Enable(True)

        self._ch_layout.Bind(wx.EVT_CHOICE, _update_terminal_choices)
        self._ch_post_side.Bind(wx.EVT_CHOICE, _update_terminal_choices)
        _update_terminal_choices()

        self._cb_baseboard = wx.CheckBox(self, label='Show baseboard')
        self._cb_baseboard.SetValue(prefs.show_baseboard)
        sizer.Add(self._cb_baseboard, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        color_row = wx.BoxSizer(wx.HORIZONTAL)
        color_row.Add(wx.StaticText(self, label='Baseboard colour:'), 0,
                      wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._cp_base = wx.ColourPickerCtrl(self)
        self._cp_base.SetColour(wx.Colour(prefs.baseboard_color))
        color_row.Add(self._cp_base, 0)
        sizer.Add(color_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self._cb_branding = wx.CheckBox(self, label='Include branding')
        self._cb_branding.SetValue(prefs.show_branding)
        sizer.Add(self._cb_branding, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        img_row = wx.BoxSizer(wx.HORIZONTAL)
        img_row.Add(wx.StaticText(self, label='Branding image:'), 0,
                    wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._tc_brand_img = wx.TextCtrl(self, value=prefs.branding_image, size=(120, -1))
        img_row.Add(self._tc_brand_img, 1, wx.EXPAND | wx.RIGHT, 4)
        browse_btn = wx.Button(self, label='Browse…', size=(70, -1))
        img_row.Add(browse_btn, 0)
        sizer.Add(img_row, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 10)

        def _on_browse(evt):
            dlg = wx.FileDialog(
                self, 'Choose branding image',
                wildcard='Images (*.png;*.jpg;*.bmp;*.svg)|*.png;*.jpg;*.jpeg;*.bmp;*.svg|All files (*.*)|*.*',
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
            if dlg.ShowModal() == wx.ID_OK:
                self._tc_brand_img.SetValue(dlg.GetPath())
            dlg.Destroy()
        browse_btn.Bind(wx.EVT_BUTTON, _on_browse)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Buttons ----
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(self, label='Save as default')
        btn_sizer.Add(save_btn, 0, wx.RIGHT, 8)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL), 0, wx.RIGHT, 8)
        ok_btn = wx.Button(self, wx.ID_OK)
        ok_btn.SetDefault()
        btn_sizer.Add(ok_btn, 0)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)

        def _on_save_default(evt):
            import dataclasses
            save_prefs(dataclasses.replace(self.get_prefs(), load_on_startup=True))
            self.EndModal(wx.ID_OK)
        save_btn.Bind(wx.EVT_BUTTON, _on_save_default)

        self.SetSizer(sizer)
        sizer.Fit(self)
        self.CentreOnParent()

    def get_prefs(self) -> Preferences:
        _layout_map = ['mini', 'half', 'full', 'double', 'triple', 'double_rails']
        _side_map   = ['left', 'right', 'top_left', 'top_center', 'top_right',
                       'bottom_left', 'bottom_center', 'bottom_right']
        _style_map  = ['bbrd_classic', 'bbrd_modern', 'solid_line', 'none']

        def _sel(buttons) -> int:
            return next(i for i, b in enumerate(buttons) if b.GetValue())

        return Preferences(
            instruments_enabled=self._cb_instr.IsChecked(),
            auto_gnd=self._cb_auto_gnd.IsChecked(),
            scope_channels=_sel(self._rb_scope) + 1,
            psu_channels=_sel(self._rb_psu) + 1,
            show_net_labels=self._cb_labels.IsChecked(),
            show_hotkeys=self._cb_hotkeys.IsChecked(),
            show_binding_posts=self._cb_binding.IsChecked(),
            num_terminals=_sel(self._rb_num_terminals) + 2,
            export_format='svg' if self._rb_fmt[1].GetValue() else 'png',
            board_layout=_layout_map[self._ch_layout.GetSelection()],
            binding_post_side=_side_map[self._ch_post_side.GetSelection()],
            show_baseboard=self._cb_baseboard.IsChecked(),
            baseboard_color=self._cp_base.GetColour().GetAsString(wx.C2S_HTML_SYNTAX),
            show_branding=self._cb_branding.IsChecked(),
            branding_image=self._tc_brand_img.GetValue(),
            rail_split=self._cb_rail_split.IsChecked(),
            rail_style=_style_map[_sel(self._rb_rail_style)],
        )
