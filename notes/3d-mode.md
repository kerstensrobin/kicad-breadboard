# 3D Mode — Design Notes

> Dev note, OVEN branch. Not yet implemented.

## Goal

A toggleable 3D view of the breadboard — view-only, rotatable — so students can
inspect their wiring from multiple angles. The 2D canvas stays as-is for placement
and editing.

## Rendering

- **Engine**: `wx.glcanvas.GLCanvas` + PyOpenGL. Embeds naturally in the existing
  wx.Frame as a second panel swapped in by a toolbar toggle button.
- **Shading**: Basic Phong shading is enough. No PBR needed.
- **Camera**: Arcball rotation on drag, scroll to zoom, middle-drag to pan.

## Component geometry

Two options were discussed:

### A — Procedural geometry (fast to ship, zero extra deps at runtime)

Build simple but recognisable meshes per `type_id` directly in code:

| Component | Shape |
|---|---|
| Resistor | Cylinder body + two bent wire leads |
| Capacitor (film) | Flat box |
| Capacitor (electrolytic) | Short cylinder, flat top with polarity stripe |
| LED | Dome + cylinder |
| Diode / Zener | Cylinder + cathode stripe ring |
| DIP IC | Rectangular box + two rows of L-shaped pins |
| TO-92 (BJT/FET) | D-shaped extrusion + three bent leads |
| Breadboard base | Slab + alternating-colour plastic strips + metal rail strips |

### B — Pre-bundled KiCad STEP meshes (preferred long term)

KiCad's library already has STEP files for TO-92, DIP-8/14, axial components, etc.
The plan is to do the heavy lifting **once at dev time**, not at user runtime:

1. Pick one canonical STEP file per component type from KiCad's library.
2. Run a dev-time conversion script (using `cadquery` or `pythonOCC`) that
   triangulates each file and writes a small JSON vertex/face list.
3. Bundle the resulting ~15–20 JSON mesh files in the repo (few hundred KB total).
4. At runtime, load JSON → upload to OpenGL buffers. **No heavy deps for the user**
   beyond PyOpenGL.

Each bundled mesh needs a calibration entry: origin offset + rotation + scale factor
(STEP is in mm; we need pixel/board-unit space). Record these alongside the mesh files.

Option B is preferred because it gives real KiCad geometry and stays dependency-light
at runtime. Start with Option A to prove the camera/interaction model, then swap in
real meshes per component type.

## Architecture

```
window.py
  toolbar: add "3D View" toggle (wx.ITEM_CHECK)
  content area: swap BreadboardCanvas ↔ BreadboardCanvas3D

canvas3d.py  (new file)
  class BreadboardCanvas3D(wx.glcanvas.GLCanvas)
    reads self.board + self.netlist — no model changes
    draw_breadboard_3d()
    draw_component_3d(placed, comp_def)   ← dispatch on type_id
    draw_wire_3d(wire)
    draw_validation_markers_3d()          ← floating ? / ⚡ sprites
    _on_mouse_drag()   → arcball rotation
    _on_scroll()       → zoom
    _on_middle_drag()  → pan
```

Placement and wiring remain in the 2D view only. The 3D panel is view-only.

## What is NOT in scope (at least for v1)

- Clicking to place components in 3D
- Shadows / reflections
- Export to 3D formats (glTF, OBJ, …)
- Loading `.wrl` files at runtime (path/version fragility, needs VRML parser)
- Loading `.step` files at runtime (needs pythonOCC — too heavy for users)
