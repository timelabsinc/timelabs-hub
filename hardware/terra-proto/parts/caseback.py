"""
caseback -- screw-in back for the Terra-style GL32 case (SPEC.md section 4, v0.2).

Assembly coordinates (params.py): case back face at z = 0, +Z toward the dial, +Y = 12 o'clock.

    flange   disc O cb_flange_d (29.4) x cb_flange_t (0.8), z = -cb_flange_t .. 0 (proud of the case back face)
    boss     O cb_thread_major x cb_thread_len, z = 0 .. cb_thread_len, inner face flat at z_cb_inner
               thread_model "plain" (titanium) : threads.boss_cosmetic, plain cylinder at the major O,
                                                  the machinist cuts M27 x 0.5 from the drawing callout
               thread_model "helix" (resin)    : threads.boss(clearance=cb_thread_clearance), true helix
    seal     in the flange's inner face (the z = 0 plane), cut downward, selected by cb_seal:
               "flat_gasket" (default): annular recess from the boss, O cb_thread_major, out to
                   O gasket_recess_od (29.0), gasket_recess_depth (0.35) deep. In titanium the recess wall IS the
                   boss surface (M27 major); in the resin print the helical crest sits cb_thread_clearance (0.15)
                   inside it -- the same stand-off the O-ring groove has -- which keeps the recess wall off the
                   helical crest faces (a wall on the crest cylinder shares that surface along broken arcs and
                   the STL tessellation cracks there). The rim land left outside the recess,
                   (cb_flange_d - gasket_recess_od) / 2 = 0.2 wide, seats on the case back face; the flat
                   gasket gasket_id x gasket_od x gasket_t (27.2 x 28.8 x 0.45) lies in the recess and is
                   squeezed (gasket_t - gasket_recess_depth) / gasket_t ~ 22 % where it faces the case.
               "oring": face-seal O-ring groove, mean O oring_groove_mean_d (28.2), width oring_groove_w (0.6),
                   depth oring_groove_depth (0.28), for an O-ring of CS oring_cs (0.4).
    holes    cb_tool_holes x O cb_tool_hole_d blind holes, cb_tool_hole_depth deep from the outer face,
               on PCD cb_tool_pcd, one at 12 o'clock (+Y); optional centre dimple (cb_center_hole)
    chamfer  OUTER_EDGE_CHAMFER (0.3) on the outer-face edge of the flange

Engraving is a drawing note only (cb_engrave_depth); no text is modelled.

bom() holds machining information only (SPEC 2: parts/purchased.py owns every bought part, the seal included):
rows 0 and 1 describe the seal seat cb_seal selects and the alternative seat (both qty 0; drawings.py reads them
by index for the seal notes on the caseback sheet), row 2 (titanium only) is the raw stock. Nothing here is a
purchase, so build.py's merged bom.csv counts the gasket / O-ring exactly once.

Boolean order matters for OCC: every feature is a single-solid operation and every tool face is kept
well away (>= 0.3) from any existing face -- see the comments in build(). The seal is cut into the bare
flange BEFORE the boss is fused on, so the recess tool only ever meets the flange; afterwards the boss
surface simply continues the recess wall across the z = 0 edge (no overlapping faces).

Run `python3 parts/caseback.py [out_dir]` from the terra-proto directory for the self-test: both
profiles build with both seal options, volumes are checked against the closed-form value, bounding
boxes and probe points are checked, the BOM rows are merged with parts/purchased.py bom() to prove the fitted
seal is bought exactly once, STLs are written to out_dir (default: a directory under the
system temp folder) and, for the resin profile, the helical boss is screwed into a test block carrying
the matching internal thread to prove the fit.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import replace

import cadquery as cq

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:               # lets `python3 parts/caseback.py` find params.py and parts/
    sys.path.insert(0, _ROOT)

import params                                     # noqa: E402
from params import Params                         # noqa: E402
from parts import threads                         # noqa: E402

PART = "caseback"
MATERIAL = {"titanium": "316L stainless", "resin": "ABS-like resin"}

OUTER_EDGE_CHAMFER = 0.3   # SPEC 4: chamfer on the outer-face edge
OVERSHOOT = 0.5            # how far cutting tools run past the face they enter (clean, non-tangent cuts)
LEAD_CHAMFER_CASE = 0.2    # lead chamfer the case body puts on the internal thread (SPEC 3.6), used by the fit test
SEALS = ("flat_gasket", "oring")
MIN_UNDER_SEAL = 0.2       # flange material that must remain under the recess / groove floor
MIN_RIM_LAND = 0.15        # flange face that must remain between the recess / groove and the flange edge
STOCK_SKIN = 2.0           # raw bar diameter over the flange (>= 1.0 of skin per side), rounded up to an even mm
STOCK_CHUCK = 7.0          # extra bar length beyond the finished part for chucking the slug (faced off)


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, step: str) -> cq.Workplane:
    """Every boolean must leave exactly one valid solid."""
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"caseback/{step}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"caseback/{step}: solid is not valid")
    return wp


def _boss_root_radius(p: Params) -> float:
    """Radius of the boss core (thread root) -- the tool holes must stay inside it."""
    return threads.minor_dia(p.cb_thread_major, p.cb_thread_pitch) / 2 - p.cb_thread_clearance


def _boss_crest_radius(p: Params) -> float:
    r = p.cb_thread_major / 2
    return r - p.cb_thread_clearance if p.thread_model == "helix" else r


def seal_radii(p: Params) -> tuple[float, float, float]:
    """(inner radius, outer radius, depth) of the seal recess / groove cut into the flange's inner face."""
    if p.cb_seal == "flat_gasket":
        return p.cb_thread_major / 2, p.gasket_recess_od / 2, p.gasket_recess_depth
    if p.cb_seal == "oring":
        return (p.oring_groove_mean_d / 2 - p.oring_groove_w / 2,
                p.oring_groove_mean_d / 2 + p.oring_groove_w / 2,
                p.oring_groove_depth)
    raise ValueError(f"cb_seal must be one of {SEALS}, got {p.cb_seal!r}")


def _check_geometry(p: Params) -> None:
    """Raise ValueError (with the offending numbers) when params.py describes an impossible caseback."""
    if p.cb_seal not in SEALS:
        raise ValueError(f"caseback: cb_seal must be one of {SEALS}, got {p.cb_seal!r}")
    r_fl = p.cb_flange_d / 2
    r_crest = _boss_crest_radius(p)
    r_root = _boss_root_radius(p)
    r_hole_out = p.cb_tool_pcd / 2 + p.cb_tool_hole_d / 2
    r_hole_in = p.cb_tool_pcd / 2 - p.cb_tool_hole_d / 2
    r_si, r_so, depth = seal_radii(p)
    under = p.cb_flange_t - depth
    land = r_fl - r_so
    problems = []

    # -- seal recess / groove: common rules (SPEC 4)
    name = "gasket recess" if p.cb_seal == "flat_gasket" else "O-ring groove"
    if under < MIN_UNDER_SEAL:
        problems.append(f"{name} depth {depth} leaves {under:.3f} < {MIN_UNDER_SEAL} under the flange face "
                        f"(cb_flange_t {p.cb_flange_t})")
    if land < MIN_RIM_LAND:
        problems.append(f"{name} outer radius {r_so:.3f} leaves a {land:.3f} < {MIN_RIM_LAND} rim land to the "
                        f"flange edge {r_fl:.3f}")
    if r_so - r_si < 0.3:
        problems.append(f"{name} is only {r_so - r_si:.3f} wide (inner r {r_si:.3f}, outer r {r_so:.3f})")
    if p.cb_tool_hole_depth > under and r_hole_out > r_si - 0.3:
        problems.append(f"tool holes (r out {r_hole_out:.3f}, {p.cb_tool_hole_depth} deep) would break through "
                        f"the {name} floor (inner r {r_si:.3f}, only {under:.3f} of flange under it)")
    # -- seal-specific
    if p.cb_seal == "flat_gasket":
        if p.gasket_od > p.gasket_recess_od:
            problems.append(f"flat gasket OD {p.gasket_od} does not fit the recess OD {p.gasket_recess_od}")
        if p.gasket_id < 2 * r_crest - 0.3:
            problems.append(f"flat gasket ID {p.gasket_id} would have to stretch > 0.3 over the boss O {2 * r_crest:.2f}")
        if p.thread_model == "helix" and r_si < r_crest + 0.1:
            problems.append(f"gasket recess wall r {r_si:.3f} must stand >= 0.1 off the helical crest {r_crest:.3f} "
                            f"(cb_thread_clearance {p.cb_thread_clearance}), or the two share one cylinder surface")
        if p.gasket_id >= p.gasket_od:
            problems.append(f"flat gasket ID {p.gasket_id} >= OD {p.gasket_od}")
        if p.gasket_t <= p.gasket_recess_depth:
            problems.append(f"flat gasket {p.gasket_t} thick in a {p.gasket_recess_depth} deep recess has no squeeze")
    else:
        if r_si < r_crest + 0.1:
            problems.append(f"O-ring groove inner radius {r_si:.3f} must clear the boss crest {r_crest:.3f} by >= 0.1")
        if p.oring_cs > p.oring_groove_w:
            problems.append(f"O-ring CS {p.oring_cs} wider than the groove {p.oring_groove_w}")
        if p.oring_cs <= p.oring_groove_depth:
            problems.append(f"O-ring CS {p.oring_cs} in a {p.oring_groove_depth} deep groove has no squeeze")
    # -- tool holes, dimple, chamfer
    if p.cb_tool_hole_depth >= p.cb_flange_t + p.cb_thread_len - 0.5:
        problems.append(f"tool hole depth {p.cb_tool_hole_depth} leaves < 0.5 to the inner face")
    if p.cb_tool_hole_depth > p.cb_flange_t and r_hole_out > r_root - 0.3:
        problems.append(f"tool holes (r out {r_hole_out:.3f}) break into the thread root {r_root:.3f}")
    if p.cb_center_hole and r_hole_in < p.cb_center_hole_d / 2 + 0.3:
        problems.append("tool holes overlap the centre dimple")
    if p.cb_center_hole and p.cb_center_hole_depth >= p.cb_flange_t + p.cb_thread_len - 0.5:
        problems.append("centre dimple too deep")
    if p.cb_tool_holes >= 2:
        chord = p.cb_tool_pcd * math.sin(math.pi / p.cb_tool_holes)
        if chord < p.cb_tool_hole_d + 0.3:
            problems.append("tool holes overlap each other on the PCD")
    if OUTER_EDGE_CHAMFER >= p.cb_flange_t - 0.1:
        problems.append("outer edge chamfer taller than the flange")
    if OUTER_EDGE_CHAMFER >= under:
        problems.append(f"outer edge chamfer {OUTER_EDGE_CHAMFER} reaches up into the {name} floor "
                        f"({under:.3f} of flange under it)")
    if problems:
        raise ValueError("caseback geometry impossible with params.py:\n  " + "\n  ".join(problems))


def _boss(p: Params) -> cq.Workplane:
    """The bare threaded boss, z = 0 .. cb_thread_len (also used by the screw-in test)."""
    if p.thread_model == "helix":
        return threads.boss(p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                            clearance=p.cb_thread_clearance, z0=0.0)
    return threads.boss_cosmetic(p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len, z0=0.0)


def _tool_hole_centres(p: Params) -> list[tuple[float, float]]:
    """(x, y) of the tool holes on the PCD, the first one at 12 o'clock (+Y), then anticlockwise."""
    r = p.cb_tool_pcd / 2
    out = []
    for k in range(p.cb_tool_holes):
        a = math.pi / 2 + 2 * math.pi * k / p.cb_tool_holes
        out.append((r * math.cos(a), r * math.sin(a)))
    return out


# --------------------------------------------------------------------------- part
def build(p: Params) -> cq.Workplane:
    """ONE valid solid in assembly coordinates: flange z -cb_flange_t..0, boss z 0..cb_thread_len."""
    _check_geometry(p)
    r_fl = p.cb_flange_d / 2
    z_out = -p.cb_flange_t                       # outer face

    # 1. flange disc, z = -cb_flange_t .. 0
    wp = cq.Workplane("XY").circle(r_fl).extrude(p.cb_flange_t).translate((0, 0, z_out))
    _check(wp, "flange")

    # 2. 0.3 chamfer on the outer-face edge (done on the plain disc, before any other feature)
    wp = wp.faces("<Z").edges().chamfer(OUTER_EDGE_CHAMFER)
    _check(wp, "outer edge chamfer")

    # 3. seal: gasket recess (from the boss surface outward) or O-ring groove, in the inner face (z = 0 plane),
    #    cut downward into the bare flange. The annular tool runs OVERSHOOT above z = 0 through empty space so
    #    the cut is not face-on-face; its floor sits >= MIN_UNDER_SEAL above the outer face and >= 0.25 clear
    #    of the chamfer cone, its walls meet the z = 0 face transversally.
    r_si, r_so, depth = seal_radii(p)
    seal = (cq.Workplane("XY").circle(r_so).circle(r_si)
            .extrude(depth + OVERSHOOT)
            .translate((0, 0, -depth)))
    wp = wp.cut(seal)
    _check(wp, "gasket recess" if p.cb_seal == "flat_gasket" else "O-ring groove")

    # 4. threaded boss z = 0 .. cb_thread_len (shares the z = 0 plane with the flange: a clean coplanar fuse).
    #    plain boss + flat_gasket: the recess wall lies on the boss cylinder, so the two meet only along the
    #    full z = 0 circle and the boss surface continues the recess wall -- one clean shared edge, no overlap.
    #    helix boss (either seal): the recess / groove wall sits >= 0.1 outside the thread crest, so the
    #    helical crest faces never share a surface with it and no face is near-tangent.
    wp = wp.union(_boss(p))
    _check(wp, "boss union")

    # 5. tool holes: blind from the outer face; they may bottom out inside the boss (depth 0.9 > flange 0.8),
    #    which is why they are cut after the boss union -- otherwise the boss would refill their floor.
    h = p.cb_tool_hole_depth + OVERSHOOT
    for (x, y) in _tool_hole_centres(p):
        hole = (cq.Workplane("XY").center(x, y).circle(p.cb_tool_hole_d / 2)
                .extrude(h).translate((0, 0, z_out - OVERSHOOT)))
        wp = wp.cut(hole)
    _check(wp, "tool holes")

    # 6. optional centre dimple (spot-drilled, decorative)
    if p.cb_center_hole:
        dimple = (cq.Workplane("XY").circle(p.cb_center_hole_d / 2)
                  .extrude(p.cb_center_hole_depth + OVERSHOOT)
                  .translate((0, 0, z_out - OVERSHOOT)))
        wp = wp.cut(dimple)
        _check(wp, "centre dimple")

    return wp


def _seat_row(p: Params, seal: str) -> dict:
    """Machining information for one seal seat -- the recess or the groove cut into the flange's inner face -- and
    the seal it takes. qty 0: nothing is bought on this row. The seal itself is a bought part and is listed once, in
    parts/purchased.py bom() (SPEC 2); listing it here as well made build.py's merged bom.csv count it twice."""
    q = p if p.cb_seal == seal else replace(p, cb_seal=seal)
    r_si, r_so, depth = seal_radii(q)                 # raises ValueError for an unknown seal
    land = q.cb_flange_d / 2 - r_so
    if seal == "flat_gasket":
        squeeze = 100 * (q.gasket_t - q.gasket_recess_depth) / q.gasket_t
        return {
            "item": "gasket recess, caseback flange (seat for the flat gasket)",
            "spec": (f"annular recess in the flange's inner face from the boss O {2 * r_si:.1f} out to "
                     f"O {2 * r_so:.1f}, {depth:.2f} deep, {land:.1f} rim land left at the flange edge; takes the flat "
                     f"gasket ID {q.gasket_id:.1f} x OD {q.gasket_od:.1f} x {q.gasket_t:.2f} mm, NBR 70 Sh, "
                     f"{squeeze:.0f}% squeeze when the land seats on the case"),
            "qty": 0,
            "source": ("machined on this part; the gasket is bought once: purchased row "
                       "'flat gasket, caseback face seal'"),
        }
    # O-ring laid in the groove, ID just above the groove's inner wall so it stays put during assembly
    oring_id = round(q.oring_groove_mean_d - q.oring_groove_w + 0.1, 1)
    squeeze = 100 * (q.oring_cs - q.oring_groove_depth) / q.oring_cs
    fill = 100 * (math.pi / 4 * q.oring_cs ** 2) / (q.oring_groove_w * q.oring_groove_depth)
    return {
        "item": "O-ring groove, caseback flange (seat for the face-seal O-ring)",
        "spec": (f"face-seal groove in the flange's inner face, mean O {q.oring_groove_mean_d:.1f} x "
                 f"{q.oring_groove_w:.1f} wide x {q.oring_groove_depth:.2f} deep (ID {2 * r_si:.1f} / "
                 f"OD {2 * r_so:.1f}), {land:.1f} rim land left at the flange edge; takes the O-ring "
                 f"ID {oring_id:.1f} x CS {q.oring_cs:.2f} mm, NBR 70 Sh ({squeeze:.0f}% squeeze, {fill:.0f}% fill)"),
        "qty": 0,
        "source": "machined on this part; the O-ring is bought once: purchased row 'O-ring, caseback face seal'",
    }


def bom(p: Params) -> list[dict]:
    """Machining information only -- no bought part appears here, so build.py's merged bom.csv lists each purchase
    once, from parts/purchased.py bom() (SPEC 2: purchased.py owns the bought parts, the caseback seal included).

        rows[0]  the seal seat cb_seal selects (SPEC 4), machined in the flange's inner face, qty 0
        rows[1]  the other seat as the alternative, qty 0: not machined on this part -- it needs the other seat cut
                 into the same flange face, so it is not a drop-in swap (drawings.py reads rows 0 and 1 by index)
        rows[2]  titanium profile only: raw stock for the machinist, qty 1 (the resin build is printed from the STL)
    """
    _check_geometry(p)                                # same ValueError as build() for an impossible caseback
    alt_key = "oring" if p.cb_seal == "flat_gasket" else "flat_gasket"
    fitted = _seat_row(p, p.cb_seal)
    alt = _seat_row(p, alt_key)
    alt["item"] += f" -- alternative seal seat, not machined on this part (build with cb_seal='{alt_key}')"
    alt["source"] = f"alternative only: neither this seat nor its seal is in the build (cb_seal = '{p.cb_seal}')"
    rows = [fitted, alt]
    if p.profile == "titanium":
        bar_d = 2 * math.ceil((p.cb_flange_d + STOCK_SKIN) / 2)
        bar_len = math.ceil(p.cb_total_t + STOCK_CHUCK)
        rows.append({
            "item": "caseback raw stock",
            "spec": (f"{MATERIAL['titanium']} round bar O {bar_d:.0f} x {bar_len:.0f} mm slug; finish flange "
                     f"O {p.cb_flange_d:.1f} x {p.cb_flange_t:.1f}, boss O {p.cb_thread_major:.1f} x "
                     f"{p.cb_thread_len:.1f} threaded M{p.cb_thread_major:g} x {p.cb_thread_pitch:g}, "
                     f"{p.cb_total_t:.1f} tall overall; general tolerance +-0.05"),
            "qty": 1,
            "source": "stainless stockist (316L round bar)",
        })
    return rows


# --------------------------------------------------------------------------- self-test
def _intersection_volume(a: cq.Workplane, b: cq.Workplane) -> float:
    r = a.intersect(b)
    return sum(s.Volume() for s in r.solids().vals())


def _expected_volume(p: Params) -> float:
    """Closed-form volume: flange disc - chamfer ring - seal annulus - tool holes - dimple + bare boss.
    The tool holes and the dimple lie entirely in solid material (r < thread root, checked by _check_geometry),
    so their volumes are exact; the boss volume is taken from the same solid build() fuses on."""
    r_fl = p.cb_flange_d / 2
    c = OUTER_EDGE_CHAMFER
    r_si, r_so, depth = seal_radii(p)
    v = math.pi * r_fl ** 2 * p.cb_flange_t
    v -= math.pi * c ** 2 * (r_fl - c / 3)                          # Pappus: triangle c^2/2 at r_fl - c/3
    v -= math.pi * (r_so ** 2 - r_si ** 2) * depth
    v -= p.cb_tool_holes * math.pi * (p.cb_tool_hole_d / 2) ** 2 * p.cb_tool_hole_depth
    if p.cb_center_hole:
        v -= math.pi * (p.cb_center_hole_d / 2) ** 2 * p.cb_center_hole_depth
    v += _boss(p).val().Volume()
    return v


def _thread_fit_test(p: Params, caseback: cq.Workplane, out_dir: str) -> None:
    """Resin profile: screw the helical boss into a block carrying the matching internal thread."""
    P = p.cb_thread_pitch
    block = cq.Workplane("XY").box(p.W, p.L, p.H, centered=(True, True, False))
    block = threads.cut_internal(block, p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                                 clearance=p.cb_thread_clearance, z0=0.0, lead_chamfer=LEAD_CHAMFER_CASE)
    _check(block, "thread test block")
    boss = _boss(p)
    _check(boss, "bare boss")
    cq.exporters.export(block, os.path.join(out_dir, "caseback_thread_test_block.stl"),
                        tolerance=0.005, angularTolerance=0.05)
    cq.exporters.export(boss, os.path.join(out_dir, "caseback_thread_test_boss.stl"),
                        tolerance=0.005, angularTolerance=0.05)

    # seated (rotation 0): bare boss and the complete caseback (flange face touching the block's back face)
    v_boss = _intersection_volume(boss, block)
    v_cb = _intersection_volume(caseback, block)
    print(f"  thread fit, seated:            boss/block {v_boss:.4f} mm^3, caseback/block {v_cb:.4f} mm^3")
    assert v_boss < 0.01, f"seated boss interferes with the internal thread: {v_boss:.4f} mm^3"
    assert v_cb < 0.01, f"seated caseback interferes with the case block: {v_cb:.4f} mm^3"

    # screw-in motion. Right-hand helix: rotating the boss by +theta about +Z re-aligns it with the internal
    # thread when paired with an axial shift of theta/360 * P. Shifting the seated boss UP by +P/3 would push its
    # top 0.33 mm into the solid case above the thread zone (that is the seat stopping it, not the thread), so the
    # screw-in path is sampled at the same helical alignment one full turn earlier: dz = theta/360 * P - P,
    # i.e. the positions the boss really passes through during the last turn before it seats.
    for theta in (120.0, 240.0):
        dz = theta / 360.0 * P - P
        b = boss.rotate((0, 0, 0), (0, 0, 1), theta).translate((0, 0, dz))
        v = _intersection_volume(b, block)
        print(f"  thread fit, rot {theta:5.1f} deg, dz {dz:+.3f}: boss/block {v:.4f} mm^3")
        assert v < 0.01, f"boss interferes at {theta} deg / {dz:+.3f} mm: {v:.4f} mm^3"
    # the same alignment shifted UP (+theta/360 * P), checked against the threaded zone of the block only
    # (z 0 .. cb_thread_len): the boss's top slice sits in solid case above the seat and is not a thread question
    zone = block.intersect(cq.Workplane("XY").box(p.W + 2, p.L + 2, p.cb_thread_len, centered=(True, True, False)))
    _check(zone, "threaded zone")
    for theta in (120.0, 240.0):
        dz = theta / 360.0 * P
        b = boss.rotate((0, 0, 0), (0, 0, 1), theta).translate((0, 0, dz))
        v = _intersection_volume(b, zone)
        print(f"  thread fit, rot {theta:5.1f} deg, dz {dz:+.3f}: boss/threaded zone {v:.4f} mm^3")
        assert v < 0.01, f"boss interferes with the threaded zone at {theta} deg / {dz:+.3f} mm: {v:.4f} mm^3"

    # negative control: half a turn out of phase must clash, proving the intersection test can see interference
    bad = boss.rotate((0, 0, 0), (0, 0, 1), 180.0)
    v_bad = _intersection_volume(bad, block)
    print(f"  thread fit, 180 deg unshifted (control): boss/block {v_bad:.3f} mm^3 (must be > 1)")
    assert v_bad > 1.0, "negative control did not interfere -- intersection test is not trustworthy"


def _probe_features(p: Params, s: cq.Solid) -> None:
    """isInside probes: every modelled feature is really there and nothing is cut that should not be."""
    z_out = -p.cb_flange_t
    r_fl = p.cb_flange_d / 2
    r_si, r_so, depth = seal_radii(p)
    r_mid = (r_si + r_so) / 2
    # tool hole at 12 o'clock: open to its floor, closed below it
    x0, y0 = _tool_hole_centres(p)[0]
    assert abs(x0) < 1e-9 and y0 > 0, "first tool hole is not at 12 o'clock"
    assert not s.isInside(cq.Vector(x0, y0, z_out + p.cb_tool_hole_depth - 0.05)), "tool hole missing"
    assert s.isInside(cq.Vector(x0, y0, z_out + p.cb_tool_hole_depth + 0.05)), "tool hole too deep"
    # seal recess / groove: open, correct depth, flange intact inboard of it and on the rim land
    assert not s.isInside(cq.Vector(r_mid, 0, -depth / 2)), f"{p.cb_seal} recess missing"
    assert not s.isInside(cq.Vector(0, r_si + 0.1, -depth + 0.05)), f"{p.cb_seal} recess inner wall not at r {r_si:.3f}"
    assert not s.isInside(cq.Vector(0, r_so - 0.1, -depth + 0.05)), f"{p.cb_seal} recess outer wall not at r {r_so:.3f}"
    assert s.isInside(cq.Vector(r_mid, 0, -depth - 0.1)), f"{p.cb_seal} recess too deep"
    assert s.isInside(cq.Vector(r_si - 0.1, 0, -depth / 2)), "flange missing inboard of the seal recess"
    assert s.isInside(cq.Vector((r_so + r_fl) / 2, 0, -0.1)), "rim land missing"
    assert s.isInside(cq.Vector(-(r_so + r_fl) / 2, 0, -0.1)), "rim land missing at 9 o'clock"
    if p.cb_seal == "oring" and p.oring_groove_depth < p.gasket_recess_depth:
        # the flat-gasket recess must NOT be there: solid below the groove floor, inside the would-be recess
        z = -(p.oring_groove_depth + p.gasket_recess_depth) / 2
        assert s.isInside(cq.Vector(r_mid, 0, z)), "gasket recess cut although cb_seal == 'oring'"
    if p.cb_center_hole:
        assert not s.isInside(cq.Vector(0, 0, z_out + p.cb_center_hole_depth / 2)), "centre dimple missing"
    assert s.isInside(cq.Vector(0, 0, p.cb_thread_len - 0.1)), "boss missing"
    assert not s.isInside(cq.Vector(0, 0, p.cb_thread_len + 0.1)), "boss too tall"
    assert s.isInside(cq.Vector(_boss_root_radius(p) - 0.1, 0, p.cb_thread_len / 2)), "boss core missing"
    assert not s.isInside(cq.Vector(r_si + 0.1, 0, p.cb_thread_len / 2)), "material outside the boss crest"


def _check_bom_rows(p: Params, rows: list[dict]) -> None:
    """bom() contract: seat rows first (qty 0, fitted then alternative), stock only for titanium, and -- merged with
    parts/purchased.py bom() as build.py does for bom.csv -- the fitted seal bought exactly once."""
    from parts import purchased
    seat = "gasket recess" if p.cb_seal == "flat_gasket" else "O-ring groove"
    other = "O-ring groove" if p.cb_seal == "flat_gasket" else "gasket recess"
    assert all(set(r) == {"item", "spec", "qty", "source"} for r in rows), "bom(): row keys"
    assert rows[0]["item"].startswith(seat) and rows[0]["qty"] == 0, f"bom(): row 0 must be the {seat} seat, qty 0"
    assert rows[1]["item"].startswith(other) and "alternative" in rows[1]["item"] and rows[1]["qty"] == 0, \
        f"bom(): row 1 must be the {other} as the qty-0 alternative"
    stock = [r for r in rows if "raw stock" in r["item"]]
    assert len(stock) == (1 if p.profile == "titanium" else 0), f"bom(): {len(stock)} stock rows for {p.profile}"
    assert all(r["qty"] == 0 for r in rows if r not in stock), "bom(): caseback.py must not list bought parts"
    merged = rows + purchased.bom(p)
    bought = [r for r in merged if r["qty"] >= 1 and "caseback" in r["item"].lower()
              and ("gasket" in r["item"].lower() or "o-ring" in r["item"].lower())]
    assert len(bought) == 1, f"merged BOM buys the caseback seal {len(bought)}x: {[r['item'] for r in bought]}"
    want = "flat gasket" if p.cb_seal == "flat_gasket" else "O-ring"
    assert bought[0]["item"].startswith(want), f"merged BOM buys {bought[0]['item']!r} for cb_seal {p.cb_seal!r}"
    # the seat row quotes the same seal figures purchased.py sells
    if p.cb_seal == "flat_gasket":
        assert f"{purchased.gasket_squeeze_pct(p):.0f}% squeeze" in rows[0]["spec"]
    else:
        assert f"{purchased.oring_squeeze_pct(p):.0f}% squeeze" in rows[0]["spec"]
        assert f"{purchased.oring_fill_pct(p):.0f}% fill" in rows[0]["spec"]


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    for prof in ("titanium", "resin"):
        for seal in SEALS:
            p = params.get(prof, cb_seal=seal)
            print(f"[{prof} / {seal}] thread_model={p.thread_model} M{p.cb_thread_major} x {p.cb_thread_pitch}, "
                  f"clearance {p.cb_thread_clearance}, flange O{p.cb_flange_d}")
            wp = build(p)
            s = wp.val()
            assert wp.solids().size() == 1 and s.isValid()
            vol = s.Volume()
            bb = s.BoundingBox()
            mass_g = vol * p.density_caseback / 1000.0
            print(f"  volume {vol:.1f} mm^3  mass {mass_g:.2f} g  bbox x {bb.xmin:.3f}..{bb.xmax:.3f} "
                  f"y {bb.ymin:.3f}..{bb.ymax:.3f} z {bb.zmin:.3f}..{bb.zmax:.3f}")
            assert abs(bb.zmin + p.cb_flange_t) < 0.02, f"zmin {bb.zmin} != {-p.cb_flange_t}"
            assert abs(bb.zmax - p.cb_thread_len) < 0.02, f"zmax {bb.zmax} != {p.cb_thread_len}"
            assert abs(bb.xmax - p.cb_flange_d / 2) < 0.02 and abs(bb.ymax - p.cb_flange_d / 2) < 0.02
            v_exp = _expected_volume(p)
            print(f"  closed-form volume {v_exp:.1f} mm^3 (diff {100 * (vol - v_exp) / v_exp:+.3f}%)")
            assert abs(vol - v_exp) / v_exp < 0.002, f"volume {vol:.2f} disagrees with the closed-form {v_exp:.2f}"
            _probe_features(p, s)

            path = os.path.join(out_dir, f"{PART}_{prof}_{seal}.stl")
            cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
            m = trimesh.load(path)
            print(f"  STL {path}: {len(m.faces)} faces, watertight={m.is_watertight}, mesh volume {m.volume:.1f} mm^3")
            assert m.is_watertight, "STL is not watertight"
            assert abs(m.volume - vol) / vol < 0.01, "mesh volume disagrees with the B-rep volume"
            rows = bom(p)
            _check_bom_rows(p, rows)
            for row in rows:
                print(f"  BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")
            if p.thread_model == "helix":
                _thread_fit_test(p, wp, out_dir)
    # the default seal is the flat gasket, and params.py says so
    assert params.get("titanium").cb_seal == "flat_gasket"
    for bad in ("oring_x", "", None):
        try:
            build(params.get("titanium", cb_seal=bad))
        except ValueError:
            pass
        else:
            raise AssertionError(f"cb_seal={bad!r} was accepted")
    print("caseback self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "terra-proto-selftest")
    _selftest(_out)
