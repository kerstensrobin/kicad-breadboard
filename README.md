
# KiCad Breadboard Builder <img src="images/icon.png" height="45">

A KiCad 9 / 10 plugin for introductory analog electronics courses at the University of Antwerp. Draw your schematic in Eeschema, then wire it up on a virtual breadboard and validate it against the schematic.

<img width="1784" height="953" alt="Screenshot from 2026-04-27 16-59-01" src="https://github.com/user-attachments/assets/c13cd047-ecd1-4b14-8953-b5d5b911a5ad" />
 
 
**In the press:**
- [Hackaday — *This KiCad Plugin Enables Breadboarding*](https://hackaday.com/2026/04/23/this-kicad-plugin-enables-breadboarding/)
- [Adafruit Blog — *KiCad Breadboard Builder*](https://blog.adafruit.com/2026/04/24/kicad-breadboard-builder/)

---

## Installation

> Built on [CachyOS](https://cachyos.org) — tested on CachyOS, Ubuntu, and Windows.

**1. Clone the repository**
```bash
git clone https://github.com/kerstensrobin/kicad-breadboard.git
```

**2. Run the install script**

Linux / macOS:
```bash
cd kicad-breadboard
bash install.sh
```
Windows: double-click `install.bat`.

The script detects your KiCad version and creates the plugin link automatically. If something goes wrong, see [Manual installation](#manual-installation) below.

**3. Refresh plugins in KiCad**

Open any project in the **PCB Editor** → **Tools → External Plugins → Refresh Plugins**. A breadboard icon appears in the right-hand toolbar.

> The plugin only appears inside the PCB Editor, not the schematic editor — this is a KiCad limitation.

**4. Open your project**

Click the toolbar button. The plugin finds the netlist (`.net`) automatically. If you have not exported one yet, use **"Update from schematic"** in the toolbar — this calls `kicad-cli` to export it without leaving the window.

---

## Workflow

Draw your schematic in Eeschema:

![schematic](images/schematic.png)

Switch to the PCB Editor — a breadboard icon appears in the toolbar:

![icon](images/pcbeditor.png)

Place components and draw jumper wires on the virtual breadboard:

![breadboard](images/breadboard.png)

Click **Validate** to check your build against the schematic. Open nets and short circuits are highlighted:

![shortcircuit](images/shortcircuit.png)

---

## Features

- Renders a breadboard in six configurable sizes: mini (170 holes), half (400), full (830), double (2× full stacked), triple (3× full with vertical power rails), or double rails (2× full with vertical power rails on both sides)
- Parses a KiCad netlist and shows all placeable components in a side tray — **any U-prefix IC with an even pin count is supported automatically**, even if it is not in the built-in list (555 timers, 74xx logic gates, CD4xxx, counters, shift registers, …)
- Two-step placement for 2-pin components: click pin 1, then click pin 2; diagonal placement and power-rail connections are preserved when the component is moved later
- Single-click placement for DIP ICs and 3-pin components (BJT, POT); DIP bodies show the reference and value (e.g. U1 / RC4558) for quick identification
- **Pin functions toggle** (toolbar): shows the KiCad pin function name (e.g. TRIG, THRESH, GND) on every placed DIP IC instead of pin numbers; short labels stay vertical, long labels angle automatically to avoid overlap
- TO-92 transistors (BJT, JFET, MOSFET) show the current pinout order (e.g. C-B-E or G-S-D) on their card; click `>` to cycle variants before placing
- Film capacitors render as flat rectangles with the value printed on the body (e.g. C5 100nF); electrolytic capacitors render as top-down circles with a polarity stripe
- Draw jumper wires between any two holes (tie strip, rail, or binding post)
- Validate the board against the schematic: highlights open nets (?) and shorts (⚡)
- Export the board as a PNG or SVG image
- "Update from schematic" re-exports the netlist via `kicad-cli` without leaving the window
- Save and load board sessions (`.kicad_bbrd`)
- Instrument probes: place function-generator, oscilloscope (1–4 channels), and PSU connection points on the board; drag their labels freely for better visibility
- Preferences dialog (`File → Preferences…`) controls instruments, display, board layout, and export format; settings can be saved as defaults and restored on startup

---

## Supported components

If your schematic uses the standard KiCad libraries, the plugin picks up your components automatically — no manual configuration needed.

**Passives & discretes** — resistors (with colour bands), capacitors (film and electrolytic), inductors, diodes, Zener diodes, LEDs, and potentiometers from the `Device:` library are all recognised.

**Transistors** — every BJT, JFET, and MOSFET in the standard `Device:`, `Transistor_BJT:`, and `Transistor_FET:` libraries is supported, whether you use a generic symbol (`Device:NPN`) or a specific part number (`Transistor_BJT:BC547`). Detection is based on the symbol name and the component description KiCad exports, so any part the library describes as *"NPN Transistor"* or *"N-Channel MOSFET"* will appear in the tray automatically.

**ICs** — any U-prefix component with an even pin count is placed as a DIP IC. The following op-amps additionally show named pin labels: TL081 (DIP-8), RC4558 (DIP-8), TL084 (DIP-14), and OPAMP / KiCad Simulation_SPICE (DIP-6, labelled "SIM").

**Modules** — Arduino Nano (+ Every, ESP32, RP2040 Connect, …), Arduino Uno R3, and Raspberry Pi (40-pin GPIO header).

<details>
<summary>Pinout selection for TO-92 transistors</summary>

The tray card for each transistor shows its current pinout (e.g. **C-B-E** or **E-B-C** for a BJT, **G-S-D** or **S-G-D** for a MOSFET). Click **`>`** to cycle through variants before placing.

**Always verify the pinout against your component's datasheet.**

| Type | Default (plugin) | Example parts that need a different variant |
|---|---|---|
| NPN BJT | C-B-E | 2N3904, 2N2222 → E-B-C |
| PNP BJT | C-B-E | 2N3906 → E-B-C |
| N-ch JFET | S-G-D | 2N5457 → D-G-S |
| N-ch MOSFET | G-S-D | Parts where pin 1 = Drain (e.g. BS108) → D-G-S |
| P-ch MOSFET | G-S-D | Check datasheet for your specific part |
| BS170 | S-G-D | — (fixed, single pinout) |

</details>

---

## Reference

<details>
<summary>Toolbar buttons</summary>

| Button | Action |
|---|---|
| Open netlist | Load a `.net` file manually |
| Update from schematic | Re-export netlist from `.kicad_sch` via `kicad-cli` and reload *(requires KiCad project; not available in standalone mode)* |
| Export image | Save the current board view as a PNG or SVG (format set in Preferences) |
| Validate | Check if the breadboard matches the schematic |
| Clear warnings | Dismiss `?` / `⚡` validation markers |
| Clear board | Remove all placed components and wires |

</details>

<details>
<summary>Hotkeys</summary>

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

</details>

<details>
<summary>Preferences</summary>

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
| Show hotkey reference | Show or hide the hotkey reference panel at the bottom of the right side panel |

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

</details>

<details>
<summary>Side panel — binding posts & instruments</summary>

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

</details>

<details>
<summary>Manual installation</summary>

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

</details>

<details>
<summary>Standalone mode</summary>

> Standalone mode is intended for UI development only. For the full workflow use the plugin inside KiCad as described above.

```bash
pip install wxPython
cd /path/to/kicad-breadboard
python -m plugins.breadboard.standalone path/to/circuit.net
```

</details>

---

## Troubleshooting

<details>
<summary>macOS: "Update from schematic" says kicad-cli not found</summary>

When KiCad is launched from Finder, the Dock, or Spotlight, macOS does not pass your shell `PATH` to the application. The plugin handles this automatically by falling back to the standard KiCad install location (`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`).

If you installed KiCad somewhere other than `/Applications` the fallback will not find it either. In that case, launch KiCad from Terminal instead:

```bash
open -a KiCad
```

This passes your shell environment (including `PATH`) to KiCad and the plugin will find `kicad-cli` normally.

</details>

---

Made with ♥ by [nacho.works](https://nacho.works) and [University of Antwerp](https://www.uantwerpen.be/en/), Belgium.
