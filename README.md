# KiCad Breadboard Builder <img src="images/icon.png" height="45">

A KiCad 9 / 10 Action Plugin for introductory analog electronics courses at the University of Antwerp. Students draw a schematic in Eeschema, then use this plugin to wire up the same circuit on a virtual breadboard — placing components, drawing jumper wires, and validating their work against the schematic.

## What it does

- Renders a breadboard in six configurable sizes: mini (170 holes), half (400), full (830), double (2× full stacked), triple (3× full with vertical power rails), or double rails (2× full with vertical power rails on both sides)
- Parses a KiCad netlist and shows all placeable components in a side tray
- Two-step placement for 2-pin components: click pin 1, then click pin 2
- Single-click placement for DIP ICs and 3-pin components (BJT, POT); DIP bodies show the reference and value (e.g. U1 / RC4558) for quick identification
- TO-92 / SOT-23 transistors (BJT, JFET, MOSFET) show the current pinout order (e.g. C-B-E or G-S-D) on their card; click `>` to cycle variants before placing
- Draw jumper wires between any two holes (tie strip, rail, or binding post)
- Validate the board against the schematic: highlights open nets (?) and shorts (⚡)
- Export the board as a PNG or SVG image
- "Update from schematic" re-exports the netlist via `kicad-cli` without leaving the window
- Save and load board sessions (`.kicad_bbrd`)
- Instrument probes: place function-generator, oscilloscope (1–4 channels), and PSU connection points on the board; drag their labels freely for better visibility
- Preferences dialog (`File → Preferences…`) controls instruments, display, board layout, and export format; settings can be saved as defaults and restored on startup

## Supported components

| Component | Package |
|---|---|
| Resistor (with colour bands) | Axial |
| Capacitor, electrolytic capacitor | Radial |
| Inductor | Axial |
| Diode, Zener diode | Axial (1N4001 style) |
| LED | 5 mm round |
| Potentiometer | 3-pin |
| NPN / PNP BJT | TO-92 |
| N / P-channel JFET | TO-92 |
| N-channel MOSFET (generic) | TO-92 |
| P-channel MOSFET (generic) | TO-92 |
| BS170 MOSFET | TO-92 |
| TL081 (single), RC4558 (dual), TL084 (quad) op-amp | DIP-8 / DIP-14 |
| OPAMP (KiCad Simulation_SPICE) | Logical 5-pin |

### Transistor detection

Components are identified from the KiCad netlist. The plugin recognises transistors in two ways:

- **Generic symbols** (`Device:NPN`, `Device:PMOS`, `Device:NMOS`, etc.) are detected from their symbol name.
- **Specific part-number symbols** (`Transistor_BJT:BC547`, `Transistor_FET:2N7002`, `Transistor_FET:AO3401A`, etc.) are detected from the component description exported by KiCad (e.g. *"NPN Transistor"*, *"N-Channel MOSFET"*).

This means any BJT or MOSFET from the standard KiCad libraries will appear in the component tray automatically.

### Pinout selection for TO-92 transistors

Different physical components that share the same schematic symbol can have a different pin order on the actual package. The tray card for each transistor shows its current pinout (e.g. **C-B-E** or **E-B-C** for a BJT, **G-S-D** or **S-G-D** for a MOSFET). Click **`>`** on the card to cycle through the available variants before placing.

**Always verify the pinout against your component's datasheet.** The default shown is a common convention, but it may not match your specific part:

| Type | Default (plugin) | Example parts that need a different variant |
|---|---|---|
| NPN BJT | C-B-E | 2N3904, 2N2222 → E-B-C |
| PNP BJT | C-B-E | 2N3906 → E-B-C |
| N-ch JFET | S-G-D | 2N5457 → D-G-S |
| N-ch MOSFET | G-S-D | Parts where pin 1 = Drain (e.g. BS108) → D-G-S |
| P-ch MOSFET | G-S-D | Check datasheet for your specific part |
| BS170 | S-G-D | — (fixed, single pinout) |

---

## Installation in KiCad 9 or 10

### Step 1 — Clone the repository

```bash
git clone https://github.com/kerstensrobin/kicad-breadboard.git
```

### Step 2 — Run the install script

**Linux / macOS:**
```bash
cd kicad-breadboard
bash install.sh
```

**Windows:** double-click `install.bat`.

The script detects your KiCad version, creates the plugin link, and tells you what to do next. If something goes wrong, see [Manual installation](#manual-installation) below.

### Step 3 — Refresh plugins in KiCad

1. Open KiCad and open any project in the **PCB Editor** (pcbnew).
2. In the menu: **Tools → External Plugins → Refresh Plugins**.
3. A breadboard icon appears in the right-hand toolbar (or under **Tools → External Plugins → Breadboard Builder**).

> The plugin only appears inside the PCB Editor, not the schematic editor — this is a KiCad limitation for Python plugins.

### Step 4 — Open your project

Click the toolbar button (or menu entry). The plugin will automatically find the netlist (`.net`) in the same folder as the open PCB file.

If you have not exported a netlist yet, use **"Update from schematic"** in the toolbar — this calls `kicad-cli` to export one automatically.

---

## Toolbar buttons

| Button | Action |
|---|---|
| Open netlist | Load a `.net` file manually |
| Update from schematic | Re-export netlist from `.kicad_sch` via `kicad-cli` and reload *(requires KiCad project; not available in standalone mode)* |
| Export image | Save the current board view as a PNG or SVG (format set in Preferences) |
| Validate | Check if the breadboard matches the schematic |
| Clear warnings | Dismiss `?` / `⚡` validation markers |
| Clear board | Remove all placed components and wires |

## Hotkeys

| Key | Action |
|---|---|
| W | Wire mode |
| D | Delete mode |
| R | Rotate / flip component (during placement or when selected) |
| Esc | Back to Select / Move mode |
| Del | Delete selected component or wire |
| Right-click on DIP | Rotate 180° |
| Right-click on binding post | Assign to schematic net |
| Ctrl+O | Open netlist |
| Ctrl+S | Save session |
| Ctrl+L | Load session |
| Scroll | Zoom in / out |
| Shift+Scroll | Pan vertical |
| Ctrl+Scroll | Pan horizontal |
| Middle drag | Pan |
| Ctrl+Home | Fit view |

---

## Preferences

Open **File → Preferences…** to configure the plugin. Settings take effect immediately when you click OK. Use **Save as default** to persist them to `~/.config/kicad_bbrd/prefs.json` and restore them automatically on next launch.

### Instruments

| Setting | Description |
|---|---|
| Enable instruments panel | Show or hide the Function generator / Oscilloscope / PSU section in the side panel |
| Auto-assign schematic ground | Automatically assign net `0` or `GND` to instrument grounds when a netlist is loaded |
| Oscilloscope channels | Number of oscilloscope channel rows shown (1–4) |
| PSU channels | Number of PSU channel pairs shown (1–3) |

### Display

| Setting | Description |
|---|---|
| Show signal labels | Draw net names next to holes on the board |

### Export

| Setting | Description |
|---|---|
| Format | PNG (default) or SVG |

### Board

| Setting | Description |
|---|---|
| Size / layout | `Mini` (170 holes, no rails) · `Half` (400 holes) · `Full` (830 holes) · `Double` (2× full stacked) · `Triple` (3× full + vertical power rails left side) · `Double Rails` (2× full + vertical power rails both sides) |
| Binding posts side | Position of the GND / V1 / V2 binding posts: `Left` (default), `Right`, `Top`, or `Bottom` |
| Show baseboard | Draw a coloured panel behind the breadboard(s) |
| Baseboard colour | Fill colour of the baseboard |
| Include branding | Display a logo image alongside the binding posts (on the outer side, away from the board) |
| Branding image | Path to a custom PNG/SVG/JPG image; leave blank to use the built-in default |
| Show binding posts on board | Toggle the circular binding-post terminals on the canvas |

---

## Side panel

### Binding posts

Three binding posts (GND, V1, V2) on the board can be assigned to schematic nets via the dropdowns. GND is automatically assigned to net `0` (SPICE-style) or `GND` when a netlist is loaded. The validator treats an assigned binding post as an electrical endpoint on that net.

### Instruments

The **Function generator**, **Oscilloscope**, and **Power supply (PSU)** sections let you place optional probe markers on any hole. Each probe can be assigned to a schematic net independently of the binding posts. The instruments panel and the number of channels shown are configurable in **Preferences**.

| Probe | Instrument |
|---|---|
| FG+ | Function generator signal |
| FG⏚ | Function generator ground |
| CH1–CH4 | Oscilloscope channels (1–4 shown, set in Preferences) |
| SC⏚ | Oscilloscope ground |
| PSU1+ / PSU1− | PSU channel 1 positive / negative |
| PSU2+ / PSU2− | PSU channel 2 positive / negative |
| PSU3+ / PSU3− | PSU channel 3 positive / negative |

- Click **Place** to enter placement mode, then click any hole on the board.
- Click **Remove** (same button once placed) to remove the probe.
- In **Delete mode** (D), hover over a probe flag and click to remove it.
- In **Select mode**, drag a probe flag to reposition the label. A leaderline connects the label back to its hole. The label position is saved in the session file.

---

## Example workflow

- Draw a schematic.

![schematic](images/schematic.png)

- Go to the PCB editor (using the green button on the toolbar, or by using Tools → Switch to PCB editor)
At the top, a new breadboard icon appeared (in the toolbar, next to the CLI input icon). Clicking this will take you to the Breadboard Builder.

![icon](images/pcbeditor.png)

- The Breadboard Builder will open!

![breadboard](images/breadboard.png)

- Here, you can select which component you want to place and click "Validate" to check if your build contains errors. If it does, it will indicate missing connections and short circuits on the relevant pins as illustrated below.

![shortcircuit](images/shortcircuit.png)

That's it! Have fun!

> **Help menu:** use **Help → Check for updates…** to compare your installed version against the latest release on GitHub, or **Help → Report issue…** to open a pre-filled GitHub issue with your system information attached.

---

## Manual installation

If the install script doesn't work, you can link or copy the plugin folder manually.

The scripting plugin directory depends on your KiCad version and OS:

| Platform | KiCad 9 | KiCad 10 |
|---|---|---|
| Linux | `~/.local/share/kicad/9.0/scripting/plugins/` | `~/.config/kicad/10.0/scripting/plugins/` |
| macOS | `~/Library/Preferences/kicad/9.0/scripting/plugins/` | `~/Library/Preferences/kicad/10.0/scripting/plugins/` |
| Windows | `%APPDATA%\kicad\9.0\scripting\plugins\` | `%APPDATA%\kicad\10.0\scripting\plugins\` |

> If you are unsure of the exact path, open KiCad and go to **Preferences → Configure Paths…**.

**Linux / macOS:**
```bash
# KiCad 9
ln -s /path/to/kicad-breadboard/plugins/breadboard \
      ~/.local/share/kicad/9.0/scripting/plugins/breadboard

# KiCad 10
ln -s /path/to/kicad-breadboard/plugins/breadboard \
      ~/.config/kicad/10.0/scripting/plugins/breadboard
```

**Windows** (PowerShell, adjust version number):
```powershell
New-Item -ItemType Junction `
  -Path  "$env:APPDATA\kicad\10.0\scripting\plugins\breadboard" `
  -Target "C:\path\to\kicad-breadboard\plugins\breadboard"
```

Or simply **copy** the `plugins/breadboard/` folder into the scripting plugins directory.

---

## Standalone mode (development / no KiCad needed)

> Standalone mode is intended for UI development only. For the full workflow use the plugin inside KiCad as described above.

```bash
pip install wxPython
cd /path/to/kicad-breadboard
python -m plugins.breadboard.standalone path/to/circuit.net
```

---

Made with ♥ by [nacho.works](https://nacho.works) and [University of Antwerp](https://www.uantwerpen.be/en/), Belgium.
