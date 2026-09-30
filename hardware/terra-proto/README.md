# Terra-style GL32 case prototype

## 1. What this is

A lug-less 32 × 44 × 8.3 mm rectangular watch case in the style of the DWC Terra, drawn
from scratch around the Miyota GL32 quartz movement: screw-in 316L back, flat sapphire
crystal held by an I-ring, machined POM movement ring, generic 4.0 mm crown on a 2.0 mm
tube, and a 22 mm single-pass strap that runs through angled channels in the case ends
instead of lugs and springbars.

Two build profiles share one geometry:

| profile | purpose | what differs |
|---|---|---|
| `titanium` | the real part: CNC Ti Grade 2 case, 316L back, machined POM ring | threads exported as plain bores at the minor / major diameter, the drawing carries the M27 × 0.5 callout; tube hole 2.0 H7; fits as designed |
| `resin` | MSLA fit-check print before any metal is cut | true helical thread at M27.6 × 1.0 with 0.15 clearance; small bores +0.10, sliding fits +0.10; tube hole printed 1.9 and reamed to 2.0 |

Status: v0.1 design intent. Nothing has been printed or machined yet. Values marked
UNVERIFIED in `SPEC.md` / `params.py` are catalogue figures or photo estimates; section 8
lists what to measure before titanium is ordered. The finished case will be titanium; the
resin profile exists to prove the fits first.

## 2. Folder layout and how to run

```
hardware/terra-proto/
  SPEC.md            engineering spec: coordinates, feature recipe per part, checks, drawings
  params.py          single source of truth for every dimension; profiles titanium / resin
  build.py           builds every part; writes STL, STEP, assembly, params.json, bom.csv
  verify.py          geometry checks, interference, thread screw-in test, masses (SPEC s.8)
  drawings.py        blueprints.pdf + SVG, one sheet per part plus assembly section (SPEC s.9)
  requirements.txt   cadquery>=2.4, trimesh, numpy, matplotlib, pillow
  parts/
    threads.py       helical thread helper (resin) and cosmetic bores (titanium)
    case_body.py     the 32 x 44 x 8.3 slab
    caseback.py      screw-in back, O-ring groove, spanner holes
    spacer_ring.py   POM movement holder / rehaut
    dial_blank.py    flat dial disc with feet holes
    purchased.py     placeholder solids for bought parts + the purchasing BOM
  out/               generated, not committed
```

Install and run (Python 3.11; CadQuery installs with pip; no display is needed):

```
cd hardware/terra-proto
pip install -r requirements.txt
python3 params.py titanium        # print the derived stack-up numbers (titanium | resin)
python3 build.py --profile all    # or --profile titanium | resin; --parts case_body ... for a subset
python3 verify.py                 # every check from SPEC section 8; exits non-zero on failure
python3 drawings.py               # blueprints for the machinist
python3 parts/case_body.py        # every part module also runs its own self-test
```

Outputs:

| path | contents |
|---|---|
| `out/titanium/`, `out/resin/` | `<part>.stl` and `<part>.step` for `case_body`, `caseback`, `spacer_ring`, `dial_blank` and each purchased placeholder |
| `out/<profile>/assembly.step` | all solids in assembly position, colour-coded (grey case, steel back, black ring, translucent crystal, orange bought parts) |
| `out/<profile>/params.json` | every parameter and derived value of that build |
| `out/<profile>/bom.csv` | the `bom()` rows of all modules in one CSV |
| `out/<profile>/verify_report.md` | `verify.py` results |
| `out/drawings/blueprints.pdf`, `*.svg` | A4 landscape sheets, title block, tolerances, thread callouts |

Warnings on stderr matter. With the v0.1 parameters `build.py` reports the spacer-ring
lip fallback and the skipped front chamfer at the crown pocket; both are explained in
section 3. If `verify.py` or `drawings.py` is missing from the folder it has not been
written yet; `SPEC.md` sections 8 and 9 define what each must do.

Coordinates used everywhere: origin at the case centre on the back face; +X toward
3 o'clock (crown), +Y toward 12, +Z toward the crystal. Back face z = 0, front face z = 8.3.

## 3. Design summary

Stack-up, titanium defaults (`SPEC.md` section 7):

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

Key decisions:

| decision | why |
|---|---|
| Flat sapphire Ø 27.0 × 2.0 in a Ø 27.7 bore with a 0.45 × 0.9 Hytrel I-ring | Stock crystal size (0.1 mm steps). No glue; the ring crushes 0.2 on diameter (bore = crystal + 0.7, the I-ring supplier rule). Crystal sits 1.3 deep and stands 0.7 proud like the reference, and the front face stays flat, so it is milled and blasted in one setup. |
| M27 × 0.5 screw-in back in 316L | Titanium on titanium galls; a steel back in a Ti case is the standard pairing and the reference does the same. 0.5 pitch gives three turns in the 1.5 mm thread zone. Thread minor Ø 26.46 is larger than the Ø 26.5 movement bore − 0.05, so the ring passes in from the back through the thread. The Ø 30 flange stands 0.8 proud and carries the face-seal O-ring groove and six Ø 1.5 spanner holes on a 20 mm PCD. |
| POM spacer ring, machined | One-off machinable, self-lubricating, holds the GL32 outline (15.6 × 18.8 pocket) and locates the dial; a 2.0 × 0.6 key at 9 o'clock stops rotation; the caseback clamps it against the ledge. |
| Generic Ø 4.0 tap-10 push-pull crown on a Ø 2.0 press-fit tube | Both are stock items. The 0.8 deep pocket in the 3 o'clock flank recesses the crown as on the reference. Tube engagement 1.95 mm, 1.2 mm proud of the pocket floor. |
| 22 mm single-pass strap through 40° channels | No lugs. Each channel is a 1.8 mm gap, 22.4 mm wide, entering the back face at y 16.2–19.0 and leaving the end face at z 2.52–4.87. The bar is 3.0 wide on the back face and 2.52 tall on the end face, with 3.43 mm of solid above the exit. Strap thickness ≤ 1.5 mm. |

Mass from the CAD volumes: case body 28.3 g (Ti Grade 2), caseback 11.1 g (316L),
ring 0.9 g (POM). The resin set is about 9.7 g.

Three things the v0.1 parameters leave open. The code reports them; `params.py` decides:

1. **Spacer-ring rehaut lip.** `ring_od` (26.30 Ti / 26.20 resin) against
   `ring_dial_recess_d` (26.20 / 26.30) leaves a 0.05 / −0.05 mm lip: a Ø 26.0 dial cannot
   sit inside a ring that itself must fit a Ø 26.5 bore. `spacer_ring.py` therefore builds
   the ring without its upper section (2.38 tall, top at the dial seat, no rehaut, nothing
   clamps it to the ledge) and prints a warning. The cheap fix is a smaller dial:
   `dial_d = 25.0` gives a 0.55 / 0.45 mm lip in the two profiles, and `dial_aperture_d`
   then has to drop to about 24.6 so the dial edge stays hidden. Going up a thread size
   instead (M28 × 0.5, movement bore about 27.5) costs the crown-side wall at the thread
   (1.7 → 1.2 inside the pocket) and the tube engagement (1.95 → 1.45, below the 1.5
   minimum), so that is not a free swap.
2. **Front chamfer at the crown pocket.** The 0.4 front chamfer cannot run around the
   pocket: 0.8 deep with R0.5 corners leaves only 0.3 mm of straight wall. `case_body.py`
   skips those five edges, so the STEP is sharp there. Either have the machinist break them
   by hand (0.2 × 45°, add the note to the drawing) or set `crown_pocket_corner_r ≤ 0.3` or
   `edge_chamfer_front ≤ 0.3`.
3. **Tube-to-thread margin is 0.18 mm** and hangs entirely on the UNVERIFIED
   `stem_below_dial_seat`. If the real stem sits lower, the tube hole breaks into the
   thread. Measure first (section 8).

## 4. Bill of materials

Made parts:

| part | stock | finish | notes |
|---|---|---|---|
| case body | Ti Grade 2 plate 10 mm, blank ≥ 35 × 47 | 32 × 44 × 8.3 | `bom.csv` lists 35 × 47 × 10.3; 10 mm nominal plate leaves ~0.85 per face to clean up |
| caseback | 316L round bar Ø 32 × 10 | flange Ø 30.0 × 0.8, boss Ø 27.0 × 1.5, M27 × 0.5 | |
| spacer ring | POM-C (acetal) rod Ø 30 × 20, black or natural | Ø 26.30 × 4.00 (2.38 tall under the current lip fallback) | |
| dial blank | brass CZ108 half-hard sheet 0.4 mm, blank ≥ 32 × 32; Ø 0.8 brass wire for the feet | Ø 26.0, Ø 1.6 centre hole, two feet | feet positions UNVERIFIED; printing is separate |

Bought parts (each row is also in `out/<profile>/bom.csv`):

| item | spec | where |
|---|---|---|
| crystal | flat sapphire Ø 27.0 × 2.0, polished both faces, plain straight edge; 0.1 mm diameter steps are stock | Cousins UK (generic flat sapphire by size), Esslinger, Ofrei |
| I-ring | Hytrel I-ring for 2.0 mm flat glass, for crystal Ø 27.0, wall 0.45 × 0.9 high (OD 27.9 uncompressed, 0.2 crush in the Ø 27.7 bore) | Cousins UK (I-ring gaskets by crystal size), Esslinger |
| caseback O-ring | ID 28.0 × CS 0.50, NBR 70 Sh; groove mean Ø 28.6 × 0.7 × 0.35 gives 30 % squeeze, 80 % fill | Cousins UK / Esslinger / Ofrei, 0.50 CS O-rings in 0.1 mm ID steps |
| flat gasket (alternative to the O-ring, fit one or the other) | ID 28.0 × OD 29.5 × 0.5, NBR or EPDM | same suppliers |
| case tube | stainless, OD 2.0, for a tap-10 (0.90 mm) stem, length ≥ 3.15 (1.95 in the case + 1.2 proud); retained with anaerobic adhesive | Ofrei / Esslinger generic case tubes; measure the real OD before reaming |
| crown | generic waterproof push-pull, Ø 4.0, tap 10, for a 2.0 tube, head ~2.5 long, with gasket (stock crowns are round; the octagon in the model is styling) | Cousins UK / Esslinger / Ofrei |
| movement | Miyota GL32, 6¾ × 8''', 3 hands, no date, stem at 3, supplied with stem; outline 15.3 × 18.5 (18.2 in some listings), casing height 2.28 | Cousins UK, Esslinger, Ofrei, or a Miyota distributor |
| battery | SR621SW (364), 1.55 V | any |
| hands | for Miyota GL30 / GL32: holes 1.20 / 0.70 / 0.17 (0.18 at some suppliers); minute and seconds ≤ 10 mm, hour ≈ 6.5 mm; stack ≤ 1.8 mm above the dial face (2.72 available) | Cousins UK / Esslinger / Ofrei GL30/GL32 hand sets |
| strap | 22 mm single-pass nylon (or thin leather), ≤ 1.5 mm thick | any strap retailer |

Tools and consumables: crystal press with a flat die ≥ 28 mm; adjustable two-pin case
wrench with pins ≤ Ø 1.4 set to 20 mm centres; 2.0 mm hand reamer and pin vice (resin);
anaerobic retaining compound for the tube; silicone grease for the O-ring; stem cutter.

## 5. Resin print guide (fit check before titanium)

Resin: a tough / ABS-like resin. Standard rigid resin chips on the thread crests and the
thin ring wall. Layer 0.05 mm. Print `case_body`, `caseback`, `spacer_ring` and
`dial_blank` from `out/resin/`. The resin profile already adds +0.10 to small bores and
sliding fits and prints the tube hole at 1.9.

Coupon first. Print a small test piece at nominal size, on the same resin and exposure:
a 15 × 15 × 3 plate with Ø 1.9, 2.0 and 2.5 holes and a 1.8 × 8 slot, plus a Ø 26.5 × 3
ring and a Ø 26.3 × 3 disc. Measure with a caliper. Holes under nominal mean your
exposure over-cures; either drop exposure or raise `hole_comp` / `fit_extra` in
`params.resin()` and rebuild. The disc must slide into the ring by hand.

Orientation:

| part | orientation | why |
|---|---|---|
| case body | back face toward the plate on supports, tilted 15–20° about the long (12–6) axis, crown flank high | Front face and crystal seat face up and get no support marks where the I-ring seals and the finish shows. The tilt removes the flat first layer (peel force, elephant foot) and lets resin drain from the thread bore and tube hole instead of trapping it against the plate. The thread mouth ends up facing down and sideways, so supports land on the back-face annulus around it, not on the teeth. |
| caseback | outer face down on supports, tilted about 10° | Thread and O-ring groove face up and print clean; support marks land on the outer face, which is sanded anyway. Clear the six Ø 1.5 tool holes with a drill afterwards. |
| spacer ring | axis vertical, open (movement) side up, light supports under the top face | The pocket prints as a cup, not a ceiling; the thin wall stays round. Do not put it flat on the plate: the first layers compress. |
| dial blank | face up, tilted 10° on supports | Or skip it; a paper disc does the same job for a fit check. |

Supports: medium, 0.3 mm tips, only on the back face and flanks of the case. None inside
the movement bore, the thread, the crystal seat, the strap channels or the front slots.

Wash and cure: two-stage IPA, 3–5 min each, brush the thread and blow the tube hole and
channels out with air before curing (resin left inside cures solid). UV cure 5–10 min per
side; tough resins go brittle when over-cured.

Then, in this order:

1. Ream the tube hole from 1.9 to 2.0 with a hand reamer in a pin vice, entering from the
   crown pocket, straight along X.
2. Chase the thread. The printed thread is M27.6 × 1.0 (no tap exists for it), so run the
   printed caseback in and out with a trace of wax and scrape any crest burr with a scalpel.
   Do not force it: binding at one fixed angle means a stepped layer, not a burr.
3. Ring into the bore from the back, key in the keyway: it should slide (0.3 diametral).
4. Movement into the ring, stem through the ring slot and out the tube hole.
5. Caseback on, checking that its flange seats on the back face.
6. Buy the I-ring before the crystal (it is cheap) and confirm it seats in the Ø 27.75
   bore; only then order the sapphire.
7. Crown and tube into the pocket; a strap through the channels.

Anything that binds is a parameter to revisit in `params.py`, not a print to sand until
it fits.

## 6. Titanium route

Stock: Ti Grade 2 (commercially pure) plate 10 mm, blank ≥ 35 × 47. Grade 2 machines and
blasts well; Grade 5 is not needed and is harder on tools.

Operation order for the case body:

1. **Op 1, back face up.** Face; rough the outline with the four 4.0 mm corner chamfers.
   Bore Ø 26.46 × 1.5 deep (thread minor), then Ø 26.5 from z 1.5 to 5.5 (movement bore).
   Thread-mill **M27 × 0.5 − 6H**, 1.5 deep, 0.2 × 45° lead chamfer; a tap this size is
   impractical in Ti. Keyway at 9 o'clock: 2.1 wide, 0.7 deep radial, 2.2 long from
   z 1.5. Back-face gate features: two grooves 1.0 wide × 0.5 deep × 10 long and two
   chamfer notches 2.5 × 0.8 × 0.8 high at the 1 and 7 o'clock corners. Back edge
   chamfer 0.3.
2. **Op 2, flip, front face up.** Face to 8.3. Crystal bore Ø 27.7 (±0.05) × 1.3 deep with
   a 0.2 lead chamfer; dial aperture Ø 25.2 through the 1.5 mm ledge; two front slots
   5.0 × 1.3, 1.0 deep, at 55° near the same corners; front edge chamfer 0.4 (hand-break
   the five pocket edges, see section 3).
3. **Side ops.** Strap channels: 1.8 gap, 22.4 wide, at 40° to the back face, through the
   corner between back face and end face at both ends; needs the part on a 40° angle
   fixture or a 4/5-axis machine with a ≤ 1.5 mm end mill, or wire EDM, which is the
   cleaner process for a 1.8 mm slot. Round the channel edges R0.4 and the bar's outer
   edge R0.8. Crown pocket 7.0 wide × 0.8 deep, R0.5 corners, full height in the +X flank.
   Tube hole Ø 2.0 H7 drilled and reamed along X at z 2.68 through the pocket floor into
   the bore, Ra 0.8.
4. **Finish.** Deburr; sandblast to Ra 1.6 with the crystal bore, thread, tube hole and
   keyway masked.

Caseback, 316L: turn the flange Ø 30.0 × 0.8 and the boss Ø 27.0 × 1.5; thread
**M27 × 0.5 − 6g**; O-ring groove in the flange inner face, mean Ø 28.6, 0.7 wide, 0.35
deep; six Ø 1.5 × 0.9 holes on a 20 mm PCD, one at 12; optional centre dimple Ø 1.5 × 0.4;
0.3 chamfer on the outer edge. Steel in titanium does not gall, but still grease the thread
lightly.

Spacer ring, POM-C: Ø 26.30 OD, pocket 15.60 × 18.80 (circle cut by flats), stem slot 2.4
wide, key 2.0 × 0.6 × 2.0, general tolerance ±0.05. Resolve the lip question in section 3
before this is made, or you get the 2.38 mm short ring.

Press-fit tube: hole Ø 2.0 H7 reamed. Generic tubes run about 2.0 +0.02/+0.04 OD; measure
with a micrometer. 0.02–0.04 interference is right for Ti; press square with the case
supported and retain with anaerobic compound.

Send the machinist `out/titanium/case_body.step`, `caseback.step`, `spacer_ring.step`
and `out/drawings/blueprints.pdf`, and state: general tolerance ±0.05, press-fit bores
Ra 0.8, thread classes as above, blast finish with the mask list, the hand-broken pocket
edges, and that every UNVERIFIED value in section 8 has been measured. `params.json` is
useful to attach so the numbers can be cross-checked.

## 7. Assembly order

1. Tube: press into the Ø 2.0 hole from the pocket side with retaining compound, 1.2 mm
   proud of the pocket floor. Let it cure.
2. Spacer ring into the case from the back, key at 9 o'clock into the keyway, until it
   touches the ledge underside.
3. Fit the dial to the movement (feet into the movement) and press the hands on the bench,
   then drop the movement with dial into the ring from the back, stem through the ring slot
   and the tube. The dial rests on the ring step.
4. Stem: fit, mark, remove, cut so the crown seats with 0.2–0.3 mm to the tube (crown face
   at x ≈ 15.5 from the case centre), thread the crown on with a trace of threadlocker,
   check both setting positions.
5. I-ring into the crystal bore from the front onto the seat, then press the crystal in
   from the front with a flat die until it sits on the seat; it stands 0.7 proud.
6. O-ring greased into the caseback flange groove (or the flat gasket on the flange face).
7. Caseback screwed in with the pin spanner on the 20 mm PCD holes until the flange meets
   the back face. Do not overtighten; it only has to clamp the ring.
8. Strap: feed the 22 mm single-pass in through the back-face openings and out of the end
   faces, then the keeper and buckle.

## 8. Measurement checklist (before titanium)

From `SPEC.md` section 11. Measure, update `params.py`, rebuild and re-verify.

| measure | on | parameter(s) |
|---|---|---|
| stem centre height below the movement top (dial seat) | GL32 | `stem_below_dial_seat` (1.20) → `z_stem`; the margin to the thread zone is only 0.18 |
| movement outline 12–6 | GL32 | `mvt_l` (18.5; 18.2 in some listings; pocket = `mvt_l` + 0.3, so an 18.2 movement has 0.3 play or needs a shim) |
| movement outline 3–9, casing height | GL32 | `mvt_w` (15.3), `mvt_h` (2.28) |
| hand pipe heights, seconds-hand top | GL32 + hand set | `mvt_pipe_h` (1.45), `hands_top_above_dial_face` (1.8); `hands_clearance` must stay ≥ `hands_clearance_min` (2.2) |
| dial feet positions and diameter | GL32 or the Miyota drawing | `dial_feet`, `dial_foot_d` |
| crystal and I-ring actually purchasable | supplier | `crystal_d` (27.0), `crystal_t` (2.0), `iring_wall` (0.45), `iring_h` (0.9); the bore follows |
| tube OD, crown size | supplier | `tube_od`, `tube_hole_d`, `crown_d`, `crown_len` |
| Terra's slot angle, bar size, gate cuts, crown recess (photo estimates) | reference | `strap_angle_deg`, `strap_bar_back_w`, `strap_gap`, `front_slot_*`, `back_groove_*`, `chamfer_notch_*`, `crown_pocket_*` |
| battery position (cosmetic) | GL32 | `BATTERY_XY` in `parts/purchased.py` |

After editing: `python3 build.py --profile all && python3 verify.py && python3 drawings.py`.

## 9. Making it distinct later

Everything below is a parameter; nothing needs code changes unless noted.

| parameter(s) | what it changes |
|---|---|
| `corner_chamfer` (4.0) | the plan-view corners: 0 is a square slab, larger reads as an octagon; a radius instead of a chamfer needs a code change in `case_body.py` |
| `front_slot`, `front_slot_len/_w/_depth/_angle_deg/_inboard`, `back_groove_*`, `chamfer_notch_*`, `gate_corners` | the corner "gate" details; set the booleans `False` for a plain slab, or move them to other corners |
| `crown_pocket_w/_depth/_corner_r`, `crown_octagonal` | the crown recess and crown shape |
| `strap_angle_deg`, `strap_bar_back_w`, `strap_gap` | how the strap wraps; the angle also sets the bar height and the wall above the exit (`verify.py` holds `strap_front_wall` ≥ 2.0 and the bar ≥ 1.5) |
| `W`, `L`, `H`, `edge_chamfer_front/back`, `crystal_engagement` | proportions, edge softness, how proud the crystal stands |
| `cb_tool_holes`, `cb_tool_pcd`, `cb_center_hole` | the back |

This reconstruction was made from published figures and photographs of DWC's Terra. The
slab, the angled slots and the gate cuts are their design. Use it as a study and a fit-check
platform, change the surface features before anything is sold, and do not reproduce their
marks, naming or engraving.

## Verification results

_To be filled in after `verify.py` and `drawings.py` have run on the final parameters._
