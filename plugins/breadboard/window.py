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

from pathlib import Path
from typing import Optional

import wx

_RESOURCES = Path(__file__).parent / 'resources'
import wx.lib.stattext

from .canvas import BreadboardCanvas, CanvasLayout, MODE_SELECT, MODE_WIRE, MODE_DELETE, WIRE_COLORS
from .tray import ComponentTray
from .prefs import Preferences, save_prefs, load_prefs
from .model import (
    Breadboard, Netlist,
    parse_netlist, find_netlist, find_schematic,
    validate, IssueKind,
    ALL_DEFS, guess_type_id,
    save_session, load_session,
    PROBE_NAMES, PROBE_META,
)

PLUGIN_VERSION = 'Wotou'
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

# Wire color picker — labels mirror WIRE_COLORS order; first entry means "cycle automatically"
_WIRE_COLOR_NAMES = ['Yellow', 'Red', 'Blue', 'Green', 'Orange', 'Purple', 'Cyan', 'Grey', 'Black']
_WIRE_COLOR_LABELS = ['Auto'] + _WIRE_COLOR_NAMES


class BreadboardWindow(wx.Frame):

    def __init__(self, parent=None, project_path: Optional[str] = None):
        super().__init__(
            parent,
            title='Breadboard Builder',
            size=(1300, 600),
            style=wx.DEFAULT_FRAME_STYLE,
        )

        self.prefs = load_prefs()
        self.board = Breadboard(layout=self.prefs.board_layout)
        self.netlist: Optional[Netlist] = None
        self._project_path: Optional[str] = project_path
        self._netlist_path: Optional[str] = None   # last successfully loaded .net file
        self._refreshing_choices: bool = False     # suppress EVT_CHOICE during SetItems

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

        self.canvas = BreadboardCanvas(inner_splitter, self.board, self.netlist)

        # --- Left panel: component tray only ---
        left_panel = wx.Panel(main_panel)
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
        tray_panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
        tray_sizer = wx.BoxSizer(wx.VERTICAL)

        # --- Binding-post assignment section ---
        self._binding_panel = wx.Panel(tray_panel)
        binding_sizer = wx.BoxSizer(wx.VERTICAL)

        term_label = wx.StaticText(self._binding_panel, label='Binding posts')
        term_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
        binding_sizer.Add(term_label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 6)

        _TERM_COLORS = {'GND': '#3a3a3a', 'V1': '#bb2020', 'V2': '#1a7a30'}
        self._term_choices: dict = {}
        term_grid = wx.FlexGridSizer(rows=3, cols=2, vgap=4, hgap=6)
        term_grid.AddGrowableCol(1)
        for name in ('GND', 'V1', 'V2'):
            lbl = wx.StaticText(self._binding_panel, label=name)
            lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
            lbl.SetForegroundColour(_TERM_COLORS[name])
            ch = wx.Choice(self._binding_panel, choices=['(unassigned)'])
            ch.SetSelection(0)
            self._term_choices[name] = ch
            term_grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
            term_grid.Add(ch, 1, wx.EXPAND)
        binding_sizer.Add(term_grid, 0, wx.EXPAND | wx.ALL, 6)
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
            sub.SetForegroundColour('#555555')
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

        # Apply initial visibility based on default prefs
        for w in self._ch3_widgets:
            self._scope_grid.Show(w, False)
        for w in self._ch4_widgets:
            self._scope_grid.Show(w, False)

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
            d.SetForegroundColour('#444444')
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
            lbl.SetForegroundColour('#666666')
            lbl.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            lbl.Bind(wx.EVT_LEFT_DOWN, lambda _e: wx.LaunchDefaultBrowser(url))
            return lbl

        info_top = wx.lib.stattext.GenStaticText(tray_panel,
                                  label=f'\nRelease: {PLUGIN_VERSION}\nMade with \u2665 by')
        info_top.SetFont(info_font)
        info_top.SetForegroundColour('#666666')

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

        inner_splitter.SplitVertically(self.canvas, tray_panel, sashPosition=-260)
        inner_splitter.SetMinimumPaneSize(150)
        inner_splitter.SetSashGravity(1.0)

        # Connect tray → canvas placement flow
        self.tray.on_pick = lambda comp_def, ref: self.canvas.begin_place(comp_def, ref)
        self.tray.on_rpi_label_mode = lambda v: self.canvas.set_rpi_long_labels(v)
        self.canvas.on_placed = lambda ref: self.tray.refresh_placed()
        self.canvas.on_probe_placed = lambda name: self._refresh_probe_buttons()

        self.SetStatusBar(wx.StatusBar(self))
        self.GetStatusBar().SetFieldsCount(2)
        self.GetStatusBar().SetStatusWidths([-3, -1])
        self.SetStatusText('Load a netlist, then click a component in the tray to place it.', 0)
        self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)

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
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_TEXT | wx.TB_NOICONS)
        tb.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))

        tb.AddTool(ID_UPDATE, 'Update from schematic', wx.NullBitmap,
                   shortHelp='Re-export netlist from .kicad_sch and reload (requires kicad-cli)')
        tb.AddSeparator()
        tb.AddTool(ID_SELECT, 'Select / Move', wx.NullBitmap,
                   shortHelp='Select and move placed components',
                   kind=wx.ITEM_RADIO)
        tb.AddTool(ID_WIRE,   'Draw Wire',    wx.NullBitmap,
                   shortHelp='Draw a jumper wire between two holes',
                   kind=wx.ITEM_RADIO)
        tb.AddControl(wx.StaticText(tb, label=' '))
        self._wire_color_choice = wx.Choice(tb, choices=_WIRE_COLOR_LABELS)
        self._wire_color_choice.SetSelection(0)
        self._wire_color_choice.SetToolTip(
            'Wire colour — Auto cycles through colours each wire; pick one to fix it.')
        self._wire_color_choice.Bind(wx.EVT_CHOICE, self._on_wire_color_choice)
        tb.AddControl(self._wire_color_choice)
        tb.AddSeparator()
        tb.AddTool(ID_DELETE, 'Delete',       wx.NullBitmap,
                   shortHelp='Delete a component or wire',
                   kind=wx.ITEM_CHECK)
        tb.AddSeparator()
        tb.AddTool(ID_EXPORT,   'Export image', wx.NullBitmap,
                   shortHelp='Save the breadboard as a PNG image')
        tb.AddSeparator()
        tb.AddTool(ID_VALIDATE, 'Validate',   wx.NullBitmap,
                   shortHelp='Check if your circuit matches the schematic')
        tb.AddTool(ID_CLEAR_WARNINGS, 'Clear warnings', wx.NullBitmap,
                   shortHelp='Dismiss validation warning/short markers')
        tb.AddTool(ID_CLEAR,  'Clear board',  wx.NullBitmap,
                   shortHelp='Remove all placed components and wires')
        tb.Realize()
        self.toolbar = tb

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_MENU, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_MENU, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_MENU, self._on_save,     id=ID_SAVE)
        self.Bind(wx.EVT_MENU, self._on_load,     id=ID_LOAD)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_TOOL, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_TOOL, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_TOOL, self._on_export,   id=ID_EXPORT)
        self.Bind(wx.EVT_TOOL, self._on_select,   id=ID_SELECT)
        self.Bind(wx.EVT_TOOL, self._on_wire,     id=ID_WIRE)
        self.Bind(wx.EVT_TOOL, self._on_delete,   id=ID_DELETE)
        self.Bind(wx.EVT_MENU, self._on_prefs,          id=ID_PREFS)
        self.Bind(wx.EVT_MENU, self._on_check_updates,  id=ID_HELP_UPDATES)
        self.Bind(wx.EVT_MENU, self._on_report_issue,   id=ID_HELP_ISSUE)
        self.Bind(wx.EVT_TOOL, self._on_validate,       id=ID_VALIDATE)
        self.Bind(wx.EVT_TOOL, self._on_clear_warnings, id=ID_CLEAR_WARNINGS)
        self.Bind(wx.EVT_TOOL, self._on_clear,          id=ID_CLEAR)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        for name, ch in self._term_choices.items():
            ch.Bind(wx.EVT_CHOICE, lambda evt, n=name: self._on_term_choice(n, evt))

    # ------------------------------------------------------------------
    # Toolbar handlers
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """Switch canvas mode and keep toolbar state in sync."""
        self.canvas.set_mode(mode)
        # Select/Wire are ITEM_RADIO (same group); Delete is ITEM_CHECK — toggle all explicitly.
        self.toolbar.ToggleTool(ID_SELECT, mode == MODE_SELECT)
        self.toolbar.ToggleTool(ID_WIRE,   mode == MODE_WIRE)
        self.toolbar.ToggleTool(ID_DELETE, mode == MODE_DELETE)
        if mode == MODE_SELECT:
            self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)
        elif mode == MODE_WIRE:
            self.SetStatusText('Mode: Draw Wire — click start, click end  [Esc] cancel', 1)
        elif mode == MODE_DELETE:
            self.SetStatusText('Mode: Delete — click component or wire  [Esc] cancel', 1)

    def _on_select(self, _evt) -> None:
        self._set_mode(MODE_SELECT)

    def _on_wire(self, _evt) -> None:
        self._set_mode(MODE_WIRE)

    def _on_wire_color_choice(self, _evt) -> None:
        idx = self._wire_color_choice.GetSelection()
        # idx 0 = Auto (cycle); idx 1..N = specific color from WIRE_COLORS
        self.canvas.set_wire_color(WIRE_COLORS[idx - 1] if idx > 0 else None)

    def _on_delete(self, _evt) -> None:
        self._set_mode(MODE_DELETE)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key in (ord('W'), ord('w')):
            self._set_mode(MODE_WIRE)
        elif key in (ord('D'), ord('d')):
            self._set_mode(MODE_DELETE)
        elif key == wx.WXK_ESCAPE:
            self._set_mode(MODE_SELECT)
        else:
            evt.Skip()

    def _on_open(self, _evt) -> None:
        with wx.FileDialog(
            self,
            message='Open KiCad netlist',
            wildcard='KiCad netlist (*.net)|*.net|All files (*)|*',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        # Loading a different netlist while the board has content → clear first
        if path != self._netlist_path and (self.board.placements or self.board.wires):
            if wx.MessageBox(
                'Loading a different netlist will clear the current board.\nContinue?',
                'Clear board?',
                wx.YES_NO | wx.ICON_QUESTION, self,
            ) != wx.YES:
                return
            self.board = Breadboard(layout=self.prefs.board_layout)
            self.canvas.reload_board(self.board)
            self.tray.board = self.board
            self.tray.refresh_placed()
            self.canvas.clear_highlights()

        self._project_path = str(Path(path).parent)
        self._netlist_path = path
        self._load_netlist(path)

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
                ['kicad-cli', 'sch', 'export', 'netlist',
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
                self.canvas.Refresh()
                self.SetStatusText(f'Netlist updated. {"; ".join(msgs).capitalize()}.', 0)

    def _on_export(self, _evt) -> None:
        use_svg = self.prefs.export_format == 'svg'
        ext = 'svg' if use_svg else 'png'
        default = f'breadboard.{ext}'
        if self._project_path:
            default = str(Path(self._project_path) / default)
        wildcard = ('SVG image (*.svg)|*.svg' if use_svg
                    else 'PNG image (*.png)|*.png')
        with wx.FileDialog(
            self,
            message='Save breadboard image',
            defaultFile=default,
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
        default = 'breadboard.kicad_bbrd'
        if self._project_path:
            from pathlib import Path as _Path
            default = str(_Path(self._project_path) / 'breadboard.kicad_bbrd')
        with wx.FileDialog(
            self,
            message='Save session',
            defaultFile=default,
            wildcard='Breadboard session (*.kicad_bbrd)|*.kicad_bbrd|All files (*)|*',
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            save_session(self.board, self._netlist_path, path)
            self.SetStatusText(f'Session saved to {path}', 0)
        except Exception as exc:
            wx.MessageBox(f'Failed to save session:\n{exc}', 'Save session',
                          wx.OK | wx.ICON_ERROR, self)

    def _on_load(self, _evt) -> None:
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
        self.canvas.clear_highlights()

        # Sync layout prefs from saved session
        saved_layout = result.get('board_layout', 'full')
        if saved_layout != self.prefs.board_layout:
            self.prefs.board_layout = saved_layout
        self.canvas.layout = CanvasLayout(saved_layout, self.prefs.binding_post_side,
                                          self.prefs.show_branding)
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
                self.board = Breadboard(layout=p.board_layout)
                self.canvas.reload_board(self.board)
                self.tray.board = self.board
                self.tray.refresh_placed()
                self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                                  p.show_branding)
                self.canvas._pan_initialized = False

        # Binding post side or branding (canvas layout only, no data change)
        if p.binding_post_side != old.binding_post_side or p.show_branding != old.show_branding:
            self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                              p.show_branding)
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
        self.canvas.layout = CanvasLayout(p.board_layout, p.binding_post_side,
                                          p.show_branding)
        self.canvas._pan_initialized = False
        self._tray_sizer.Show(self._binding_panel, p.show_binding_posts)
        self._tray_sizer.Show(self._hotkey_line,   p.show_hotkeys)
        self._tray_sizer.Show(self._hotkey_sizer,  p.show_hotkeys)
        self._tray_panel.Layout()

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

        # Versions go backwards through the alphabet (zwieback → zopf → … → aardvark)
        # so a lower string value means a newer release.
        if latest.lower().lstrip('v') >= PLUGIN_VERSION.lower():
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
        self.canvas.clear_highlights()
        self.SetStatusText('Validation markers cleared.', 0)

    def _on_clear(self, _evt) -> None:
        if wx.MessageBox(
            'Clear all placed components and wires?', 'Confirm',
            wx.YES_NO | wx.ICON_QUESTION, self
        ) == wx.YES:
            self.board = Breadboard(layout=self.prefs.board_layout)
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
            self.canvas.clear_highlights()
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
            # Re-export silently if the schematic is newer than the saved netlist,
            # so components added after the last export appear immediately.
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
        self.board.assign_terminal(term_name, net)
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

    # ------------------------------------------------------------------
    # Instrument probe handlers
    # ------------------------------------------------------------------

    def _on_probe_choice(self, probe_name: str, _evt) -> None:
        if self._refreshing_choices:
            return
        ch = self._probe_choices[probe_name]
        sel = ch.GetSelection()
        net = ch.GetString(sel) if sel > 0 else ''
        self.board.assign_probe_net(probe_name, net)
        self.canvas.Refresh()

    def _on_probe_place_btn(self, probe_name: str) -> None:
        if self.board.get_probe_hole(probe_name) is not None:
            # Already placed — remove it
            self.board.remove_probe(probe_name)
            self._refresh_probe_buttons()
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

    def _load_netlist(self, path: str) -> None:
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

        # ---- Instruments ----
        sizer.Add(section('Instruments'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self._cb_instr = wx.CheckBox(self, label='Enable instruments panel')
        self._cb_instr.SetValue(prefs.instruments_enabled)
        sizer.Add(self._cb_instr, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self._cb_auto_gnd = wx.CheckBox(
            self, label='Auto-assign schematic ground to instrument grounds')
        self._cb_auto_gnd.SetValue(prefs.auto_gnd)
        sizer.Add(self._cb_auto_gnd, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        scope_row = wx.BoxSizer(wx.HORIZONTAL)
        scope_row.Add(wx.StaticText(self, label='Oscilloscope channels:'), 0,
                      wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._sc_scope = wx.SpinCtrl(self, min=1, max=4,
                                     initial=prefs.scope_channels, size=(50, -1))
        scope_row.Add(self._sc_scope, 0)
        sizer.Add(scope_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        psu_row = wx.BoxSizer(wx.HORIZONTAL)
        psu_row.Add(wx.StaticText(self, label='PSU channels:'), 0,
                    wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._sc_psu = wx.SpinCtrl(self, min=1, max=3,
                                   initial=prefs.psu_channels, size=(50, -1))
        psu_row.Add(self._sc_psu, 0)
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
        fmt_row = wx.BoxSizer(wx.HORIZONTAL)
        fmt_row.Add(wx.StaticText(self, label='Format:'), 0,
                    wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._rb_png = wx.RadioButton(self, label='PNG', style=wx.RB_GROUP)
        self._rb_svg = wx.RadioButton(self, label='SVG')
        self._rb_png.SetValue(prefs.export_format == 'png')
        self._rb_svg.SetValue(prefs.export_format == 'svg')
        fmt_row.Add(self._rb_png, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        fmt_row.Add(self._rb_svg, 0, wx.ALIGN_CENTRE_VERTICAL)
        sizer.Add(fmt_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # ---- Board ----
        sizer.Add(section('Board'), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        layout_row = wx.BoxSizer(wx.HORIZONTAL)
        layout_row.Add(wx.StaticText(self, label='Size / layout:'), 0,
                       wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._ch_layout = wx.Choice(self, choices=[
            'Mini (170 holes, no rails)',
            'Half (400 holes)', 'Full (830 holes)',
            'Double (2× full, stacked)', 'Triple (3× full + vertical rails)',
            'Double + side rails (2× full, left & right rails)',
        ])
        _layout_map = ['mini', 'half', 'full', 'double', 'triple', 'double_rails']
        self._ch_layout.SetSelection(
            _layout_map.index(prefs.board_layout) if prefs.board_layout in _layout_map else 2)
        layout_row.Add(self._ch_layout, 1, wx.EXPAND)
        sizer.Add(layout_row, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 10)

        post_row = wx.BoxSizer(wx.HORIZONTAL)
        post_row.Add(wx.StaticText(self, label='Binding posts side:'), 0,
                     wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        self._ch_post_side = wx.Choice(self, choices=['Left', 'Right', 'Top', 'Bottom'])
        _side_map = ['left', 'right', 'top', 'bottom']
        self._ch_post_side.SetSelection(
            _side_map.index(prefs.binding_post_side) if prefs.binding_post_side in _side_map else 0)
        post_row.Add(self._ch_post_side, 0)
        sizer.Add(post_row, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

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
            dlg = wx.FileDialog(self, 'Choose branding image',
                                wildcard='Images (*.png;*.jpg;*.bmp;*.svg)|*.png;*.jpg;*.jpeg;*.bmp;*.svg|All files (*.*)|*.*',
                                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
            if dlg.ShowModal() == wx.ID_OK:
                self._tc_brand_img.SetValue(dlg.GetPath())
            dlg.Destroy()
        browse_btn.Bind(wx.EVT_BUTTON, _on_browse)

        self._cb_binding = wx.CheckBox(self, label='Show binding posts on board')
        self._cb_binding.SetValue(prefs.show_binding_posts)
        sizer.Add(self._cb_binding, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

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
        _side_map   = ['left', 'right', 'top', 'bottom']
        return Preferences(
            instruments_enabled=self._cb_instr.IsChecked(),
            auto_gnd=self._cb_auto_gnd.IsChecked(),
            scope_channels=self._sc_scope.GetValue(),
            psu_channels=self._sc_psu.GetValue(),
            show_net_labels=self._cb_labels.IsChecked(),
            show_hotkeys=self._cb_hotkeys.IsChecked(),
            show_binding_posts=self._cb_binding.IsChecked(),
            export_format='svg' if self._rb_svg.GetValue() else 'png',
            board_layout=_layout_map[self._ch_layout.GetSelection()],
            binding_post_side=_side_map[self._ch_post_side.GetSelection()],
            show_baseboard=self._cb_baseboard.IsChecked(),
            baseboard_color=self._cp_base.GetColour().GetAsString(wx.C2S_HTML_SYNTAX),
            show_branding=self._cb_branding.IsChecked(),
            branding_image=self._tc_brand_img.GetValue(),
        )
