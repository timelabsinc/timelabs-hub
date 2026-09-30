"""
spacer_ring -- POM movement holder / rehaut for the Terra-style GL32 case (SPEC.md section 5, v0.3).

Assembly coordinates (params.py): +X = 3 o'clock (crown), +Y = 12 o'clock, +Z = front; case back face z = 0.

    body    cylinder O ring_od from z = z_cb_inner to z = z_ledge_bottom (height ring_h = 4.0): it sits on the
            caseback inner face and its top face touches the ledge underside, so the caseback clamps it.  The
            height tolerance on the drawing is +ring_axial_preload / +(ring_axial_preload + 0.03), i.e. +0.05/+0.08;
            the model is the nominal ring_h
    pocket  the GL32 outline = circle O ring_pocket_l INTERSECT strip |x| <= ring_pocket_w/2 (flats at
            3 and 9 o'clock), open at the bottom, from the ring bottom up ring_step_h to the dial seat;
            the movement rests on the caseback, the dial rests on the step
    recess  O ring_dial_recess_d (dial_d + 0.4: 25.4 Ti / 25.5 resin) from the step up through the top: the
            wall left around the dial is the visible rehaut lip, ring_lip_wall = (ring_od - ring_dial_recess_d)/2
            = 0.45 Ti / 0.35 resin
    slot    stem slot at +X, width ring_stem_slot_w (in Y), through the wall, from the bottom up to
            z_stem + 1.2, so the movement drops in from the back with its stem fitted
    key     anti-rotation SLOT in the OD at -X (9 o'clock): width key_slot_w (in Y), flat floor key_slot_depth
    slot    inside the OD (at x = -(ring_od/2 - key_slot_depth)), from the ring bottom (open, so the ring slides
            over the rib the case body leaves on its movement-bore wall, SPEC 3.7) up to key_slot_z1.  It stays in
            the thick lower wall (5.35 at -X in Ti): the floor is >= POCKET_WALL_MIN outside the pocket flat and
            the slot never rises above the step, so the rehaut lip above it is untouched.  v0.2's raised key and
            the case's open keyway are gone (ring_key_* no longer exist in params.py).

Lip wall.  The ring is ALWAYS built full height z_cb_inner -> z_ledge_bottom with the rehaut lip above the
step: there is no short-ring fallback and no warning list.  SPEC 5 requires ring_lip_wall >= LIP_WALL_MIN
(0.3); when params.py describes a thinner lip (a dial too large for the movement bore) geometry() raises
ValueError naming ring_od, ring_dial_recess_d, dial_d and the lip they would leave, and nothing is built.
That is fixed in params.py (dial_d <= ring_od - ring_dial_clearance - fit_extra - 2 * LIP_WALL_MIN, or a
larger movement bore); this module never quietly builds a different ring.  The same ValueError (listing every
problem) covers an anti-rotation slot that would reach the pocket, rise into the lip or not clear the case rib.

Boolean order matters for OCC: every feature is a single-tool operation, every tool face is either well
inside solid material or overlaps >= OVERLAP into an existing void, and the only coincident faces are
exact: the step plane, the stem slot top and the anti-rotation slot top when they land on the dial seat
(both are snapped to exact equality when within COPLANAR_SNAP).  With the v0.3 defaults key_slot_z1 ==
z_dial_seat == step plane, and the slot (0.75 deep) is deeper than the lip (0.45 / 0.35), so at the step the
slot's ceiling coincides with the recess floor over its inner 0.30 (Ti) / 0.40 (resin) of depth: a
2.2-wide window from the slot into the dial recess at 9 o'clock, exactly as SPEC 5's numbers describe
(the dial, O 25.0, loses 0.1 x 2.2 of seat there -- noted, harmless).  Every boolean is followed by
_check(): exactly one solid, isValid().

geometry(p) returns the numbers build() uses: r_od, z0, z1, z_step, r_pl, x_flat, r_rec, lip_wall,
z_slot_top, z_slot_cut_top, slot_w (stem slot), key_slot_w, key_slot_depth, x_key_slot, z_key_slot_top,
z_key_slot_cut_top, key_slot_wall, ring_h_tol (z_top == z1 is kept for drawings.py).
outline_tool() / outline_area() are shared with parts/purchased.py for the movement envelope.

Run `python3 parts/spacer_ring.py [out_dir]` from the terra-proto directory for the self-test: both
profiles build, one valid solid each, the volume is checked against a closed-form value, the bounding box
runs exactly z_cb_inner .. z_ledge_bottom and the full OD in X and Y, probe points prove every feature exists
(including the SPEC 8.3b rib path through the slot), STLs are written to out_dir (default: TERRA_SCRATCH or a
directory under the system temp folder) and checked watertight, a dial too large for a 0.3 lip and an
impossible anti-rotation slot must raise ValueError.
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

LIP_WALL_MIN = 0.30     # SPEC 5: thinnest rehaut lip allowed; a thinner lip is a params.py error, not a fallback
POCKET_WALL_MIN = 0.50  # thinnest wall between the movement pocket and the OD / the anti-rotation slot floor
OVERRUN = 1.0           # how far a cutting tool runs past a face it enters through
OVERLAP = 0.3           # how far a tool reaches into an existing void
COPLANAR_SNAP = 0.05    # |dz| below which a tool face is snapped onto an existing face (never near-coplanar)
H_TOL_EXTRA = 0.03      # drawing: ring height +ring_axial_preload / +(ring_axial_preload + this)  (SPEC 5: +0.05/+0.08)


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, step: str) -> cq.Workplane:
    """Every boolean must leave exactly one valid solid."""
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"{PART}/{step}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"{PART}/{step}: solid is not valid")
    return wp


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


def max_dial_d(p: Params) -> float:
    """Largest dial_d that still leaves a LIP_WALL_MIN rehaut lip in this profile."""
    return p.ring_od - p.ring_dial_clearance - p.fit_extra - 2 * LIP_WALL_MIN


# --------------------------------------------------------------------------- geometry
def geometry(p: Params) -> dict:
    """All the numbers build() uses, in assembly coordinates. Raises ValueError, naming the offending
    values, when params.py describes a ring that cannot exist -- first of all a rehaut lip thinner than
    LIP_WALL_MIN. There is no fallback: the ring is always z_cb_inner .. z_ledge_bottom with its lip."""
    r_od = p.ring_od / 2
    z0 = p.z_cb_inner
    z1 = p.z_ledge_bottom
    z_step = z0 + p.ring_step_h
    r_pl = p.ring_pocket_l / 2
    x_flat = p.ring_pocket_w / 2
    r_rec = p.ring_dial_recess_d / 2
    lip_wall = r_od - r_rec
    z_slot_top = p.z_stem + 1.2
    # anti-rotation slot (SPEC 5): flat floor at |x| = x_key_slot, |y| <= key_slot_w/2, open at the bottom, up to key_slot_z1
    x_key_slot = r_od - p.key_slot_depth
    z_key_slot_top = p.key_slot_z1
    key_slot_wall = x_key_slot - min(x_flat, r_pl)     # solid left between the slot floor and the pocket at 9 o'clock

    if lip_wall < LIP_WALL_MIN - 1e-9:
        raise ValueError(
            f"{PART} ({p.profile}): rehaut lip (ring_od {p.ring_od:.2f} - ring_dial_recess_d "
            f"{p.ring_dial_recess_d:.2f}) / 2 = {lip_wall:.3f} mm < {LIP_WALL_MIN:.2f} (SPEC 5): dial_d {p.dial_d:.2f} "
            f"+ ring_dial_clearance {p.ring_dial_clearance:.2f} + fit_extra {p.fit_extra:.2f} does not fit inside a "
            f"ring that itself fits the O {p.mvt_bore_d:.2f} movement bore with {p.ring_od_clearance:.2f} clearance; "
            f"fix params.py: dial_d <= {max_dial_d(p):.2f} or a larger mvt_bore_d")

    problems = []
    if p.ring_h < 0.5:
        problems.append(f"ring height ring_h {p.ring_h:.3f} < 0.5 (z_cb_inner {z0} .. z_ledge_bottom {z1})")
    if p.ring_step_h < 0.5:
        problems.append(f"ring_step_h {p.ring_step_h:.3f} < 0.5: the dial seat is not above the caseback")
    if p.ring_step_h > p.ring_h - 0.5:
        problems.append(f"ring_step_h {p.ring_step_h:.3f} > ring_h {p.ring_h:.3f} - 0.5: no room for the lip above "
                        f"the dial seat")
    if r_od - r_pl < POCKET_WALL_MIN:
        problems.append(f"pocket wall (ring_od - ring_pocket_l)/2 = {r_od - r_pl:.3f} < {POCKET_WALL_MIN}")
    if r_od - x_flat < POCKET_WALL_MIN:
        problems.append(f"pocket wall at the flats (ring_od - ring_pocket_w)/2 = {r_od - x_flat:.3f} < {POCKET_WALL_MIN}")
    if r_pl > r_rec - OVERLAP:
        problems.append(f"dial recess O {p.ring_dial_recess_d:.2f} is not larger than the movement pocket "
                        f"O {p.ring_pocket_l:.2f} by 2 x {OVERLAP}")
    if p.ring_stem_slot_w <= 0 or p.ring_stem_slot_w / 2 >= r_pl:
        problems.append(f"ring_stem_slot_w {p.ring_stem_slot_w} is not a slot in the wall")
    if z_slot_top <= z0 + 0.5:
        problems.append(f"stem slot top z_stem + 1.2 = {z_slot_top:.3f} is not above the ring bottom {z0}")
    if z_slot_top < p.z_stem + 0.45 + 0.2:
        problems.append("stem slot does not clear a 0.9 stem")
    # anti-rotation slot
    if p.key_slot_w <= 0 or p.key_slot_w / 2 >= r_od:
        problems.append(f"key_slot_w {p.key_slot_w:.2f} is not a slot in the OD (ring_od {p.ring_od:.2f})")
    if p.key_slot_depth <= 0:
        problems.append(f"key_slot_depth {p.key_slot_depth:.2f} <= 0: no anti-rotation slot")
    if key_slot_wall < POCKET_WALL_MIN:
        problems.append(f"anti-rotation slot floor at |x| = ring_od/2 - key_slot_depth = {r_od:.3f} - {p.key_slot_depth:.2f} = "
                        f"{x_key_slot:.3f} leaves only {key_slot_wall:.3f} to the movement pocket (flat at |x| = "
                        f"{x_flat:.3f}, radius {r_pl:.3f}) < {POCKET_WALL_MIN} (SPEC 5: the slot stays in the lower wall)")
    if z_key_slot_top <= z0 + 0.5:
        problems.append(f"anti-rotation slot top key_slot_z1 {z_key_slot_top:.3f} is not above the ring bottom {z0}")
    if z_key_slot_top > z_step + COPLANAR_SNAP:
        problems.append(f"anti-rotation slot top key_slot_z1 {z_key_slot_top:.3f} rises above the step {z_step:.3f} into the "
                        f"rehaut lip (SPEC 5: the lip above the slot is untouched; slot depth {p.key_slot_depth:.2f} vs lip "
                        f"{lip_wall:.2f})")
    if p.key_slot_w < p.key_w or p.key_slot_depth < p.key_h or z_key_slot_top < p.key_z1 - 1e-9:
        problems.append(f"anti-rotation slot {p.key_slot_w:.2f} wide x {p.key_slot_depth:.2f} deep up to z {z_key_slot_top:.3f} "
                        f"does not clear the case rib {p.key_w:.2f} x {p.key_h:.2f} up to z {p.key_z1:.3f} (SPEC 3.7): the ring "
                        f"cannot slide over it")
    if problems:
        raise ValueError(f"{PART} ({p.profile}): geometry impossible with params.py:\n  " + "\n  ".join(problems))

    if z_slot_top > z1 - COPLANAR_SNAP:
        z_slot_cut_top = z1 + OVERRUN            # stem slot runs out through the top face
    elif abs(z_slot_top - z_step) < COPLANAR_SNAP:
        z_slot_cut_top = z_step                  # exactly on the step plane, never 0.01 off it
    else:
        z_slot_cut_top = z_slot_top
    if abs(z_key_slot_top - z_step) < COPLANAR_SNAP:
        z_key_slot_cut_top = z_step              # v0.3 default: the slot ceiling IS the step plane
    else:
        z_key_slot_cut_top = z_key_slot_top      # well inside the lower wall
    return dict(r_od=r_od, z0=z0, z1=z1, z_top=z1, z_step=z_step, r_pl=r_pl, x_flat=x_flat, r_rec=r_rec,
                lip_wall=lip_wall, z_slot_top=z_slot_top, z_slot_cut_top=z_slot_cut_top, slot_w=p.ring_stem_slot_w,
                key_slot_w=p.key_slot_w, key_slot_depth=p.key_slot_depth, x_key_slot=x_key_slot,
                z_key_slot_top=z_key_slot_top, z_key_slot_cut_top=z_key_slot_cut_top, key_slot_wall=key_slot_wall,
                ring_h_tol=(p.ring_axial_preload, p.ring_axial_preload + H_TOL_EXTRA))


# --------------------------------------------------------------------------- part
def build(p: Params) -> cq.Workplane:
    """ONE valid solid in assembly coordinates: the full-height ring z_cb_inner .. z_ledge_bottom.
    Raises ValueError (from geometry()) when params.py leaves a rehaut lip < LIP_WALL_MIN or describes an
    impossible anti-rotation slot."""
    g = geometry(p)
    r_od, z0, z1, z_step = g["r_od"], g["z0"], g["z1"], g["z_step"]

    # 1. body: plain cylinder z0 .. z1 (bottom on the caseback inner face, top on the ledge underside)
    wp = _check(_cylinder_z(r_od, z0, z1), "body")

    # 2. dial recess from the step plane up through the top: what is left around it is the rehaut lip.
    #    The tool's floor at z_step is inside solid material at this point: a clean cut.
    wp = _check(wp.cut(_cylinder_z(g["r_rec"], z_step, z1 + OVERRUN)), "dial recess")

    # 3. movement pocket, open at the bottom, up to the step. The tool overruns the bottom face and reaches
    #    OVERLAP above the step into the recess, so no face is near another face.
    wp = _check(wp.cut(outline_tool(g["r_pl"], g["x_flat"], z0 - OVERRUN, z_step + OVERLAP)), "movement pocket")

    # 4. stem slot at +X through the wall, from the bottom to z_stem + 1.2. Inner end sits OVERLAP inside the
    #    pocket void, outer end OVERRUN outside the OD; the top face is either exactly on the step plane,
    #    well inside the lip, or out through the top (see geometry()).
    slot = _box(g["x_flat"] - OVERLAP, r_od + OVERRUN, -g["slot_w"] / 2, g["slot_w"] / 2,
                z0 - OVERRUN, g["z_slot_cut_top"])
    wp = _check(wp.cut(slot), "stem slot")

    # 5. anti-rotation slot in the OD at -X (SPEC 5): box from OVERRUN outside the OD in to the flat floor at
    #    x = -x_key_slot (>= POCKET_WALL_MIN outside the pocket flat), |y| <= key_slot_w/2, from OVERRUN below the
    #    bottom face (open) up to z_key_slot_cut_top: exactly the step plane (v0.3 default) or inside the lower wall.
    kslot = _box(-(r_od + OVERRUN), -g["x_key_slot"], -g["key_slot_w"] / 2, g["key_slot_w"] / 2,
                 z0 - OVERRUN, g["z_key_slot_cut_top"])
    wp = _check(wp.cut(kslot), "anti-rotation slot")
    return wp


def bom(p: Params) -> list[dict]:
    """Raw stock for the machinist (titanium build only; the resin build is printed from the STL)."""
    if p.profile != "titanium":
        return []
    g = geometry(p)                              # same ValueError as build() for an impossible ring
    lo, hi = g["ring_h_tol"]
    return [{
        "item": "spacer ring raw stock",
        "spec": (f"POM-C (acetal copolymer) round bar O 30 x 20 mm, black or natural; finish O {p.ring_od:.2f} x "
                 f"{p.ring_h:.2f} (+{lo:.2f}/+{hi:.2f}) tall, movement pocket {p.ring_pocket_w:.2f} x {p.ring_pocket_l:.2f} "
                 f"from the bottom {p.ring_step_h:.2f} deep, dial recess O {p.ring_dial_recess_d:.2f} from the top (lip "
                 f"{p.ring_lip_wall:.2f}), stem slot {p.ring_stem_slot_w:.1f} wide at 3 o'clock, anti-rotation slot "
                 f"{p.key_slot_w:.2f} wide x {p.key_slot_depth:.2f} deep in the OD at 9 o'clock, open at the bottom, "
                 f"{g['z_key_slot_top'] - g['z0']:.2f} tall; general tolerance +-0.05"),
        "qty": 1,
        "source": "engineering-plastics stockist (POM-C / Delrin rod)",
    }]


# --------------------------------------------------------------------------- self-test
def expected_volume(p: Params, n: int = 20001) -> float:
    """Closed-form / quadrature volume of the ring described by params.py, independent of the CAD kernel."""
    import numpy as np
    _trapz = getattr(np, "trapezoid", None) or np.trapz      # numpy 2 renamed trapz
    g = geometry(p)
    r_od, z0, z1, z_step = g["r_od"], g["z0"], g["z1"], g["z_step"]
    v = math.pi * r_od ** 2 * (z1 - z0)
    v -= math.pi * g["r_rec"] ** 2 * (z1 - z_step)                    # dial recess
    v -= outline_area(g["r_pl"], g["x_flat"]) * (z_step - z0)         # movement pocket
    # stem slot: what the tool actually removes ends at z_slot_cut_top (clipped to the top face)
    w = g["slot_w"] / 2
    y = np.linspace(-w, w, n)
    x_out = np.sqrt(np.clip(r_od ** 2 - y ** 2, 0, None))
    x_in = np.minimum(g["x_flat"], np.sqrt(np.clip(g["r_pl"] ** 2 - y ** 2, 0, None)))
    z_slot_top = min(g["z_slot_cut_top"], z1)
    v -= float(_trapz(np.clip(x_out - x_in, 0, None), y)) * (min(z_slot_top, z_step) - z0)   # lower band: wall
    if z_slot_top > z_step:                                                                  # upper band: lip only
        x_rec = np.sqrt(np.clip(g["r_rec"] ** 2 - y ** 2, 0, None))
        v -= float(_trapz(np.clip(x_out - x_rec, 0, None), y)) * (z_slot_top - z_step)
    # anti-rotation slot: OD arc minus the flat floor over |y| <= key_slot_w/2, bottom .. z_key_slot_cut_top (<= step,
    # so it never meets the recess or the pocket: disjoint from every other removal)
    kw = g["key_slot_w"] / 2
    yk = np.linspace(-kw, kw, n)
    xk_out = np.sqrt(np.clip(r_od ** 2 - yk ** 2, 0, None))
    v -= float(_trapz(np.clip(xk_out - g["x_key_slot"], 0, None), yk)) * (g["z_key_slot_cut_top"] - z0)
    return v


def probe_points(p: Params) -> tuple[list, list]:
    """(must_be_empty, must_be_solid) labelled points for the verifier / self-test."""
    g = geometry(p)
    r_od, z0, z1, z_step = g["r_od"], g["z0"], g["z1"], g["z_step"]
    y_wall = (g["r_pl"] + r_od) / 2                       # inside the wall at 12 o'clock
    y_lip = (g["r_rec"] + r_od) / 2                       # inside the rehaut lip
    z_lip = (z_step + z1) / 2
    x_slot = (g["x_flat"] + r_od) / 2
    x_ks = -(r_od - 0.1)                                  # 0.1 inside the OD at 9 o'clock: in the slot / in the lip
    z_ks = g["z_key_slot_top"]
    empty = [
        ("pocket centre", (0.0, 0.0, z0 + 0.5)),
        ("pocket at the +Y end", (0.0, g["r_pl"] - 0.2, z0 + 0.5)),
        ("outside the flat (still pocket)", (g["x_flat"] - 0.2, 3.0, z0 + 0.5)),
        ("stem slot", (x_slot, 0.0, z0 + 0.5)),
        ("stem passes the slot", (x_slot, 0.0, p.z_stem)),
        ("above the step (dial recess)", (0.0, y_wall, z_step + 0.2)),
        ("dial recess at its edge", (0.0, g["r_rec"] - 0.3, z_lip)),
        ("anti-rotation slot, under its top", (x_ks, 0.0, z_ks - 0.3)),
        ("anti-rotation slot, just under its top", (x_ks, 0.0, z_ks - 0.05)),
        ("anti-rotation slot, open at the bottom", (x_ks, 0.0, z0 + 0.1)),
        ("anti-rotation slot, at its floor", (-(g["x_key_slot"] + 0.1), 0.0, z0 + 0.5)),
        ("anti-rotation slot, at its side", (x_ks, g["key_slot_w"] / 2 - 0.05, z0 + 0.5)),
        ("case rib path through the slot (SPEC 8.3b)", (-(p.key_r_in + 0.1), 0.0, (p.key_z0 + p.key_z1) / 2)),
        ("case rib top inner corner clears the slot", (-(p.key_r_in + 0.05), p.key_w / 2 - 0.05, p.key_z1 - 0.05)),
    ]
    solid = [
        ("wall at 12 o'clock", (0.0, y_wall, z0 + 0.5)),
        ("wall at 6 o'clock", (0.0, -y_wall, z0 + 0.5)),
        ("wall beyond the flat at 9 o'clock", (-(g["x_flat"] + r_od) / 2, 0.0, z0 + 0.5)),
        ("step, just under the seat", (0.0, y_wall, z_step - 0.15)),
        ("wall beside the stem slot", (x_slot, g["slot_w"] / 2 + 0.3, z0 + 0.5)),
        ("lip above the anti-rotation slot", (x_ks, 0.0, z_ks + 0.3)),
        ("lip just above the anti-rotation slot", (x_ks, 0.0, z_ks + 0.05)),
        ("OD beside the anti-rotation slot", (x_ks, g["key_slot_w"] / 2 + 0.4, 2.0)),
        ("wall beside the anti-rotation slot", (-(g["x_key_slot"] + r_od) / 2, g["key_slot_w"] / 2 + 0.4, 2.0)),
        ("wall between the slot floor and the pocket", (-(g["x_key_slot"] - 0.3), 0.0, z0 + 0.5)),
        ("rehaut lip at 12 o'clock", (0.0, y_lip, z_lip)),
        ("rehaut lip at 9 o'clock", (-y_lip, 0.0, z_lip)),
        ("rehaut lip just under the top", (0.0, y_lip, z1 - 0.1)),
    ]
    if g["z_slot_top"] < z1 - 0.3:
        solid.append(("lip above the stem slot", (y_lip, 0.0, (g["z_slot_top"] + z1) / 2)))
    return empty, solid


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    failures = 0
    for prof in ("titanium", "resin"):
        p = params.get(prof)
        g = geometry(p)
        print(f"[{prof}] ring_od {p.ring_od:.2f} recess {p.ring_dial_recess_d:.2f} lip {g['lip_wall']:.3f}; pocket "
              f"{p.ring_pocket_w:.2f} x {p.ring_pocket_l:.2f}; z {g['z0']:.2f} .. {g['z1']:.2f} (ring_h {p.ring_h:.2f} "
              f"+{g['ring_h_tol'][0]:.2f}/+{g['ring_h_tol'][1]:.2f}), step z {g['z_step']:.2f}, stem slot top z "
              f"{g['z_slot_top']:.2f}; anti-rotation slot {g['key_slot_w']:.2f} wide x {g['key_slot_depth']:.2f} deep, "
              f"floor |x| {g['x_key_slot']:.3f} ({g['key_slot_wall']:.2f} to the pocket), top z {g['z_key_slot_top']:.2f}")
        assert g["lip_wall"] >= LIP_WALL_MIN - 1e-9 and abs(g["lip_wall"] - p.ring_lip_wall) < 1e-9
        assert abs(p.ring_h - 4.0) < 1e-9 and abs(p.z_cb_inner + p.ring_step_h - p.z_dial_seat) < 1e-9
        # SPEC 5 / task check: the slot floor stays >= 0.5 outside the pocket flat, and the slot clears the case rib
        assert g["x_key_slot"] > g["x_flat"] + 0.5, "anti-rotation slot reaches the pocket wall"
        assert p.key_slot_w > p.key_w and p.key_slot_depth > p.key_h and g["z_key_slot_top"] >= p.key_z1
        assert g["z_key_slot_cut_top"] <= g["z_step"] + 1e-9, "anti-rotation slot rises into the lip"
        wp = build(p)
        s = wp.val()
        assert wp.solids().size() == 1 and s.isValid(), "not one valid solid"
        vol = s.Volume()
        vexp = expected_volume(p)
        bb = s.BoundingBox()
        print(f"  volume {vol:.2f} mm^3 (expected {vexp:.2f}, {100 * (vol - vexp) / vexp:+.3f} %)  mass "
              f"{vol * p.density_ring / 1000:.2f} g  bbox x {bb.xmin:.3f}..{bb.xmax:.3f} y {bb.ymin:.3f}..{bb.ymax:.3f} "
              f"z {bb.zmin:.4f}..{bb.zmax:.4f}")
        assert abs(vol - vexp) / vexp < 2e-3, "volume disagrees with the closed-form value"
        # full height, exactly: bottom on the caseback inner face, top on the ledge underside
        assert abs(bb.zmin - p.z_cb_inner) < 1e-6, f"ring bottom {bb.zmin:.6f} != z_cb_inner {p.z_cb_inner:.6f}"
        assert abs(bb.zmax - p.z_ledge_bottom) < 1e-6, f"ring top {bb.zmax:.6f} != z_ledge_bottom {p.z_ledge_bottom:.6f}"
        assert abs((bb.zmax - bb.zmin) - p.ring_h) < 1e-6, "ring height != ring_h"
        # the stem slot removes the OD at y = 0 unless the lip continues above it
        x_max = g["r_od"] if g["z_slot_top"] < g["z1"] - COPLANAR_SNAP \
            else math.sqrt(g["r_od"] ** 2 - (g["slot_w"] / 2) ** 2)
        assert abs(bb.xmax - x_max) < 0.01, f"xmax {bb.xmax:.3f} != {x_max:.3f} (stem slot missing?)"
        assert abs(bb.ymax - g["r_od"]) < 0.01 and abs(bb.ymin + g["r_od"]) < 0.01
        # nothing stands proud of the OD any more (v0.2 key gone); the lip above the slot still reaches the full OD at -X
        assert abs(bb.xmin + g["r_od"]) < 0.01, f"xmin {bb.xmin:.3f} != -{g['r_od']:.3f} (a key on the OD, or the lip cut?)"
        # the slot really removes its volume: the ring minus the slot's box must equal the ring (nothing left in it)
        kbox = _box(-(g["r_od"] + 0.5), -g["x_key_slot"], -g["key_slot_w"] / 2, g["key_slot_w"] / 2, g["z0"] - 0.5,
                    g["z_key_slot_cut_top"])
        left = wp.intersect(kbox)
        v_left = sum(so.Volume() for so in left.solids().vals()) if left.solids().size() else 0.0
        assert v_left < 1e-6, f"{v_left:.4f} mm^3 of ring left inside the anti-rotation slot"
        empty, solid = probe_points(p)
        for name, pt in empty:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if not ins else 'BAD'} empty  {name:44s} {tuple(round(v, 3) for v in pt)}")
            failures += int(ins)
        for name, pt in solid:
            ins = s.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if ins else 'BAD'} solid  {name:44s} {tuple(round(v, 3) for v in pt)}")
            failures += int(not ins)
        path = os.path.join(out_dir, f"{PART}_{prof}.stl")
        cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
        m = trimesh.load(path)
        print(f"  STL {path}: {len(m.faces)} faces, watertight={m.is_watertight}, mesh volume {m.volume:.2f} mm^3")
        assert m.is_watertight, "STL is not watertight"
        assert abs(m.volume - vol) / vol < 0.01, "mesh volume disagrees with the B-rep volume"
        for row in bom(p):
            print(f"  BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")

    # negative test: a dial too large for a LIP_WALL_MIN lip must raise ValueError naming the numbers (no fallback)
    print("[negative] dials too large for the rehaut lip (params.get overrides only; params.py is untouched):")
    for prof, dial_d in (("titanium", 25.4), ("titanium", 25.6), ("resin", 25.2), ("resin", 25.6), ("resin", 26.0)):
        q = params.get(prof, dial_d=dial_d)
        try:
            build(q)
        except ValueError as e:
            msg = str(e)
            for needle in (f"{q.ring_od:.2f}", f"{q.ring_dial_recess_d:.2f}", f"{q.ring_lip_wall:.3f}", f"{dial_d:.2f}",
                           f"{LIP_WALL_MIN:.2f}", f"{max_dial_d(q):.2f}"):
                assert needle in msg, f"ValueError does not name {needle}: {msg}"
            print(f"    ok  {prof} dial_d {dial_d:.2f} -> lip {q.ring_lip_wall:+.3f}: ValueError: {msg.splitlines()[0][:110]}...")
        else:
            raise AssertionError(f"{prof} dial_d {dial_d} (lip {q.ring_lip_wall:.3f}) built a ring instead of raising")
    # the limit is inclusive: the largest allowed dial still describes a ring with exactly LIP_WALL_MIN of lip
    for prof in ("titanium", "resin"):
        q = params.get(prof)
        q = params.get(prof, dial_d=round(max_dial_d(q), 3))
        geometry(q)
        assert abs(q.ring_lip_wall - LIP_WALL_MIN) < 1e-6
        print(f"    ok  {prof} dial_d {q.dial_d:.2f} -> lip {q.ring_lip_wall:.3f} (the limit) is accepted")
    # negative test: an anti-rotation slot that reaches the pocket, or is smaller than the case rib, must raise too
    print("[negative] impossible anti-rotation slots:")
    for prof, kw, needle in (("titanium", dict(key_slot_clear_h=4.6), "movement pocket"),
                             ("resin", dict(key_slot_clear_h=5.0), "movement pocket"),
                             ("titanium", dict(key_slot_clear_w=-0.3), "cannot slide over"),
                             ("titanium", dict(key_slot_clear_h=-0.2), "cannot slide over")):
        q = params.get(prof, **kw)
        try:
            build(q)
        except ValueError as e:
            msg = str(e)
            assert needle in msg and f"{q.key_slot_depth:.2f}" in msg, f"ValueError does not explain the slot: {msg}"
            print(f"    ok  {prof} {kw} -> ValueError: {[ln for ln in msg.splitlines() if needle in ln][0].strip()[:110]}...")
        else:
            raise AssertionError(f"{prof} {kw} built a ring instead of raising")

    if failures:
        print(f"FAILED: {failures} probe(s)")
        sys.exit(1)
    print("spacer_ring self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("TERRA_SCRATCH", os.path.join(tempfile.gettempdir(), "terra-proto-selftest")))
    _selftest(_out)
