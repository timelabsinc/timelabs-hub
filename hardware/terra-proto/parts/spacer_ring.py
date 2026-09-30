"""
spacer_ring -- POM movement holder / rehaut for the Terra-style GL32 case (SPEC.md section 5).

Assembly coordinates (params.py): +X = 3 o'clock (crown), +Y = 12 o'clock, +Z = front; case back face z = 0.

    body    cylinder O ring_od from z = z_cb_inner to z = z_ledge_bottom (height ring_h): it sits on the
            caseback inner face and touches the ledge underside, so the caseback clamps it
    pocket  the GL32 outline = circle O ring_pocket_l INTERSECT strip |x| <= ring_pocket_w/2 (flats at
            3 and 9 o'clock), open at the bottom, from the ring bottom up ring_step_h to the dial seat;
            the movement rests on the caseback, the dial rests on the step
    recess  O ring_dial_recess_d from the step up to the top: the lip left around the dial is the rehaut
    slot    stem slot at +X, width ring_stem_slot_w (in Y), through the wall, from the bottom up to
            z_stem + 1.2, so the movement drops in from the back with its stem fitted
    key     anti-rotation key on the OD at -X: ring_key_w (Y) x ring_key_h (radial) x ring_key_len (axial,
            from the bottom); it mates with the keyway the case body cuts at 9 o'clock

Lip wall.  The lip between the dial recess and the OD is (ring_od - ring_dial_recess_d) / 2.  With the v0.1
values in params.py that is 0.05 mm in the titanium profile (26.3 vs 26.2) and -0.05 mm in the resin
profile (26.2 vs 26.3): a Ø26.0 dial simply does not fit inside a ring that itself must fit a Ø26.5 bore.
That is a params.py problem (dial_d / mvt_bore_d / ring_od_clearance), not one this module can solve, so
when the lip would be thinner than LIP_WALL_MIN the ring is built WITHOUT the upper section: it then runs
from z_cb_inner to the dial seat only (height ring_step_h), the dial rests on its top face and is located by
the movement bore, and nothing clamps the ring against the ledge.  A warning is printed and recorded in
WARNINGS; verify.py / build.py should surface it.  Fix in params.py, e.g. dial_d <= ring_od - 2 * 0.5 - 0.2,
and the full-height ring with the rehaut lip is built again (the self-test exercises that path with an
override).

Boolean order matters for OCC: every feature is a single-tool operation, every tool face is either well
inside solid material or overlaps >= OVERLAP into an existing void, and the only coincident faces are
exact (the step plane and the slot top when z_stem + 1.2 lands on the dial seat, which is snapped to
exact equality).

Run `python3 parts/spacer_ring.py [out_dir]` from the terra-proto directory for the self-test: both
profiles build (plus both profiles with a dial small enough for a real lip), one valid solid each, the
volume is checked against a closed-form value, probe points prove every feature exists, STLs are written
to out_dir (default: TERRA_SCRATCH or a directory under the system temp folder) and checked watertight.
"""
from __future__ import annotations

import math
import os
import sys

import cadquery as cq

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:               # lets `python3 parts/spacer_ring.py` find params.py
    sys.path.insert(0, _ROOT)

import params                                     # noqa: E402
from params import Params                         # noqa: E402

PART = "spacer_ring"
MATERIAL = {"titanium": "POM-C (acetal), machined", "resin": "ABS-like resin"}

LIP_WALL_MIN = 0.30     # thinnest radial lip worth machining in POM / printing in resin
POCKET_WALL_MIN = 0.50  # thinnest wall between the movement pocket and the OD
OVERRUN = 1.0           # how far a cutting tool runs past a face it enters through
OVERLAP = 0.3           # how far a tool reaches into an existing void / a fused tool reaches into solid
COPLANAR_SNAP = 0.05    # |dz| below which a tool face is snapped onto an existing face (never near-coplanar)

# Warnings raised by the last build(), as "<profile>: <text>". build() clears its own profile's entries.
WARNINGS: list[str] = []


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, step: str) -> cq.Workplane:
    """Every boolean must leave exactly one valid solid."""
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"{PART}/{step}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"{PART}/{step}: solid is not valid")
    return wp


def _warn(p: Params, text: str) -> None:
    msg = f"{p.profile}: {text}"
    if msg not in WARNINGS:
        WARNINGS.append(msg)
    print(f"WARNING [{PART}/{p.profile}] {text}", file=sys.stderr)


def _box(x0, x1, y0, y1, z0, z1) -> cq.Workplane:
    return (cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=(False, False, False))
            .translate((x0, y0, z0)))


def _cylinder_z(r: float, z0: float, z1: float) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def outline_tool(r: float, half_w: float, z0: float, z1: float) -> cq.Workplane:
    """The GL32-style outline (circle radius r cut by flats at x = +-half_w) extruded from z0 to z1.
    Also used by purchased.py for the movement envelope."""
    cyl = _cylinder_z(r, z0, z1)
    if half_w >= r:
        return cyl
    strip = _box(-half_w, half_w, -(r + OVERRUN), r + OVERRUN, z0 - OVERRUN, z1 + OVERRUN)
    return _check(cyl.intersect(strip), "outline")


def outline_area(r: float, half_w: float) -> float:
    """Area of the circle radius r cut by flats at x = +-half_w."""
    if half_w >= r:
        return math.pi * r * r
    h = r - half_w
    segment = r * r * math.acos((r - h) / r) - (r - h) * math.sqrt(2 * r * h - h * h)
    return math.pi * r * r - 2 * segment


# --------------------------------------------------------------------------- geometry
def geometry(p: Params) -> dict:
    """All the numbers build() uses, in assembly coordinates. Raises ValueError with the offending
    values when params.py describes a ring that cannot exist; the too-thin lip is a warning + fallback."""
    r_od = p.ring_od / 2
    z0 = p.z_cb_inner
    z1 = p.z_ledge_bottom
    z_step = z0 + p.ring_step_h
    r_pl = p.ring_pocket_l / 2
    x_flat = p.ring_pocket_w / 2
    r_rec_req = p.ring_dial_recess_d / 2
    lip_wall = r_od - r_rec_req
    z_slot_top = p.z_stem + 1.2

    problems = []
    if p.ring_h < 0.5:
        problems.append(f"ring height ring_h {p.ring_h:.3f} < 0.5 (z_cb_inner {z0} .. z_ledge_bottom {z1})")
    if p.ring_step_h < 0.5:
        problems.append(f"ring_step_h {p.ring_step_h:.3f} < 0.5: the dial seat is not above the caseback")
    if p.ring_step_h > p.ring_h - 1e-6:
        problems.append(f"ring_step_h {p.ring_step_h:.3f} >= ring_h {p.ring_h:.3f}: dial seat above the ledge")
    if r_od - r_pl < POCKET_WALL_MIN:
        problems.append(f"pocket wall (ring_od - ring_pocket_l)/2 = {r_od - r_pl:.3f} < {POCKET_WALL_MIN}")
    if r_od - x_flat < POCKET_WALL_MIN:
        problems.append(f"pocket wall at the flats (ring_od - ring_pocket_w)/2 = {r_od - x_flat:.3f} < {POCKET_WALL_MIN}")
    if p.ring_stem_slot_w <= 0 or p.ring_stem_slot_w / 2 >= r_pl:
        problems.append(f"ring_stem_slot_w {p.ring_stem_slot_w} is not a slot in the wall")
    if z_slot_top <= z0 + 0.5:
        problems.append(f"stem slot top z_stem + 1.2 = {z_slot_top:.3f} is not above the ring bottom {z0}")
    if z_slot_top < p.z_stem + 0.45 + 0.2:
        problems.append("stem slot does not clear a 0.9 stem")
    if p.ring_key_len > p.ring_h + 1e-6 or p.ring_key_len <= 0:
        problems.append(f"ring_key_len {p.ring_key_len} is not within the ring height {p.ring_h:.3f}")
    if p.ring_key_h <= 0 or p.ring_key_w <= 0 or p.ring_key_w / 2 >= r_od:
        problems.append("ring key has no size")
    if problems:
        raise ValueError(f"{PART}: geometry impossible with params.py:\n  " + "\n  ".join(problems))

    lip = lip_wall >= LIP_WALL_MIN
    if lip:
        if r_pl > r_rec_req - OVERLAP:
            raise ValueError(f"{PART}: dial recess O {p.ring_dial_recess_d:.2f} is not larger than the movement "
                             f"pocket O {p.ring_pocket_l:.2f} by 2 x {OVERLAP}")
        z_top = z1
        r_rec = r_rec_req
    else:
        z_top = z_step
        r_rec = None
    if z_slot_top > z_top - COPLANAR_SNAP:
        z_slot_cut_top = z_top + OVERRUN         # slot runs out through the top face
    elif abs(z_slot_top - z_step) < COPLANAR_SNAP:
        z_slot_cut_top = z_step                  # exactly on the step plane, never 0.01 off it
    else:
        z_slot_cut_top = z_slot_top
    return dict(r_od=r_od, z0=z0, z1=z1, z_step=z_step, z_top=z_top, r_pl=r_pl, x_flat=x_flat,
                r_rec=r_rec, r_rec_requested=r_rec_req, lip=lip, lip_wall=lip_wall,
                z_slot_top=z_slot_top, z_slot_cut_top=z_slot_cut_top, slot_w=p.ring_stem_slot_w,
                key_w=p.ring_key_w, key_h=p.ring_key_h, key_len=p.ring_key_len)


# --------------------------------------------------------------------------- part
def build(p: Params) -> cq.Workplane:
    """ONE valid solid in assembly coordinates (see the module docstring for the lip fallback)."""
    WARNINGS[:] = [w for w in WARNINGS if not w.startswith(p.profile + ":")]
    g = geometry(p)
    r_od, z0, z_top, z_step = g["r_od"], g["z0"], g["z_top"], g["z_step"]
    if not g["lip"]:
        _warn(p, f"dial-recess lip would be {g['lip_wall']:.3f} mm (ring_od {p.ring_od:.2f} vs "
                 f"ring_dial_recess_d {p.ring_dial_recess_d:.2f}) < {LIP_WALL_MIN}: ring built without its "
                 f"upper section, top at the dial seat z = {z_step:.2f} instead of z_ledge_bottom {g['z1']:.2f}; "
                 f"fix params.py (dial_d <= {p.ring_od - 2 * 0.5 - 0.2:.1f} or a larger movement bore)")

    # 1. body: plain cylinder z0 .. z_top
    wp = _cylinder_z(r_od, z0, z_top)
    _check(wp, "body")

    # 2. anti-rotation key on the OD at -X, fused first while the wall behind it is still solid.
    #    Box reaches OVERLAP into the wall; bottom face exactly on the ring bottom plane.
    key = _box(-(r_od + g["key_h"]), -(r_od - OVERLAP), -g["key_w"] / 2, g["key_w"] / 2, z0, z0 + g["key_len"])
    wp = _check(wp.union(key), "key")

    # 3. dial recess from the step plane up through the top (only when a real lip remains).
    #    The tool's floor at z_step is inside solid material at this point: a clean cut.
    if g["lip"]:
        wp = _check(wp.cut(_cylinder_z(g["r_rec"], z_step, z_top + OVERRUN)), "dial recess")

    # 4. movement pocket, open at the bottom, up to the step. The tool overruns the bottom face and reaches
    #    OVERLAP above the step into the recess (or above the top), so no face is near another face.
    wp = _check(wp.cut(outline_tool(g["r_pl"], g["x_flat"], z0 - OVERRUN, z_step + OVERLAP)), "movement pocket")

    # 5. stem slot at +X through the wall, from the bottom to z_stem + 1.2. Inner end sits OVERLAP inside the
    #    pocket void, outer end OVERRUN outside the OD; the top face is either exactly on the step plane,
    #    well inside the lip, or out through the top (see geometry()).
    slot = _box(g["x_flat"] - OVERLAP, r_od + OVERRUN, -g["slot_w"] / 2, g["slot_w"] / 2,
                z0 - OVERRUN, g["z_slot_cut_top"])
    wp = _check(wp.cut(slot), "stem slot")
    return wp


def bom(p: Params) -> list[dict]:
    """Raw stock for the machinist (titanium build only; the resin build is printed from the STL)."""
    if p.profile != "titanium":
        return []
    g = geometry(p)
    h = g["z_top"] - g["z0"]
    return [{
        "item": "spacer ring raw stock",
        "spec": (f"POM-C (acetal copolymer) round bar O 30 x 20 mm, black or natural; finish O {p.ring_od:.2f} x "
                 f"{h:.2f} tall, movement pocket {p.ring_pocket_w:.2f} x {p.ring_pocket_l:.2f}, "
                 f"stem slot {p.ring_stem_slot_w:.1f} wide, key {p.ring_key_w:.1f} x {p.ring_key_h:.1f} x "
                 f"{p.ring_key_len:.1f}; general tolerance +-0.05"),
        "qty": 1,
        "source": "engineering-plastics stockist (POM-C / Delrin rod)",
    }]


# --------------------------------------------------------------------------- self-test
def expected_volume(p: Params, n: int = 20001) -> float:
    """Closed-form / quadrature volume of the ring described by params.py, independent of the CAD kernel."""
    import numpy as np
    _trapz = getattr(np, "trapezoid", None) or np.trapz      # numpy 2 renamed trapz
    g = geometry(p)
    r_od, z0, z_top, z_step = g["r_od"], g["z0"], g["z_top"], g["z_step"]
    v = math.pi * r_od ** 2 * (z_top - z0)
    if g["lip"]:
        v -= math.pi * g["r_rec"] ** 2 * (z_top - z_step)
    v -= outline_area(g["r_pl"], g["x_flat"]) * (z_step - z0)
    # stem slot, lower band: between the pocket boundary and the OD
    w = g["slot_w"] / 2
    y = np.linspace(-w, w, n)
    x_out = np.sqrt(np.clip(r_od ** 2 - y ** 2, 0, None))
    x_in = np.minimum(g["x_flat"], np.sqrt(np.clip(g["r_pl"] ** 2 - y ** 2, 0, None)))
    z_slot_top = min(g["z_slot_top"], z_top)
    v -= float(_trapz(np.clip(x_out - x_in, 0, None), y)) * (min(z_slot_top, z_step) - z0)
    if g["lip"] and z_slot_top > z_step:      # upper band: through the lip only
        x_rec = np.sqrt(np.clip(g["r_rec"] ** 2 - y ** 2, 0, None))
        v -= float(_trapz(np.clip(x_out - x_rec, 0, None), y)) * (z_slot_top - z_step)
    # key: box outside the OD arc
    kw = g["key_w"] / 2
    yk = np.linspace(-kw, kw, n)
    v += float(_trapz((r_od + g["key_h"]) - np.sqrt(r_od ** 2 - yk ** 2), yk)) * g["key_len"]
    return v


def probe_points(p: Params) -> tuple[list, list]:
    """(must_be_empty, must_be_solid) labelled points for the verifier / self-test."""
    g = geometry(p)
    r_od, z0, z_top, z_step = g["r_od"], g["z0"], g["z_top"], g["z_step"]
    y_wall = (g["r_pl"] + r_od) / 2                       # inside the wall at 12 o'clock
    x_slot = (g["x_flat"] + r_od) / 2
    x_key = -(r_od + g["key_h"] / 2)
    empty = [
        ("pocket centre", (0.0, 0.0, z0 + 0.5)),
        ("pocket at the +Y end", (0.0, g["r_pl"] - 0.2, z0 + 0.5)),
        ("outside the flat (still pocket)", (g["x_flat"] - 0.2, 3.0, z0 + 0.5)),
        ("stem slot", (x_slot, 0.0, z0 + 0.5)),
        ("stem passes the slot", (x_slot, 0.0, p.z_stem)),
        ("above the step (recess or open top)", (0.0, y_wall, z_step + 0.2)),
        ("above the key", (x_key, 0.0, z0 + g["key_len"] + 0.2)),
        ("beside the key", (x_key, g["key_w"] / 2 + 0.3, z0 + g["key_len"] / 2)),
    ]
    solid = [
        ("wall at 12 o'clock", (0.0, y_wall, z0 + 0.5)),
        ("wall at 6 o'clock", (0.0, -y_wall, z0 + 0.5)),
        ("wall beyond the flat at 9 o'clock", (-(g["x_flat"] + r_od) / 2, 0.0, z0 + 0.5)),
        ("step, just under the seat", (0.0, y_wall, z_step - 0.15)),
        ("wall beside the stem slot", (x_slot, g["slot_w"] / 2 + 0.3, z0 + 0.5)),
        ("key", (x_key, 0.0, z0 + g["key_len"] / 2)),
    ]
    if g["lip"]:
        solid.append(("rehaut lip", (0.0, (g["r_rec"] + r_od) / 2, (z_step + z_top) / 2)))
        empty.append(("dial recess", (0.0, g["r_rec"] - 0.3, (z_step + z_top) / 2)))
        if g["z_slot_top"] < z_top - 0.3:
            solid.append(("lip above the stem slot", ((g["r_rec"] + r_od) / 2, 0.0, (g["z_slot_top"] + z_top) / 2)))
    return empty, solid


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    failures = 0
    seen_warnings: list[str] = []
    # the two real profiles, then both again with a dial small enough for a real rehaut lip (exercises the lip path)
    cases = [("titanium", {}), ("resin", {}), ("titanium", {"dial_d": 24.6}), ("resin", {"dial_d": 24.6})]
    for prof, over in cases:
        p = params.get(prof, **over)
        tag = prof + ("" if not over else "_lipcheck")
        g = geometry(p)
        print(f"[{tag}] ring_od {p.ring_od:.2f} recess {p.ring_dial_recess_d:.2f} lip {g['lip_wall']:+.3f} "
              f"-> {'lip' if g['lip'] else 'NO LIP (fallback)'}; pocket {p.ring_pocket_w:.2f} x {p.ring_pocket_l:.2f}, "
              f"step z {g['z_step']:.2f}, top z {g['z_top']:.2f}, slot top z {g['z_slot_top']:.2f}")
        wp = build(p)
        seen_warnings += [f"[{tag}] {w}" for w in WARNINGS if w.startswith(prof + ":")]
        s = wp.val()
        assert wp.solids().size() == 1 and s.isValid()
        vol = s.Volume()
        vexp = expected_volume(p)
        bb = s.BoundingBox()
        print(f"  volume {vol:.2f} mm^3 (expected {vexp:.2f}, {100 * (vol - vexp) / vexp:+.3f} %)  mass "
              f"{vol * p.density_ring / 1000:.2f} g  bbox x {bb.xmin:.3f}..{bb.xmax:.3f} y {bb.ymin:.3f}..{bb.ymax:.3f} "
              f"z {bb.zmin:.3f}..{bb.zmax:.3f}")
        assert abs(vol - vexp) / vexp < 2e-3, "volume disagrees with the closed-form value"
        assert abs(bb.zmin - g["z0"]) < 0.01 and abs(bb.zmax - g["z_top"]) < 0.01
        # the stem slot removes the OD at y = 0 unless the lip continues above it
        x_max = g["r_od"] if g["z_slot_top"] < g["z_top"] - COPLANAR_SNAP \
            else math.sqrt(g["r_od"] ** 2 - (g["slot_w"] / 2) ** 2)
        assert abs(bb.xmax - x_max) < 0.01, f"xmax {bb.xmax:.3f} != {x_max:.3f} (stem slot missing?)"
        assert abs(bb.ymax - g["r_od"]) < 0.01 and abs(bb.ymin + g["r_od"]) < 0.01
        assert abs(bb.xmin + (g["r_od"] + g["key_h"])) < 0.01, "key missing on the OD at -X"
        empty, solid = probe_points(p)
        for name, pt in empty:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if not ins else 'BAD'} empty  {name:36s} {tuple(round(v, 2) for v in pt)}")
            failures += int(ins)
        for name, pt in solid:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if ins else 'BAD'} solid  {name:36s} {tuple(round(v, 2) for v in pt)}")
            failures += int(not ins)
        path = os.path.join(out_dir, f"{PART}_{tag}.stl")
        cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
        m = trimesh.load(path)
        print(f"  STL {path}: {len(m.faces)} faces, watertight={m.is_watertight}, mesh volume {m.volume:.2f} mm^3")
        assert m.is_watertight, "STL is not watertight"
        assert abs(m.volume - vol) / vol < 0.01, "mesh volume disagrees with the B-rep volume"
        for row in bom(p):
            print(f"  BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")
    print("WARNINGS:", ("\n  " + "\n  ".join(seen_warnings)) if seen_warnings else "none")
    if failures:
        print(f"FAILED: {failures} probe(s)")
        sys.exit(1)
    print("spacer_ring self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("TERRA_SCRATCH", os.path.join(tempfile.gettempdir(), "terra-proto-selftest")))
    _selftest(_out)
