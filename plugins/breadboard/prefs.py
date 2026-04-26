"""
User preferences for the Breadboard Builder.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass


def _prefs_path() -> str:
    if sys.platform == 'win32':
        base = os.getenv('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.getenv('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'kicad_bbrd', 'prefs.json')


_PREFS_PATH = _prefs_path()


@dataclass
class Preferences:
    instruments_enabled: bool = True   # show/hide instruments panel section
    auto_gnd: bool = True              # auto-assign schematic GND to probe grounds on netlist load
    scope_channels: int = 2            # number of oscilloscope channels shown (1–4)
    psu_channels: int = 3              # number of PSU channels shown (1, 2, or 3)
    show_net_labels: bool = True       # show signal net labels on the board
    show_binding_posts: bool = True    # draw binding-post terminals on the board
    export_format: str = 'png'         # 'png' or 'svg'
    board_layout: str = 'full'         # 'mini', 'half', 'full', 'double', 'triple'
    binding_post_side: str = 'left'    # 'left', 'right', 'top', 'bottom'
    show_baseboard: bool = True        # draw a baseboard behind the breadboard(s)
    baseboard_color: str = '#3d6fa8'   # baseboard fill colour
    show_branding: bool = False        # draw branding image on the baseboard
    branding_image: str = ''           # path to custom branding image (empty = built-in default)
    show_hotkeys: bool = True          # show hotkey reference panel in the side tray
    load_on_startup: bool = False      # restore these saved settings on next launch


def save_prefs(prefs: Preferences) -> None:
    """Persist preferences to disk.  Silently ignores I/O errors."""
    try:
        os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
        with open(_PREFS_PATH, 'w') as f:
            json.dump(dataclasses.asdict(prefs), f, indent=2)
    except Exception:
        pass


def load_prefs() -> Preferences:
    """Load preferences from disk.
    Returns defaults if the file is missing, unreadable, or load_on_startup is False."""
    try:
        with open(_PREFS_PATH) as f:
            data = json.load(f)
        if not data.get('load_on_startup', False):
            return Preferences()
        known = set(Preferences.__dataclass_fields__)
        return Preferences(**{k: v for k, v in data.items() if k in known})
    except Exception:
        return Preferences()
