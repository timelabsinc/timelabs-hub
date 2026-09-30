"""
dial_blank -- flat dial disc for the Terra-style GL32 case (SPEC.md section 6).

Assembly coordinates (params.py): +X = 3 o'clock, +Y = 12 o'clock, +Z = front (the dial face looks toward +Z).

    disc         O dial_d x dial_t, from z = z_dial_seat (resting on the movement / the spacer-ring step)
                 to z = z_dial_face
    centre hole  O dial_center_hole_d for the hand pipes
    feet holes   O dial_foot_d at every (angle, radius) in dial_feet; the angle is measured CLOCKWISE
                 from 12 o'clock (+Y) as seen from the dial side, so 90 deg is 3 o'clock (+X):
                 x = r sin(a), y = r cos(a).  The positions are UNVERIFIED placeholders for the GL32.

The feet themselves are not modelled: on the brass dial they are O dial_foot_d wire pins soldered to the
back at these positions; on the resin fit-check blank the holes take glued-in wire feet.  The disc is
plain: no printing, no applied indices, no date window.

Run `python3 parts/dial_blank.py [out_dir]` from the terra-proto directory for the self-test.
"""
from __future__ import annotations

import math
import os
import sys

import cadquery as cq

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import params                                     # noqa: E402
from params import Params                         # noqa: E402

PART = "dial_blank"
MATERIAL = {"titanium": "brass CZ108 half-hard sheet 0.4 mm", "resin": "ABS-like resin"}

OVERRUN = 0.5           # through-holes run this far past both faces
EDGE_MARGIN = 0.3       # minimum web between a foot hole and the dial edge / the centre hole / another foot
FEET_WARNING = ("dial feet positions and diameter are UNVERIFIED placeholders (params.dial_feet / dial_foot_d): "
                "measure them on the real GL32 (or its official drawing) before soldering feet or cutting a dial")


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, step: str) -> cq.Workplane:
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"{PART}/{step}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"{PART}/{step}: solid is not valid")
    return wp


def foot_xy(angle_deg_cw_from_12: float, radius: float) -> tuple[float, float]:
    """(x, y) of a dial foot given its angle clockwise from 12 o'clock (+Y), seen from the dial side."""
    a = math.radians(angle_deg_cw_from_12)
    return (radius * math.sin(a), radius * math.cos(a))


def feet_xy(p: Params) -> list[tuple[float, float]]:
    return [foot_xy(a, r) for (a, r) in p.dial_feet]


def _check_geometry(p: Params) -> None:
    r = p.dial_d / 2
    r_c = p.dial_center_hole_d / 2
    r_f = p.dial_foot_d / 2
    problems = []
    if p.dial_t <= 0.05:
        problems.append(f"dial_t {p.dial_t} is not a sheet")
    if r_c <= 0 or r_c >= r - EDGE_MARGIN:
        problems.append(f"centre hole O {p.dial_center_hole_d} does not fit the O {p.dial_d} disc")
    pts = feet_xy(p)
    for (a, rad), (x, y) in zip(p.dial_feet, pts):
        if rad + r_f > r - EDGE_MARGIN:
            problems.append(f"foot at {a} deg / r {rad} breaks the dial edge (O {p.dial_d})")
        if rad - r_f < r_c + EDGE_MARGIN:
            problems.append(f"foot at {a} deg / r {rad} runs into the centre hole")
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i], pts[j])
            if d < p.dial_foot_d + EDGE_MARGIN:
                problems.append(f"feet {i} and {j} are only {d:.2f} apart")
    if problems:
        raise ValueError(f"{PART}: geometry impossible with params.py:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------- part
def build(p: Params) -> cq.Workplane:
    """ONE valid solid: the dial disc with its centre hole and feet holes, z = z_dial_seat .. z_dial_face."""
    _check_geometry(p)
    z0 = p.z_dial_seat
    wp = cq.Workplane("XY").workplane(offset=z0).circle(p.dial_d / 2).extrude(p.dial_t)
    _check(wp, "disc")

    def through_hole(x: float, y: float, d: float) -> cq.Workplane:
        return (cq.Workplane("XY").workplane(offset=z0 - OVERRUN).center(x, y).circle(d / 2)
                .extrude(p.dial_t + 2 * OVERRUN))

    wp = _check(wp.cut(through_hole(0.0, 0.0, p.dial_center_hole_d)), "centre hole")
    for k, (x, y) in enumerate(feet_xy(p)):
        wp = _check(wp.cut(through_hole(x, y, p.dial_foot_d)), f"foot hole {k}")
    return wp


def bom(p: Params) -> list[dict]:
    """Dial stock for the build, plus the UNVERIFIED-feet warning as a row so it lands on every BOM."""
    feet = "; ".join(f"{a:.0f} deg cw from 12 at r {r:.1f}" for (a, r) in p.dial_feet)
    rows = []
    if p.profile == "titanium":
        rows.append({
            "item": "dial blank stock",
            "spec": (f"brass CZ108 (CuZn37) half-hard sheet {p.dial_t:.1f} mm, blank >= {p.dial_d + 6:.0f} x "
                     f"{p.dial_d + 6:.0f} mm; finish O {p.dial_d:.1f} disc, centre hole O {p.dial_center_hole_d:.1f}, "
                     f"{len(p.dial_feet)} feet O {p.dial_foot_d:.1f} brass wire soldered at {feet}"),
            "qty": 1,
            "source": "metal stockist / model-engineering supplier (0.4 mm brass sheet); dial printing separately",
        })
        rows.append({
            "item": "dial feet wire",
            "spec": f"brass wire O {p.dial_foot_d:.1f} mm, ~3 mm per foot, soft-soldered to the dial back",
            "qty": len(p.dial_feet),
            "source": "model-engineering supplier",
        })
    else:
        rows.append({
            "item": "dial blank (resin fit-check)",
            "spec": (f"printed with the case set from dial_blank.stl, O {p.dial_d:.1f} x {p.dial_t:.1f}; feet holes "
                     f"O {p.dial_foot_d:.1f} take glued-in brass wire feet at {feet}"),
            "qty": 1,
            "source": "MSLA print (same resin as the case)",
        })
    rows.append({
        "item": "WARNING: dial feet UNVERIFIED",
        "spec": FEET_WARNING,
        "qty": 0,
        "source": "SPEC.md section 12 / params.dial_feet",
    })
    return rows


# --------------------------------------------------------------------------- self-test
def expected_volume(p: Params) -> float:
    r, t = p.dial_d / 2, p.dial_t
    return math.pi * t * (r ** 2 - (p.dial_center_hole_d / 2) ** 2 - len(p.dial_feet) * (p.dial_foot_d / 2) ** 2)


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    failures = 0
    for prof in ("titanium", "resin"):
        p = params.get(prof)
        pts = feet_xy(p)
        print(f"[{prof}] dial O {p.dial_d} x {p.dial_t} at z {p.z_dial_seat:.2f}..{p.z_dial_face:.2f}; feet "
              + ", ".join(f"({a:.0f} deg, r {r}) -> ({x:+.2f}, {y:+.2f})" for (a, r), (x, y) in zip(p.dial_feet, pts)))
        # the clockwise-from-12 convention: 90 deg must land on +X (3 o'clock)
        assert abs(foot_xy(90.0, 5.0)[0] - 5.0) < 1e-9 and abs(foot_xy(90.0, 5.0)[1]) < 1e-9
        assert abs(foot_xy(0.0, 5.0)[1] - 5.0) < 1e-9
        wp = build(p)
        s = wp.val()
        assert wp.solids().size() == 1 and s.isValid()
        vol = s.Volume()
        vexp = expected_volume(p)
        bb = s.BoundingBox()
        print(f"  volume {vol:.3f} mm^3 (expected {vexp:.3f}, {100 * (vol - vexp) / vexp:+.3f} %)  bbox x {bb.xmin:.3f}.."
              f"{bb.xmax:.3f} y {bb.ymin:.3f}..{bb.ymax:.3f} z {bb.zmin:.3f}..{bb.zmax:.3f}")
        assert abs(vol - vexp) / vexp < 1e-3
        assert abs(bb.zmin - p.z_dial_seat) < 0.01 and abs(bb.zmax - p.z_dial_face) < 0.01
        assert abs(bb.xmax - p.dial_d / 2) < 0.01 and abs(bb.ymin + p.dial_d / 2) < 0.01
        zc = (p.z_dial_seat + p.z_dial_face) / 2
        empty = [("centre hole", (0.0, 0.0, zc))] + [(f"foot hole {k}", (x, y, zc)) for k, (x, y) in enumerate(pts)]
        solid = [("disc edge", (p.dial_d / 2 - 0.2, 0.0, zc)), ("web beside foot 0", (pts[0][0] + p.dial_foot_d / 2 + 0.2, pts[0][1], zc)),
                 ("web at 12 o'clock", (0.0, p.dial_d / 4, zc))]
        for name, pt in empty:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if not ins else 'BAD'} empty  {name:20s} {tuple(round(v, 2) for v in pt)}")
            failures += int(ins)
        for name, pt in solid:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if ins else 'BAD'} solid  {name:20s} {tuple(round(v, 2) for v in pt)}")
            failures += int(not ins)
        path = os.path.join(out_dir, f"{PART}_{prof}.stl")
        cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
        m = trimesh.load(path)
        print(f"  STL {path}: {len(m.faces)} faces, watertight={m.is_watertight}, mesh volume {m.volume:.3f} mm^3")
        assert m.is_watertight and abs(m.volume - vol) / vol < 0.01
        for row in bom(p):
            print(f"  BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")
    if failures:
        print(f"FAILED: {failures} probe(s)")
        sys.exit(1)
    print("dial_blank self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("TERRA_SCRATCH", os.path.join(tempfile.gettempdir(), "terra-proto-selftest")))
    _selftest(_out)
