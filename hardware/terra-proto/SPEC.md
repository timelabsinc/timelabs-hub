# Terra-style GL32 case prototype — engineering spec

Status: v0.1 design intent, derived from published DWC Terra figures (32 × 44 mm, lug-less
titanium slab, screw-in 316L back, angled through-slots for a ≤22 mm single-pass strap) and
the Miyota GL32 catalogue values. Everything marked **UNVERIFIED** must be measured on the real
parts before titanium is cut. `params.py` is the single source of truth; this file explains it.
No DWC / Terra marks or numbering are modelled anywhere.

## 1. Coordinate system and build profiles

* Origin at the case centre **on the back face plane**. +X → 3 o'clock (crown), +Y → 12 o'clock,
  +Z → front (dial/crystal). Back face z = 0, front face z = `H` = 8.3.
* Every part module returns its solid **already placed in these assembly coordinates**.
* Profiles (`params.get("titanium")`, `params.get("resin")`): same geometry; the resin profile
  opens fits (`hole_comp`, `fit_extra`), prints an undersize tube hole to be reamed, and models
  the caseback thread as a true helix at M27.6 × 1.0 (titanium: M27 × 0.5, cosmetic bores only).

## 2. Parts and module contract

| module | PART name | material (Ti build / resin build) | what it is |
|---|---|---|---|
| `parts/case_body.py` | `case_body` | Ti Grade 2 / ABS-like resin | the 32 × 44 × 8.3 slab |
| `parts/caseback.py` | `caseback` | 316L / resin | screw-in back, flange protrudes 0.8 |
| `parts/spacer_ring.py` | `spacer_ring` | POM (machined) / resin | movement holder + rehaut |
| `parts/dial_blank.py` | `dial_blank` | brass 0.4 / resin | flat dial disc |
| `parts/purchased.py` | `crystal`, `iring`, `oring`, `tube`, `crown`, `movement`, `hands_envelope`, `battery` | bought parts | placeholder solids for interference checks and the assembly STEP |

Every part module exposes exactly:

```python
PART = "case_body"                          # file stem used by build.py
MATERIAL = {"titanium": "Ti Grade 2", "resin": "ABS-like resin"}
def build(p: "Params") -> cq.Workplane:     # ONE valid solid, in assembly coordinates
def bom(p: "Params") -> list[dict]:         # [{"item":..., "spec":..., "qty":..., "source":...}] (may be [])
```
`parts/purchased.py` instead exposes `PARTS = {"crystal": build_crystal, ...}` with the same
`build_x(p) -> cq.Workplane` signature and `bom(p)`.

## 3. Case body (`case_body`) — feature recipe

Build in this order (each step names the parameters it uses):

1. **Slab**: box `W × L × H`, centred in X/Y, z from 0 to `H`.
2. **Corner chamfers**: the four vertical corner edges chamfered by `corner_chamfer` (45°, leg 4.0).
3. **Crystal bore**: from the front face down `crystal_engagement` (1.3), Ø `crystal_bore_d` (27.7),
   with a `crystal_bore_lead_chamfer` (0.2) at the front edge. Floor of this bore = crystal seat
   at `z_crystal_seat` (7.0). The Hytrel I-ring sits here.
4. **Dial aperture**: through the ledge, Ø `dial_aperture_d` (25.2), from `z_crystal_seat` down to
   `z_ledge_bottom` (5.5). Ledge is therefore `ledge_t` (1.5) thick.
5. **Movement bore**: Ø `mvt_bore_d` (26.5) from `z_ledge_bottom` down to `z_cb_inner` (1.5).
6. **Caseback thread zone**: z 0 → `cb_thread_len` (1.5). Titanium: `threads.cut_internal_cosmetic`
   (plain bore at the minor Ø, lead chamfer 0.2). Resin: `threads.cut_internal(...)` with
   `cb_thread_clearance`. Requirement: thread minor Ø ≥ `mvt_bore_d` − 0.05 so the ring inserts
   from the back (params guarantee it; verify.py asserts it).
7. **Ring keyway**: at 9 o'clock (−X) in the movement bore wall: width `ring_key_w`+0.1, radial
   depth `ring_key_h`+0.1, from `z_cb_inner` to `z_cb_inner + ring_key_len + 0.2`.
8. **Strap channels** (both ends, mirror in Y): in the YZ section a straight band of perpendicular
   thickness `strap_gap` whose axis crosses z = 0 at y = `y_strap_axis_back` and climbs toward the
   end face at `strap_angle_deg` above the back plane. Extrude it over x ∈ ±`strap_slot_w`/2 and
   extend it well past both faces so it cuts cleanly. Results (titanium defaults): back-face opening
   y 16.2 → 19.0, end-face opening z 2.52 → 4.87, bar 3.0 wide on the back face and 2.52 tall on the
   end face, 3.43 of solid above the exit. Round the four channel edges (`strap_fillet` 0.4) and the
   bar's outer edge, i.e. the back-face/end-face edge, over the full width (`strap_bar_round` 0.8).
9. **Crown pocket**: rectangular notch in the +X flank, y ∈ ±`crown_pocket_w`/2, from
   x = `x_pocket_floor` (15.2) outward, full height, vertical corner radii `crown_pocket_corner_r`.
10. **Tube hole**: Ø `tube_hole_d` along X, axis at (y = 0, z = `z_stem`), from inside the movement
    bore out through the pocket floor.
11. **Gate details** for each `(sx, sy)` in `gate_corners` (defaults: 1 o'clock and 7 o'clock):
    * front slot (`front_slot`): stadium `front_slot_len × front_slot_w`, blind `front_slot_depth`
      from the front face, centred at `Params.front_slot_center(sx, sy)`, long axis at
      `front_slot_angle_deg` from +X (same angle for both corners: the pattern is 180° symmetric).
    * chamfer notch (`chamfer_notch`): box centred on `Params.chamfer_mid(sx, sy)`, `chamfer_notch_w`
      along the chamfer, `chamfer_notch_depth` inward normal to the chamfer face, height
      `chamfer_notch_h` from the back face.
    * back groove (`back_groove`): stadium on the back face, width `back_groove_w`, depth
      `back_groove_depth`, running parallel to Y at x = sx·(W/2 − `back_groove_inboard`), from
      y = sy·(L/2 − corner_chamfer − 0.5) toward the centre for `back_groove_len`.
12. **Edge breaks**: front outline `edge_chamfer_front` (0.4); back outline `edge_chamfer_back`
    (0.3) except the two end edges which already carry the `strap_bar_round`.
    If CadQuery's fillet/chamfer selection fails on a complex edge, skip that edge break, log it,
    and keep the solid valid — a missing 0.3 chamfer is not a blocker, an invalid solid is.

Sanity values (titanium): wall at crystal bore on the crown side 2.15 → 1.35 inside the pocket;
wall at the thread on the crown side 2.5 → 1.7 inside the pocket; tube engagement 1.95;
tube-hole bottom sits 0.18 above the thread zone (`tube_to_thread_margin`) — small, driven by the
UNVERIFIED `stem_below_dial_seat`.

## 4. Caseback (`caseback`)

* Flange disc Ø `cb_flange_d` (30.0) × `cb_flange_t` (0.8), from z = −0.8 to 0 (outside the case).
* Threaded boss Ø `cb_thread_major` × `cb_thread_len` from z = 0 to 1.5, inner face flat at
  z = `z_cb_inner`. Titanium: `threads.boss_cosmetic` (plain cylinder at major Ø); resin:
  `threads.boss(..., clearance=cb_thread_clearance)`.
* Face-seal O-ring groove on the flange's inner face (z = 0 plane): mean Ø `oring_groove_mean_d`
  (28.6), width `oring_groove_w` (0.7), depth `oring_groove_depth` (0.35) cut downward into the
  flange. O-ring CS 0.5. A 0.5 mm flat gasket 28 × 29.5 is the pragmatic alternative.
* `cb_tool_holes` (6) × Ø `cb_tool_hole_d` blind holes `cb_tool_hole_depth` deep from the outer
  face on PCD `cb_tool_pcd`, one at 12 o'clock; plus the optional centre dimple. Opens with a pin
  spanner / 2-pin caseback wrench.
* Outer face edge chamfer 0.3. Engraving area noted in the drawing only (no text modelled).

## 5. Spacer ring (`spacer_ring`)

* Cylinder OD `ring_od`, from z = `z_cb_inner` to `z_ledge_bottom` (height `ring_h` = 4.0).
* Movement pocket from the bottom up `ring_step_h` (2.38): the GL32 outline = circle Ø
  `ring_pocket_l` ∩ strip of width `ring_pocket_w` (flats at ±`ring_pocket_w`/2 in X), open at
  the bottom (movement rests on the caseback).
* Above the step: dial recess Ø `ring_dial_recess_d` (26.2) up to the top → the visible rehaut.
* Stem slot at +X: width `ring_stem_slot_w`, through the wall, from the bottom up to
  `z_stem + 1.2` (open toward the back so the movement drops in with its stem).
* Anti-rotation key at −X on the OD: `ring_key_w` × `ring_key_h` × `ring_key_len` from the bottom.
* Battery access: none needed (movement lifts out).

## 6. Dial blank, purchased placeholders

* Dial: Ø `dial_d` × `dial_t`, centre hole `dial_center_hole_d`, feet holes per `dial_feet`
  (**UNVERIFIED** positions), placed z = `z_dial_seat` → `z_dial_face`.
* Crystal: Ø `crystal_d` × `crystal_t` at z = `z_crystal_seat` → `z_crystal_top`.
* I-ring: tube Ø_i = crystal_d, wall `iring_wall`, height `iring_h`, sitting on the crystal seat.
* Movement envelope: the GL32 outline extruded `mvt_h` from `z_mvt_back`, plus a Ø 3 × `mvt_pipe_h`
  centre pipe stub; stem: Ø 0.9 rod from the movement's +X edge to x = `x_crown_start`.
* Hands envelope: Ø 20 × `hands_top_above_dial_face` on the dial face (must not touch the crystal).
* Tube: Ø `tube_od` × `tube_len` from x = `x_bore_wall` outward at (0, `z_stem`).
* Crown: octagonal prism (across-flats `crown_d`) × `crown_len` from x = `x_crown_start`.
* Battery: Ø 6.8 × 2.15 inside the movement envelope (cosmetic).
* O-ring: torus in the caseback groove.

## 7. Stack-up (titanium defaults)

| z (mm) | what |
|---|---|
| −0.8 | caseback outer face |
| 0.0 | case back face; thread zone starts |
| 1.5 | thread zone ends = caseback inner face |
| 1.6 | movement back (0.1 clearance) |
| 2.68 | stem axis (UNVERIFIED offset 1.20 below dial seat) |
| 3.88 | dial seat (movement top), ring step |
| 4.28 | dial face |
| 5.5 | ledge underside, ring top |
| 7.0 | crystal seat (ledge top); hands clearance 2.72 |
| 8.3 | front face |
| 9.0 | crystal top (0.7 proud) |

## 8. Verification (`verify.py`) — required checks

1. Both profiles build; every part is exactly one valid solid; STL export watertight (trimesh).
2. Parametric assertions: `hands_clearance ≥ hands_clearance_min`; `tube_to_thread_margin ≥ 0.1`;
   `cb_thread_minor ≥ ring_od + 0.1`; `strap_front_wall ≥ 2.0`; `strap_bar_end_h ≥ 1.5`;
   `end_face_flat_half_w ≥ strap_slot_w/2 + 0.3`; crown-side wall at the crystal bore ≥ 1.0 inside
   the pocket; ring pocket wall ≥ 2.5; tube engagement ≥ 1.5.
3. Assembly interference: pairwise intersection volumes < 0.01 mm³ for case/caseback, case/ring,
   case/movement, case/crystal, case/tube, case/crown, ring/movement, ring/dial, caseback/movement,
   caseback/tube, crystal/hands envelope, crystal/iring (iring is inside the bore, outside the crystal).
4. Thread screw-in test (resin profile): boss rotated θ about Z and translated θ/360·pitch for
   θ ∈ {0, 120, 240} → zero interference with the case.
5. Probe points: a point inside each strap channel, each front slot, the crown pocket and the tube
   hole is NOT inside the case solid; a point in each wall that must be solid IS inside.
6. Mass estimates from volumes and `density_*`.
7. Writes `out/<profile>/verify_report.md` and exits non-zero on any failure.

## 9. Drawings (`drawings.py`) — required output

`out/drawings/blueprints.pdf` (A4 landscape, one part per sheet, plus an assembly section sheet)
and the same as SVG. Views: case body front, back, section A-A (YZ through x = 0: strap channels,
ledge, bore, thread), section B-B (XZ through y = 0: crown pocket, tube, stack-up); caseback plan
and section; ring plan and section; assembly section with the stack-up table. Every sheet: title
block (part, material, profile, scale, sheet n/N, date from `args`), general tolerance ±0.05 (Ti)
/ ±0.15 (resin), thread callouts (`M27 × 0.5 − 6H`, lead chamfer), surface finish (Ti: sandblasted
Ra 1.6 after machining; press-fit bores Ra 0.8), and every dimension in `params.py` that a
machinist needs. Draw the outlines from true CadQuery sections (not from hand-drawn primitives) and
overlay the dimensions with matplotlib; label UNVERIFIED values with an asterisk and a footnote.

## 10. Exports (`build.py`)

`python build.py --profile resin|titanium|all --out out` writes
`out/<profile>/<PART>.stl` and `.step` for every part, `out/<profile>/assembly.step`
(cq.Assembly, purchased placeholders included and colour-coded), `out/<profile>/params.json`
(from `Params.as_dict()`), and prints the volumes. Must run from this directory with the
repo's system python (CadQuery 2.8, trimesh, numpy, matplotlib installed).

## 11. Known unknowns (measure before cutting titanium)

* `stem_below_dial_seat` (1.20) — sets `z_stem`; tolerance stack against the thread zone is 0.18.
* GL32 outline length 18.2 vs 18.5 — pocket uses 18.5 + 0.15/side; a 18.2 movement will have
  0.3 play in Y; add a shim or tighten `mvt_l` after measuring.
* Hand pipe heights / seconds-hand top (`hands_top_above_dial_face` 1.8).
* Dial feet positions and diameter.
* Terra's own slot angle, bar size, corner "gate" cuts and crown recess — reconstructed from photos.
* Crystal stock: confirm a 27.0 × 2.0 flat sapphire and a 0.45 × 0.9 Hytrel I-ring are purchasable;
  otherwise change `crystal_d` and rebuild.
