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
| `titanium` | the real part: CNC Ti Grade 2 case, 316L back, machined POM ring, brass dial | threads exported as plain bores at the minor / major diameter, the drawing carries the M27 × 0.5 callout; tube hole 2.0 H7; fits as designed |
| `resin` | MSLA fit-check print before any metal is cut | true helical thread at M27.6 × 1.0 with 0.15 radial clearance; small bores +0.10, sliding fits +0.10; tube hole printed 1.9 and reamed to 2.0 |

Status: v0.2 design intent. v0.1 was reviewed and six things changed, all in `params.py` /
`SPEC.md` and followed by the code (section 3 lists them, and what the review left open).
Nothing has been printed or machined yet. Values marked UNVERIFIED in `SPEC.md` /
`params.py` are catalogue figures or photo estimates; section 8 lists what to measure
before titanium is ordered. The finished case will be titanium; the resin profile exists
to prove the fits first.

## 2. Folder layout and how to run

```
hardware/terra-proto/
  SPEC.md            engineering spec: coordinates, feature recipe per part, checks, drawings, assembly order
  params.py          single source of truth for every dimension; profiles titanium / resin
  build.py           builds every part; writes STL, STEP, assembly, params.json, bom.csv
  verify.py          geometry checks, interference, ring insertion sweep, thread screw-in test, masses (SPEC s.8)
  drawings.py        blueprints.pdf + SVG, one sheet per part plus assembly and profiles sheets (SPEC s.9)
  renders.py         shaded PNG views, exploded view and filled sections of the exported STLs (review aid)
  requirements.txt   cadquery>=2.4, trimesh, numpy, matplotlib, pillow
  parts/
    threads.py       helical thread helper (resin) and cosmetic bores (titanium)
    case_body.py     the 32 x 44 x 8.3 slab
    caseback.py      screw-in back, flat-gasket recess (O-ring groove option), spanner holes
    spacer_ring.py   POM movement holder / rehaut, full height, keyed
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
python3 verify.py                 # every check from SPEC section 8; --profile titanium | resin | all (default)
python3 drawings.py               # blueprints for the machinist (--date, --sheets, --png DIR)
python3 renders.py                # PNG renders and sections of the STLs in out/<profile>/
python3 parts/case_body.py        # every part module also runs its own self-test; python3 -m parts.threads too
```

Outputs:

| path | contents |
|---|---|
| `out/titanium/`, `out/resin/` | `<part>.stl` and `<part>.step` for `case_body`, `caseback`, `spacer_ring`, `dial_blank` and each purchased placeholder |
| `out/<profile>/assembly.step` | all solids in assembly position, colour-coded (grey case, steel back, black ring, translucent crystal, orange bought parts); only the seal `cb_seal` selects is exported and assembled |
| `out/<profile>/params.json` | every parameter and derived value of that build |
| `out/<profile>/bom.csv` | the `bom()` rows of all modules in one CSV; each bought part appears once (the caseback rows are machining notes, qty 0) |
| `out/<profile>/verify_report.md` | `verify.py` results, every row with its measured value, limit and verdict |
| `out/drawings/blueprints.pdf`, `sheet_NN_*.svg` | nine A4 landscape sheets: case front, case back, sections A-A and B-B, caseback, spacer ring, dial blank, assembly, profiles and printing |
| `out/renders/<profile>_*.png` | one shaded view per part (plus a back view where the back matters), an exploded view and filled YZ / XZ sections per profile |

Warnings on stderr matter. With the v0.2 parameters the titanium build is warning-free
(every edge break applies, the ring is built full height). The resin build warns once and
still exits 0: the 27.2 ID flat gasket does not fit around the resin profile's Ø 27.6 boss
(`build.py` exports what `params.py` describes; it is `verify.py` that fails the two seal
rows on the resin profile and exits 1 for `--profile all`; `--profile titanium` exits 0).
A part module that cannot build what `params.py` describes raises instead of quietly
building something else.

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
| M27 × 0.5 screw-in back in 316L | Titanium on titanium galls; a steel back in a Ti case is the standard pairing and the reference does the same. 0.5 pitch gives three turns in the 1.5 mm thread zone. Thread minor Ø 26.46 is larger than the Ø 26.5 movement bore − 0.05, so the ring passes in from the back through the thread. The Ø 29.4 flange stands 0.8 proud, carries six Ø 1.5 × 0.9 spanner holes on a 20 mm PCD and, on its inner face, a 0.35 deep gasket recess from the Ø 27 boss out to Ø 29.0 that leaves a 0.2 seating land at the rim. |
| Flat gasket 27.2 × 28.8 × 0.45 in the flange recess | The 27.0 → 29.4 annulus is too narrow for a 0.5 CS O-ring with proper lands. A 0.45 gasket in a 0.35 recess is nominally squeezed 22 % when the rim land seats on the case; its 0.1 uncompressed protrusion is the squeeze and is excluded from the interference checks. The review found the recess too small for that gasket volume (open item 2 below). `cb_seal = "oring"` switches to a face-seal groove (mean Ø 28.2 × 0.6 × 0.28, 0.4 CS) on the same part. |
| POM spacer ring, machined, full height | One-off machinable, self-lubricating, holds the GL32 outline (15.6 × 18.8 pocket, 2.38 deep from the bottom) and carries the Ø 25.4 dial recess above the step, leaving a 0.45 (Ti) / 0.35 (resin) rehaut lip around the Ø 25.0 dial; the module refuses to build a lip under 0.3. A 2.0 × 0.6 × 2.0 key at 9 o'clock enters a keyway that is open from the back face through the thread zone, so the keyed ring inserts from the back; the caseback clamps it against the ledge underside. |
| Dial Ø 25.0 over a Ø 24.4 aperture | The dial hides 0.3 per side under the 1.5 mm ledge and sits with 0.2/side in the ring recess, more than the movement's 0.15/side, so the dial (carried by the movement) never binds. |
| Generic Ø 4.0 tap-10 push-pull crown on a Ø 2.0 press-fit tube | Both are stock items. The 0.8 deep pocket in the 3 o'clock flank recesses the crown as on the reference. Tube engagement 1.95 mm, 1.2 mm proud of the pocket floor. |
| 22 mm single-pass strap through 40° channels | No lugs. Each channel is a 1.8 mm gap, 22.4 mm wide, entering the back face at y 15.2–18.0 and leaving the end face at z 3.36–5.71. The bar is 4.0 wide on the back face and 3.36 tall on the end face (a 6.7 mm² Ti wedge before its R0.8 / R0.4 rounds, about 6.2 mm² after; `SPEC.md` 12 estimates roughly 190 N of straight pull to yield against about 100 N for the v0.1 3.0 bar), with 2.59 mm of solid above the exit. The Ø 29.4 caseback flange stays 0.5 inside the back-face opening. Strap thickness ≤ 1.5 mm. |

Mass from the CAD volumes (`density_*` in `params.py`, `density_dial` new in v0.2): case
body 28.03 g (Ti Grade 2), caseback 10.85 g (316L), spacer ring 1.01 g (POM), dial blank
1.66 g (brass); 41.55 g of made parts. The resin set is 9.66 g.

What the review changed (v0.1 → v0.2):

1. **Ring lip.** The Ø 26.0 dial could not sit inside a ring that itself fits a Ø 26.5 bore
   (0.05 / −0.05 lip), so v0.1 built a 2.38 mm short ring with no rehaut and nothing
   clamping it. The dial is now Ø 25.0 in a Ø 25.4 recess (`ring_dial_clearance` 0.4): lip
   0.45 (Ti) / 0.35 (resin), ring full height 4.0 from the caseback inner face to the ledge
   underside, clamped by the caseback. The short-ring fallback is gone; `spacer_ring.py`
   raises below a 0.3 lip.
2. **Keyway open to the back.** v0.1 cut a blind 2.2 mm pocket from z 1.5, behind a thread
   whose minor radius is smaller than the key tip, so the keyed ring could not be inserted.
   The keyway (2.1 wide, 0.7 deep) now runs from the back face through the thread zone to
   z 3.7 and interrupts the thread over its width; `verify.py` sweeps the ring out of the
   case in 0.25 steps to prove it.
3. **Strap bar 3.0 → 4.0.** The 3.0 bar reached Ti Gr 2 yield at about 100 N of strap pull.
   The bar is now 4.0 on the back face / 3.36 on the end face; the openings moved from
   y 16.2–19.0 to 15.2–18.0 and from z 2.52–4.87 to 3.36–5.71; the caseback flange shrank
   from Ø 30.0 to 29.4 to keep 0.5 mm to the opening.
4. **Flat gasket default.** The v0.1 face-seal O-ring (0.5 CS in a mean Ø 28.6 × 0.7 × 0.35
   groove) had no room for lands in the 27–29.4 annulus. Default is now a 27.2 × 28.8 × 0.45
   flat gasket in a 0.35 recess with a 0.2 rim land; the O-ring stays as `cb_seal = "oring"`
   with a 0.4 CS in a mean Ø 28.2 × 0.6 × 0.28 groove.
5. **Front chamfer 0.4 → 0.3** with the crown-pocket corner radius 0.5 → 0.3, so the chamfer
   runs all the way round including the five pocket edges instead of being skipped there.
6. **Dial / aperture 26.0 / 25.2 → 25.0 / 24.4.** Overlap under the ledge 0.3 per side
   (`verify.py` asserts ≥ 0.25); the dial blank got its own density for the mass table.

Still open after v0.2 (the code reports these, `params.py` / `SPEC.md` decide; none is a
modelling error):

1. **Resin gasket does not fit** (`verify.py` fails two rows on the resin profile only).
   `params.resin()` moves the thread to M27.6 × 1.0 but keeps `gasket_id` 27.2, and SPEC 4
   starts the recess at the boss, so the gasket lands 0.2 radial on the flange inboard of
   the recess (6.08 mm³ overlap); the gasket's volume is 145 % of the printed recess
   (Ø 27.6 → 29.0), and the M27.6 thread mouth leaves only 0.45 of the gasket's 0.8 wide
   counter-face on the case. The print therefore cannot check the seal (section 5, step 5).
   Fix: a resin-specific `gasket_id` (≥ 27.8 keeps 0.1 to the boss) or a recess that starts
   at a fixed Ø 27.0 regardless of profile.
2. **Titanium gasket overfills its recess.** 31.7 mm³ of gasket in a 30.8 mm³ recess
   (102.9 %): rubber does not compress in volume, so the 0.2 rim land cannot seat until the
   gasket has extruded inward over the thread mouth. The recess needs more volume (wider or
   deeper) or the gasket less (thinner, e.g. 0.40, or narrower). With the ±0.05 general
   tolerance on both the squeeze also falls to zero at worst case; the drawing already
   holds the recess depth to ±0.02.
3. **The open keyway crosses the seal seat.** The 2.1 wide slot (floor r 13.95) runs
   through the gasket counter-face (r 13.6–14.4): 1.1 % of the seat by area, but a straight
   path from outside past the thread into the movement cavity. No water resistance is
   claimed for v0.2; a plug in the keyway mouth behind the gasket, or a different
   anti-rotation feature, is a `SPEC.md` decision.
4. **Zero-clearance axial stack.** Rim land on the back face (z 0), boss top against the
   ring bottom (1.5) and ring top against the ledge (5.5) are all nominally exact, which is
   what `verify.py` checks. With ±0.05 on each part the caseback either stops on the ring
   before the land seats (no gasket squeeze) or the ring floats up to 0.15. One face needs a
   deliberate clearance or a tolerance direction before titanium is cut.
5. **Tube-to-thread margin 0.18 mm** (0.23 in resin) hangs entirely on the UNVERIFIED
   `stem_below_dial_seat`. If the real stem sits lower, the tube hole breaks into the
   thread relief (allowed, the drawing says so; the thread loses 2 mm of one turn). The web
   between the reamed hole and the relief is thin either way, so the machining order there
   is the shop's call (section 6).
6. **Dial eccentricity.** An 18.2 mm movement in the 18.8 pocket can shift 0.3 in Y, exactly
   the 0.3 the dial hides under the ledge. Measure `mvt_l` first, or shim (section 8).
7. **Gap above the I-ring.** A 0.9 tall I-ring in a 1.3 deep crystal bore leaves a
   0.35 × 0.4 open groove around the crystal above the ring (a dirt trap and a visible line):
   `iring_h` or `crystal_engagement`.
8. **Caseback proud, not flush.** The Ø 29.4 flange stands 0.8 proud of a flat back; the
   reference has a flush back in a machined counterbore. A counterbore in `case_body.py` is a
   code change, kept for v0.3.
9. **Key fit.** A 2.0 key in a 2.1 keyway is 0.1 nominal; at ±0.05 on both it can bind, so the
   drawing gives the POM key −0.05/−0.10 (clearance 0.10–0.25). `params.py` still says 2.0.
10. **SPEC 8.3 (a) wording.** The expected thread-zone overlap formula there omits the
    keyway interruption that the v0.2 open keyway causes; `verify.py` subtracts it
    (0.811 mm³) and passes at 0.00 %; without that term the check would miss by 2.5 %.
11. **Strap-bar figure.** The ≈ 190 N in `SPEC.md` 12 is an estimate with no stated load
    case; pull-test the resin and the first titanium part before trusting it.

## 4. Bill of materials

Made parts:

| part | stock | finish | notes |
|---|---|---|---|
| case body | Ti Grade 2 plate 10 mm, blank ≥ 35 × 47 | 32 × 44 × 8.3 | `bom.csv` lists 35 × 47 × 10.3; 10 mm nominal plate leaves ~0.85 per face to clean up |
| caseback | 316L round bar Ø 32 × 10 | flange Ø 29.4 × 0.8, boss Ø 27.0 × 1.5, M27 × 0.5 − 6g; gasket recess Ø 27.0 → 29.0 × 0.35 with a 0.2 rim land | the O-ring groove is an alternative seat, not machined unless `cb_seal = "oring"` |
| spacer ring | POM-C (acetal) rod Ø 30 × 20, black or natural | Ø 26.30 × 4.00; pocket 15.60 × 18.80 × 2.38 from the bottom; dial recess Ø 25.40 from the top (lip 0.45); stem slot 2.4; key 2.0 × 0.6 × 2.0 | the raised key needs a milling operation (section 6) |
| dial blank | brass CZ108 half-hard sheet 0.4 mm, blank ≥ 31 × 31; Ø 0.8 brass wire for the feet | Ø 25.0, Ø 1.6 centre hole, two feet | feet positions UNVERIFIED; printing is separate |

Bought parts (each row is also in `out/<profile>/bom.csv`):

| item | spec | where |
|---|---|---|
| crystal | flat sapphire Ø 27.0 × 2.0, polished both faces, plain straight edge; 0.1 mm diameter steps are stock | Cousins UK (generic flat sapphire by size), Esslinger, Ofrei |
| I-ring | Hytrel I-ring for 2.0 mm flat glass, for crystal Ø 27.0, wall 0.45 × 0.9 high (OD 27.9 uncompressed, 0.2 crush in the Ø 27.7 bore) | Cousins UK (I-ring gaskets by crystal size), Esslinger |
| caseback flat gasket (fitted, `cb_seal = "flat_gasket"`) | ID 27.2 × OD 28.8 × 0.45, NBR 70 Sh or EPDM; lies in the 0.35 recess, 22 % nominal squeeze when the 0.2 rim land seats (see open item 2 in section 3 before ordering) | Cousins UK / Esslinger / Ofrei generic flat caseback gaskets (0.1 mm ID / OD steps), or punched from 0.45 sheet |
| caseback O-ring (option, `cb_seal = "oring"`, needs the groove machined instead of the recess) | ID 27.7 × CS 0.40, NBR 70 Sh; groove mean Ø 28.2 × 0.6 × 0.28 gives 30 % squeeze, 75 % fill | same suppliers, 0.40 CS O-rings in 0.1 mm ID steps |
| case tube | stainless, OD 2.0, for a tap-10 (0.90 mm) stem, length ≥ 3.15 (1.95 in the case + 1.2 proud); retained with anaerobic adhesive | Ofrei / Esslinger generic case tubes; measure the real OD before reaming |
| crown | generic waterproof push-pull, Ø 4.0, tap 10, for a 2.0 tube, head ~2.5 long, with gasket (stock crowns are round; the octagon in the model is styling) | Cousins UK / Esslinger / Ofrei |
| movement | Miyota GL32, 6¾ × 8''', 3 hands, no date, stem at 3, supplied with stem; outline 15.3 × 18.5 (18.2 in some listings), casing height 2.28 | Cousins UK, Esslinger, Ofrei, or a Miyota distributor |
| battery | SR621SW (364), 1.55 V | any |
| hands | for Miyota GL30 / GL32: holes 1.20 / 0.70 / 0.17 (0.18 at some suppliers); minute and seconds ≤ 10 mm, hour about 6–6.5 mm; stack ≤ 1.8 mm above the dial face (2.72 available) | Cousins UK / Esslinger / Ofrei GL30/GL32 hand sets |
| strap | 22 mm single-pass nylon (or thin leather), ≤ 1.5 mm thick | any strap retailer |

Tools and consumables: crystal press with a flat die ≥ 28 mm; adjustable two-pin case
wrench with pins ≤ Ø 1.4 set to 20 mm centres; 2.0 mm hand reamer and pin vice (resin);
anaerobic retaining compound for the tube; silicone grease for the gasket or O-ring; stem
cutter.

## 5. Resin print guide (fit check before titanium)

Resin: a tough / ABS-like resin. Standard rigid resin chips on the thread crests and the
thin ring lip. Layer 0.05 mm. Print `case_body`, `caseback`, `spacer_ring` and
`dial_blank` from `out/resin/`. The resin profile already adds +0.10 to small bores and
sliding fits and prints the tube hole at 1.9.

Coupon first. Print a small test piece at nominal size, on the same resin and exposure:
a 15 × 15 × 3 plate with Ø 1.9, 2.0 and 2.4 holes and a 1.8 × 8 slot, plus a Ø 26.5 × 3
ring and a Ø 26.2 × 3 disc, and, standing on the plate, the two thinnest features of the
real parts: a 0.35 wide × 1.6 tall × 8 long wall (the ring's rehaut lip, 0.35 × 1.62 at
Ø 26.2 in the resin profile) and a 0.2 wide × 0.35 tall × 8 long rib (the caseback's rim
land). Both sit at the lower limit of what MSLA holds with 0.3 mm support tips and the
usual 0.05–0.1 mm of XY bleed. Measure with a caliper. Holes under nominal mean your
exposure over-cures; either drop exposure or raise `hole_comp` / `fit_extra` in
`params.resin()` and rebuild. The disc must slide into the ring by hand. If the 0.35 wall
comes out incomplete, wavy or fused to its supports, the remedy is in `params.py`, not in
the print: a resin-specific `ring_dial_clearance` of 0.3 (lip 0.40; the dial keeps 0.20 per
side, still more than the movement's 0.15) or a smaller `dial_d` (24.9 keeps
`dial_ledge_overlap` at the 0.25 floor `verify.py` asserts). Neither was changed in v0.2.
If the 0.2 rib prints rounded or short, plan on the light face-sanding of the caseback in
step 5 below.

Orientation:

| part | orientation | why |
|---|---|---|
| case body | back face toward the plate on supports, tilted 15–20° about the long (12–6) axis, crown flank high | Front face and crystal seat face up and get no support marks where the I-ring seals and the finish shows. The tilt removes the flat first layer (peel force, elephant foot) and lets resin drain from the thread bore and tube hole instead of trapping it against the plate. The thread mouth ends up facing down and sideways, so supports land on the back-face annulus around it, not on the teeth. |
| caseback | outer face down on supports, tilted about 10° | Thread and gasket recess face up and print clean; support marks land on the outer face, which is sanded anyway. Clear the six Ø 1.5 tool holes with a drill afterwards. |
| spacer ring | axis vertical, bottom (caseback) face down on supports, lip up | No overhang anywhere: the ring has no floor (the movement pocket opens straight into the dial recess), so the step faces up and the 0.35 lip grows from the thick base. Support marks land on the bottom face that sits on the caseback; keep the lip and the dial seat free of supports. The only downward-facing surface is the 2.4 mm wide bridge of lip over the stem slot, supported at both ends. Do not put it flat on the plate: the first layers compress. Lip down is wrong: the print would start on the 0.35 lip annulus and, 1.62 mm up, step inward by 3.3 mm (4.9 at the flats) with only air below the step. |
| dial blank | face up, tilted 10° on supports | Or skip it; a paper disc does the same job for a fit check. |

Supports: medium, 0.3 mm tips, only on the back face and flanks of the case, the outer
face of the caseback and the bottom face of the ring. None inside the movement bore, the
thread, the keyway, the tube hole, the crystal seat, the strap channels, the front slots,
the back grooves or the chamfer notches; none in the caseback's gasket recess or tool
holes; none on the ring's lip or dial seat.

Wash and cure: two-stage IPA, 3–5 min each, brush the thread and blow the tube hole,
keyway and channels out with air before curing (resin left inside cures solid). UV cure
5–10 min per side; tough resins go brittle when over-cured.

Then, in this order:

1. Ream the tube hole from 1.9 to 2.0 with a hand reamer in a pin vice, entering from the
   crown pocket, straight along X.
2. Chase the thread. The printed thread is M27.6 × 1.0 (no tap exists for it), so run the
   printed caseback in and out with a trace of wax and scrape any crest burr with a scalpel.
   Expect to: the fit has 0.15 mm radial and about 0.12 mm axial clearance per flank
   (measured in CAD by shifting the seated boss until it touches; the internal groove is
   0.65 P wide at its root against a 0.5 P tooth) over 1.5 turns of engagement, and with
   the case printed back face down one flank of every groove faces the plate and collects
   the overcure. `verify.py` proves the nominal fit only. Do not force it: binding at one
   fixed angle means a stepped layer, not a burr.
3. Ring into the bore from the back, key into the open keyway at 9 o'clock: it should slide
   (0.3 diametral) until its lip touches the ledge underside.
4. Movement with a dial (or a paper disc) into the ring from its top, then the ring assembly
   in from the back; stem through the ring slot and out the tube hole.
5. Caseback on, checking that its rim land seats on the back face and that it clamps the
   ring. The land is 0.2 wide and stands only 0.35 above the recess floor, so it may print
   rounded or slightly short; if the caseback rocks or does not sit flat, dress the land
   with a few light strokes of 1200-grit paper on a flat block, staying off the boss and
   taking off hundredths, then re-check. The printed recess starts at the Ø 27.6 boss: the
   27.2 gasket will not lie in it and would overfill it by 45 % (section 3, open item 1),
   so dry-fit the caseback without a gasket. The print checks the thread, the clamp and
   the fits, not the seal.
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
   Bore Ø 26.5 +0.08/0 straight from the back face to z 5.5: one bore serves as thread
   minor and movement bore, because 26.50–26.58 lies inside the M27 × 0.5 − 6H minor band
   26.459–26.599 (the 26.46 nominal minor is not a separate step to hold). Thread-mill
   **M27 × 0.5 − 6H** 1.5 deep, with a 0.3 wide relief Ø 27.2 +0.1/0 at z 1.2–1.5 and a
   0.2 × 45° lead chamfer at the mouth; a tap this size is impractical in Ti. Keyway at
   9 o'clock: 2.1 wide, 0.7 deep radial (floor at r 13.95), an open slot from the back face
   through the thread zone up to z 3.7 (mill it before thread-milling; it interrupts the
   thread over its 2.1 width). Machine the back-face annulus from the bore out to Ø 29.8
   flat, Ra 0.8: it is the gasket counter-face (r 13.6–14.4) and the seat of the caseback's
   rim land (r 14.5–14.7). Back-face gate features: two grooves 1.0 wide × 0.5 deep × 10 long
   and two chamfer notches 2.5 × 0.8 × 0.8 high at the 1 and 7 o'clock corners. Back edge
   chamfer 0.3 (the two end edges carry the R0.8 bar round instead; the four end-face /
   corner-chamfer vertical edges carry no break).
2. **Op 2, flip, front face up.** Face to 8.3. Crystal bore Ø 27.7 (±0.05) × 1.3 deep with
   a 0.2 lead chamfer; dial aperture Ø 24.4 through the 1.5 mm ledge; two front slots
   5.0 × 1.3, 1.0 deep, at 55° near the same corners; front edge chamfer 0.3 all round,
   continuing around the crown pocket.
3. **Side ops.** Strap channels: 1.8 gap, 22.4 wide, at 40° to the back face, through the
   corner between back face and end face at both ends; needs the part on a 40° angle
   fixture or a 4/5-axis machine with a ≤ 1.5 mm end mill, or wire EDM, which is the
   cleaner process for a 1.8 mm slot; the bar is 4.0 wide on the back face and 3.36 tall on
   the end face. Round the channel edges R0.4 and the bar's outer edge R0.8. Crown pocket
   7.0 wide × 0.8 deep, R0.3 corners, full height in the +X flank. Tube hole Ø 2.0 H7
   drilled and reamed along X at z 2.68 through the pocket floor into the bore, Ra 0.8.
   Its bottom sits 0.18 above the thread zone, so only a 0.18 web separates the reamed hole
   from the thread relief: measure the stem first (section 8), let the shop choose whether
   the hole or the thread is cut first, and deburr from the bore side. If the measured stem
   sits lower the hole may break into the relief (drawing note).
4. **Finish.** Deburr; sandblast to Ra 1.6 with the crystal bore, thread, tube hole, keyway
   and the back-face seal annulus (out to Ø 29.8) masked.

Caseback, 316L: turn the flange Ø 29.4 × 0.8 and the boss Ø 27.0 × 1.5; thread
**M27 × 0.5 − 6g**; gasket recess in the flange inner face from the boss out to Ø 29.0,
0.35 ±0.02 deep, leaving the 0.2 rim land (O-ring option instead: groove mean Ø 28.2,
0.6 wide, 0.28 deep); six Ø 1.5 × 0.9 holes on a 20 mm PCD, one at 12; optional centre
dimple Ø 1.5 × 0.4; 0.3 chamfer on the outer edge. Steel in titanium does not gall, but
still grease the thread lightly. Read section 3, open items 2 and 4, before this part is
made: the recess volume and the axial stack are both still nominal.

Spacer ring, POM-C: Ø 26.30 OD × 4.00 tall, movement pocket 15.60 × 18.80 (circle cut by
flats) 2.38 deep from the bottom, dial recess Ø 25.40 from the top leaving the 0.45 lip,
stem slot 2.4 wide from the bottom to the step, key 2.0 (−0.05/−0.10) × 0.6 × 2.0, general
tolerance ±0.05. The key stands 0.6 proud of the OD, so the OD cannot simply be turned:
turn both faces and the OD to Ø 27.6 min (key tip Ø 27.5), bore the pocket and the dial
recess, then mill the OD down to Ø 26.30 on a 4th axis or indexer leaving the 2.0 × 0.6 key
standing (the sliding-fit OD is a milled surface). Alternative: a Ø 1.5 POM or brass pin
pressed into a radial hole instead of the key, which needs a `params.py` change. The lip
is a 0.45 wall 1.62 tall: bore the dial recess last, light cuts, ring supported on its OD.

Press-fit tube: hole Ø 2.0 H7 reamed. Generic tubes run about 2.0 +0.02/+0.04 OD; measure
with a micrometer. 0.02–0.04 interference is right for Ti; press square with the case
supported and retain with anaerobic compound.

Send the machinist `out/titanium/case_body.step`, `caseback.step`, `spacer_ring.step`
and `out/drawings/blueprints.pdf`, and state: general tolerance ±0.05, press-fit bores and
seal faces Ra 0.8, thread classes as above, blast finish with the mask list, and that
every UNVERIFIED value in section 8 has been measured. `params.json` is useful to attach so
the numbers can be cross-checked.

## 7. Assembly order

From `SPEC.md` section 11; the geometry was checked against this sequence (ring insertion
sweep, axial stack, thread screw-in).

1. Press the Ø 2.0 tube into the case from the crown-pocket side, with retaining compound,
   until it is flush with the movement bore (1.2 mm proud of the pocket floor). Let it cure.
2. Fit the hands on the movement, then the dial (feet into the movement).
3. Drop the movement with its dial into the spacer ring from the ring's top: the dial
   settles in the Ø 25.4 recess, the movement passes through it into the pocket below.
4. Slide the ring assembly into the case from the back, key at 9 o'clock into the open
   keyway, until the ring's lip touches the ledge underside. No stem is fitted yet.
5. Push the cut stem with its crown in through the tube until the movement's setting lever
   clicks. (Fit, mark, remove and cut first, so the crown seats over the tube with its
   inner face about 0.3 mm off the pocket floor, x ≈ 15.5 from the case centre; a trace of
   threadlocker on the crown; check both setting positions.)
6. Seat the I-ring in the crystal bore, then press the crystal in from the front with a
   flat die until it sits on the seat; it stands 0.7 proud.
7. Lay the flat gasket in the caseback recess and screw the caseback in with the pin
   spanner on the 20 mm PCD holes until the rim land seats on the back face. The caseback
   clamps ring → ledge and movement → dial; do not overtighten.
8. Thread the single-pass strap through the channels: in at one end face, under the case
   over the caseback, out the other end face, then the keeper and buckle.

## 8. Measurement checklist (before titanium)

From `SPEC.md` section 12 plus the review's open items. Measure or decide, update
`params.py`, rebuild and re-verify.

| measure | on | parameter(s) |
|---|---|---|
| stem centre height below the movement top (dial seat) | GL32 | `stem_below_dial_seat` (1.20) → `z_stem`; the margin to the thread zone is only 0.18 |
| movement outline 12–6 | GL32 | `mvt_l` (18.5; 18.2 in some listings; pocket = `mvt_l` + 0.3, so an 18.2 movement has 0.3 play, equal to the dial's 0.3 overlap under the ledge, or needs a shim) |
| movement outline 3–9, casing height | GL32 | `mvt_w` (15.3), `mvt_h` (2.28) |
| hand pipe heights, seconds-hand top | GL32 + hand set | `mvt_pipe_h` (1.45), `hands_top_above_dial_face` (1.8); `hands_clearance` must stay ≥ `hands_clearance_min` (2.2) |
| dial feet positions and diameter | GL32 or the Miyota drawing | `dial_feet`, `dial_foot_d` |
| crystal and I-ring actually purchasable | supplier | `crystal_d` (27.0), `crystal_t` (2.0), `iring_wall` (0.45), `iring_h` (0.9); the bore follows |
| tube OD, crown size | supplier | `tube_od`, `tube_hole_d`, `crown_d`, `crown_len` |
| Terra's slot angle, bar size, gate cuts, crown recess (photo estimates) | reference | `strap_angle_deg`, `strap_bar_back_w`, `strap_gap`, `front_slot_*`, `back_groove_*`, `chamfer_notch_*`, `crown_pocket_*` |
| strap bar strength (4.0 × 3.36 Ti wedge, about 6.2 mm² after rounds; SPEC estimates ~190 N, load case unstated) | pull test on the resin / first Ti part | `strap_bar_back_w`; widen it and shrink `cb_flange_d` together to keep `flange_to_strap_margin` ≥ 0.3 |
| the resin gasket (27.2 ID against the Ø 27.6 resin boss) | design decision | a resin-specific `gasket_id`, or a fixed Ø 27.0 recess start in `SPEC.md` section 4 |
| the titanium gasket's recess volume (102.9 % fill) and the zero-squeeze worst case | design decision | `gasket_recess_od` / `gasket_recess_depth` / `gasket_t` / `gasket_od`, with the tolerance direction |
| the keyway mouth crossing the gasket seat; the zero-clearance axial stack (z 0, 1.5, 5.5) | design decision | `SPEC.md` sections 3.7, 4 and 8.3b; a clearance or tolerance direction on the ring height or boss length |
| battery position (cosmetic) | GL32 | `BATTERY_XY` in `parts/purchased.py` |

After editing: `python3 build.py --profile all && python3 verify.py && python3 drawings.py && python3 renders.py`.

## 9. Making it distinct later

Everything below is a parameter; nothing needs code changes unless noted.

| parameter(s) | what it changes |
|---|---|
| `corner_chamfer` (4.0) | the plan-view corners: 0 is a square slab, larger reads as an octagon; a radius instead of a chamfer needs a code change in `case_body.py` |
| `front_slot`, `front_slot_len/_w/_depth/_angle_deg/_inboard`, `back_groove_*`, `chamfer_notch_*`, `gate_corners` | the corner "gate" details; set the booleans `False` for a plain slab, or move them to other corners |
| `crown_pocket_w/_depth/_corner_r`, `crown_octagonal` | the crown recess and crown shape (`crown_pocket_corner_r` must stay ≤ `edge_chamfer_front` for the chamfer to run round the pocket) |
| `strap_angle_deg`, `strap_bar_back_w`, `strap_gap` | how the strap wraps; the angle also sets the bar height and the wall above the exit (`verify.py` holds `strap_front_wall` ≥ 2.0, the bar ≥ 2.5 tall on the end face and the caseback flange ≥ 0.3 inside the opening) |
| `cb_seal`, `gasket_*`, `oring_*` | flat gasket in a recess (default) or a face-seal O-ring groove on the same caseback |
| `W`, `L`, `H`, `edge_chamfer_front/back`, `crystal_engagement` | proportions, edge softness, how proud the crystal stands |
| `cb_tool_holes`, `cb_tool_pcd`, `cb_center_hole`, `cb_flange_d`, `cb_flange_t` | the back; the flange must stay `flange_to_strap_margin` inside the strap opening; a flush back (flange in a counterbore) is a code change in `case_body.py` |

This reconstruction was made from published figures and photographs of DWC's Terra. The
slab, the angled slots and the gate cuts are their design. Use it as a study and a fit-check
platform, change the surface features before anything is sold, and do not reproduce their
marks, naming or engraving.

## Verification results

`python3 build.py --profile all && python3 verify.py` on the v0.2 parameters, run
2026-09-30 (`out/<profile>/verify_report.md` has every row; `out/` is generated, so run
`build.py`, `verify.py`, `drawings.py` and `renders.py` once before the file links below
resolve).

| profile | result | time | what failed |
|---|---|---|---|
| titanium | **PASS**, 198 / 198 scored checks, 9 informational rows | 4 s | nothing |
| resin | **FAIL**, 202 / 204 scored checks, 11 informational rows | 28 s | the two resin-gasket rows (section 3, open item 1): `gasket/caseback` 6.084 mm³ and `seal_fit_problems` ("gasket_id 27.2 sits on the Ø 27.6 boss: needs ≥ 27.7"); both name `params.py`, no module or verifier defect |

Build and export (both profiles): all 12 solids (four made parts, eight purchased
placeholders; only the `flat_gasket` seal is built) are one valid solid each, at their SPEC
positions within ±0.02; every STL is watertight and its mesh volume is within 0.05 % of the
B-rep; no edge break was skipped.

Parametric assertions (titanium / resin): hands clearance 2.72 ≥ 2.2; tube-to-thread
margin 0.18 / 0.23 ≥ 0.1; thread minor 26.459 / 26.518 ≥ ring OD + 0.1 (26.4 / 26.3) and
≥ movement bore − 0.05; strap front wall 2.594 ≥ 2.0; bar 3.356 tall ≥ 2.5; caseback flange
0.5 ≥ 0.3 inside the strap opening; ring lip 0.45 / 0.35 ≥ 0.3; dial overlap 0.3 ≥ 0.25;
crown-side wall inside the pocket 1.35 / 1.325 at the crystal bore and 1.70 / 1.25 at the
thread (≥ 1.0); ring pocket wall 3.75 / 3.70 ≥ 2.5; tube engagement 1.95 ≥ 1.5; key tip
clears the keyway floor by 0.20 / 0.55.

Interference (pairwise intersection volumes, limit 0.01 mm³): every SPEC 8.3 pair and every
extra pair (ring/caseback, ring/tube, ring/crown, dial/movement, dial/case, dial/hands,
case/hands, crown/tube, crown/movement, tube/movement) is 0.0000 mm³ in both profiles,
with these documented exceptions:

| pair | titanium | resin |
|---|---|---|
| case/caseback inside the thread-zone envelope (z 0–1.5, r ≤ 13.5) | 31.606 mm³ = cosmetic tooth annulus 34.088 − lead chamfer 1.671 − keyway interruption 0.811 (0.00 %; the SPEC 8.3 (a) formula without the keyway term would be 2.50 % off); 0.0000 outside the envelope | true helix, plain pair: 0.0000 |
| case/iring | 7.860 mm³ = the designed 0.20 diametral crush of the Ø 27.9 I-ring in the Ø 27.7 bore | 5.901 mm³ = 0.15 crush in the Ø 27.75 bore |
| case/gasket, the 0.10 uncompressed protrusion past z 0 | 6.961 mm³ = the seating band minus the keyway mouth (0.076 mm³, 1.1 % of the seat); the seal's inner radius clears the thread lead chamfer by 0.171 | 5.928 mm³: keyway mouth 0.139 (2.0 %) plus 0.970 where the M27.6 groove runs out under the band, leaving 0.45 of its 0.80 width continuous |
| case/tube | 0.0000 | 0.0000 after reaming the printed 1.9 hole to 2.0; as printed 0.5973 mm³ = the ream allowance π/4 (2.0² − 1.9²) × 1.95 (0.00 %) |
| gasket/caseback (gasket inside its recess) | 0.0000, radial clearance +0.10 to the boss and to the recess wall | **6.084 mm³ FAIL**, −0.05 to the Ø 27.6 boss crest |

Ring insertion sweep (SPEC 8.3b): the assembled ring translated toward the back in 0.25 mm
steps, 23 positions from dz −0.25 to −5.75 (fully outside the case at the last), never
touches the case in either profile (worst 0.0000 mm³). Axial stack: ring top = ledge
underside 5.500, ring bottom = caseback inner face 1.500, dial on the ring step 3.880, and
movement back − caseback inner face = 0.100 = `mvt_axial_clearance`, all within 0.001 —
the nominal clamp works; the tolerance question is open item 4 in section 3.

Thread screw-in test (resin, true helix M27.6 × 1.0, 0.15 radial clearance, 1.5 turns
engaged): the caseback rotated 0 / 120 / 240° and advanced θ/360 × pitch along the
screw-in path meets the case with 0.0000 mm³; the bare boss advanced +0.333 / +0.667 into
the threaded zone also 0.0000; the negative control (boss rotated 180° without advancing)
clashes with 9.832 mm³, so the test can see a clash. Titanium: not applicable, the M27 × 0.5
thread is cut by the machinist into the cosmetic bore.

Probe points: every void that must exist (strap channels at four points each, crown pocket,
tube hole, keyway mouth on the back face and along the key path, front slots, chamfer
notches, back grooves, crystal bore and its lead chamfer, dial aperture, movement bore,
thread bore with a 36-point ring at the lead chamfer, ring pocket, stem slot, dial recess,
caseback recess and tool holes, gasket bore) is void, and every wall that must be solid
(strap bars, wall above the channel exits, end-face flats, walls around the keyway,
ledge, crown-side walls, ring lip at 12 / 9 / over the stem slot, caseback rim land and
recess floor) is solid, in both profiles.

Masses from the CAD volumes: titanium build 41.55 g of made parts (case body 28.03 g at
4.51 g/cm³, caseback 10.85 g at 8.00, spacer ring 1.01 g at 1.41, dial blank 1.66 g at 8.50);
the resin set 9.66 g at 1.15 g/cm³ (7.10 + 1.53 + 0.80 + 0.22).

`drawings.py` writes the nine sheets and `renders.py` the PNGs from the same solids without
warnings. Titanium renders:

![case body, front](out/renders/titanium_case_body_iso.png)

Case body from the front: crystal bore with its lead chamfer, dial aperture, the two
front slots at 1 and 7 o'clock, the crown pocket with the tube hole, and one strap channel
leaving the end face.

![exploded assembly](out/renders/titanium_exploded_iso.png)

Exploded along Z (tube and crown along X): crystal, I-ring, hands envelope, dial, movement
with stem, spacer ring, case body, flat gasket, caseback.

![assembly section at x = 0](out/renders/titanium_section_yz.png)

Assembly section in the YZ plane: the 40° strap channels through both ends with the
4.0 × 3.36 bars, the ring clamped between the caseback boss and the ledge, the dial on the
ring step, the I-ring in the crystal bore, and the gasket in the caseback recess with the
rim land on the back face.

![caseback from the back](out/renders/titanium_caseback_iso_back.png)

Caseback outer face: six Ø 1.5 spanner holes on the 20 mm PCD, centre dimple, 0.3 edge
chamfer.
