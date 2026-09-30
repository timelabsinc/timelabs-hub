"""
caseback -- screw-in back for the Terra-style GL32 case (SPEC.md section 4).

Assembly coordinates (params.py): case back face at z = 0, +Z toward the dial, +Y = 12 o'clock.

    flange   disc  O cb_flange_d x cb_flange_t, z = -cb_flange_t .. 0 (proud of the case back face)
    boss     O cb_thread_major x cb_thread_len, z = 0 .. cb_thread_len, inner face flat at z_cb_inner
               thread_model "plain" (titanium) : threads.boss_cosmetic, plain cylinder at the major O,
                                                  the machinist cuts M27 x 0.5 from the drawing callout
               thread_model "helix" (resin)    : threads.boss(clearance=cb_thread_clearance), true helix
    groove   face-seal O-ring groove in the flange's inner face (the z = 0 plane), cut downward:
               mean O oring_groove_mean_d, width oring_groove_w, depth oring_groove_depth
    holes    cb_tool_holes x O cb_tool_hole_d blind holes, cb_tool_hole_depth deep from the outer face,
               on PCD cb_tool_pcd, one at 12 o'clock (+Y); optional centre dimple (cb_center_hole)
    chamfer  OUTER_EDGE_CHAMFER (0.3) on the outer-face edge of the flange

Engraving is a drawing note only (cb_engrave_depth); no text is modelled.

Boolean order matters for OCC: every feature is a single-solid operation and every tool face is kept
well away (>= 0.3) from any existing face -- see the comments in build().

Run `python3 parts/caseback.py [out_dir]` from the terra-proto directory for the self-test: both
profiles build, volumes / bounding boxes are checked, STLs are written to out_dir (default: a
directory under the system temp folder) and, for the resin profile, the helical boss is screwed into a
test block carrying the matching internal thread to prove the fit.
"""
from __future__ import annotations

import math
import os
import sys

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


def _check_geometry(p: Params) -> None:
    """Raise ValueError (with the offending numbers) when params.py describes an impossible caseback."""
    r_fl = p.cb_flange_d / 2
    r_gi = p.oring_groove_mean_d / 2 - p.oring_groove_w / 2
    r_go = p.oring_groove_mean_d / 2 + p.oring_groove_w / 2
    r_crest = _boss_crest_radius(p)
    r_root = _boss_root_radius(p)
    r_hole_out = p.cb_tool_pcd / 2 + p.cb_tool_hole_d / 2
    r_hole_in = p.cb_tool_pcd / 2 - p.cb_tool_hole_d / 2
    problems = []
    if r_gi < r_crest + 0.1:
        problems.append(f"O-ring groove inner radius {r_gi:.3f} must clear the boss crest {r_crest:.3f} by >= 0.1")
    if r_go > r_fl - 0.2:
        problems.append(f"O-ring groove outer radius {r_go:.3f} leaves < 0.2 wall to the flange edge {r_fl:.3f}")
    if p.oring_groove_depth >= p.cb_flange_t - 0.2:
        problems.append(f"O-ring groove depth {p.oring_groove_depth} leaves < 0.2 under the flange face {p.cb_flange_t}")
    if p.oring_cs > p.oring_groove_w:
        problems.append(f"O-ring CS {p.oring_cs} wider than the groove {p.oring_groove_w}")
    if p.cb_tool_hole_depth >= p.cb_flange_t + p.cb_thread_len - 0.5:
        problems.append(f"tool hole depth {p.cb_tool_hole_depth} leaves < 0.5 to the inner face")
    if p.cb_tool_hole_depth > p.cb_flange_t and r_hole_out > r_root - 0.3:
        problems.append(f"tool holes (r out {r_hole_out:.3f}) break into the thread root {r_root:.3f}")
    if r_hole_out > r_gi - 0.3:
        problems.append(f"tool holes (r out {r_hole_out:.3f}) run into the O-ring groove {r_gi:.3f}")
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

    # 3. face-seal O-ring groove in the inner face (z = 0 plane), cut downward into the flange.
    #    Tool runs OVERSHOOT above z = 0 through empty space so the cut is not face-on-face.
    r_gi = p.oring_groove_mean_d / 2 - p.oring_groove_w / 2
    r_go = p.oring_groove_mean_d / 2 + p.oring_groove_w / 2
    groove = (cq.Workplane("XY").circle(r_go).circle(r_gi)
              .extrude(p.oring_groove_depth + OVERSHOOT)
              .translate((0, 0, -p.oring_groove_depth)))
    wp = wp.cut(groove)
    _check(wp, "O-ring groove")

    # 4. threaded boss z = 0 .. cb_thread_len (shares the z = 0 plane with the flange: a clean coplanar fuse;
    #    the groove's inner wall sits >= 0.3 outside the thread crest so no face is near-tangent)
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


def bom(p: Params) -> list[dict]:
    """Seal: a face-seal O-ring (default) or the flat-gasket alternative from SPEC 4."""
    r_gi = p.oring_groove_mean_d - p.oring_groove_w          # groove inner diameter
    # ring laid in the groove: ID just above the groove's inner wall so it stays put during assembly
    oring_id = round(r_gi + 0.1, 1)
    squeeze = 100 * (p.oring_cs - p.oring_groove_depth) / p.oring_cs
    fill = 100 * (math.pi / 4 * p.oring_cs ** 2) / (p.oring_groove_w * p.oring_groove_depth)
    return [
        {
            "item": "O-ring, face seal (caseback flange groove)",
            "spec": (f"ID {oring_id:.1f} x CS {p.oring_cs:.2f} mm, NBR 70 Sh; groove mean O "
                     f"{p.oring_groove_mean_d} x {p.oring_groove_w} wide x {p.oring_groove_depth} deep "
                     f"({squeeze:.0f}% squeeze, {fill:.0f}% fill)"),
            "qty": 1,
            "source": "watch-parts supplier, generic 0.50 CS O-ring (0.1 mm ID steps)",
        },
        {
            "item": "Flat gasket, alternative to the O-ring (fit one or the other)",
            "spec": "ID 28.0 x OD 29.5 x 0.5 mm flat ring, NBR or EPDM; seats on the flange face over the groove",
            "qty": 1,
            "source": "watch-parts supplier, generic caseback flat gasket",
        },
    ]


# --------------------------------------------------------------------------- self-test
def _intersection_volume(a: cq.Workplane, b: cq.Workplane) -> float:
    r = a.intersect(b)
    return sum(s.Volume() for s in r.solids().vals())


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


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    for prof in ("titanium", "resin"):
        p = params.get(prof)
        print(f"[{prof}] thread_model={p.thread_model} M{p.cb_thread_major} x {p.cb_thread_pitch}, "
              f"clearance {p.cb_thread_clearance}")
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
        if prof == "titanium":
            assert 1300 <= vol <= 1500, f"titanium caseback volume {vol:.1f} outside 1300-1500"
        else:
            assert 1250 <= vol <= 1500, f"resin caseback volume {vol:.1f} outside 1250-1500"
        # features actually present: 12 o'clock tool hole floor is open, centre dimple open, groove open
        z_out = -p.cb_flange_t
        x0, y0 = _tool_hole_centres(p)[0]
        assert abs(x0) < 1e-9 and y0 > 0, "first tool hole is not at 12 o'clock"
        assert not s.isInside(cq.Vector(x0, y0, z_out + p.cb_tool_hole_depth - 0.05)), "tool hole missing"
        assert s.isInside(cq.Vector(x0, y0, z_out + p.cb_tool_hole_depth + 0.05)), "tool hole too deep"
        assert not s.isInside(cq.Vector(p.oring_groove_mean_d / 2, 0, -p.oring_groove_depth / 2)), "groove missing"
        assert s.isInside(cq.Vector(p.oring_groove_mean_d / 2, 0, -p.oring_groove_depth - 0.1)), "groove too deep"
        if p.cb_center_hole:
            assert not s.isInside(cq.Vector(0, 0, z_out + p.cb_center_hole_depth / 2)), "centre dimple missing"
        assert s.isInside(cq.Vector(0, 0, p.cb_thread_len - 0.1)), "boss missing"
        assert not s.isInside(cq.Vector(0, 0, p.cb_thread_len + 0.1)), "boss too tall"

        path = os.path.join(out_dir, f"{PART}_{prof}.stl")
        cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
        m = trimesh.load(path)
        print(f"  STL {path}: {len(m.faces)} faces, watertight={m.is_watertight}, mesh volume {m.volume:.1f} mm^3")
        assert m.is_watertight, "STL is not watertight"
        assert abs(m.volume - vol) / vol < 0.01, "mesh volume disagrees with the B-rep volume"
        for row in bom(p):
            print(f"  BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")
        if p.thread_model == "helix":
            _thread_fit_test(p, wp, out_dir)
    print("caseback self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "terra-proto-selftest")
    _selftest(_out)
