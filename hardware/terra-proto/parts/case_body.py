"""
Case body: the 32 x 44 x 8.3 lug-less slab (SPEC.md section 3, v0.2).

Module contract (SPEC.md section 2):

    PART, MATERIAL
    build(p)  -> cq.Workplane   one valid solid in assembly coordinates
    bom(p)    -> list[dict]     raw-stock line for the machinist only; the crystal,
                                I-ring and every other bought part live in purchased.py

Coordinate system: origin at the case centre on the back face, +X -> 3 o'clock
(crown), +Y -> 12 o'clock, +Z -> front. Back face z = 0, front face z = H.

The feature recipe is applied in the SPEC order. Every boolean is a single-tool
cut followed by a one-solid / isValid check. Edge breaks (SPEC step 12) and the
strap-channel rounds (step 8) are "best effort": when OCC cannot fillet or chamfer
a particular edge set the break is skipped, a warning is printed and the label is
appended to the module-level SKIPPED list, so the solid stays valid. Every value
below is read from params.py; the numbers quoted are the v0.2 titanium defaults.

What the v0.2 parameters produce (SKIPPED stays empty for both profiles):

* Front outline chamfer 0.3 runs all the way round, including the crown pocket:
  the pocket's R 0.3 corner arcs leave a 0.5 straight side wall (crown_pocket_depth
  - crown_pocket_corner_r), longer than the chamfer leg. (v0.1 asked for a 0.4
  chamfer against a 0.3 wall and skipped the five pocket edges.) On the chamfered
  front face the pocket floor edge sits at x 14.9, the side edges at y +-3.8 and the
  corner arcs at R 0.6.
* Back outline chamfer 0.3 runs round the flanks, corner chamfers and chamfer
  notches and stops at the tangent lines of the strap_bar_round (0.8), which the two
  end edges carry instead. The flank, crown-pocket and notch edges are chamfered with
  OCC MakeChamfer; the four back-face / corner-chamfer edges are cut as explicit 45 deg
  wedge prisms instead (_corner_chamfer_wedge_tool). Once the bar round exists those
  edges are G1-continuous, through the round's end arc, with the end-face / corner-
  chamfer vertical edges at (+-12, +-22), and MakeChamfer propagates along tangent
  chains: it used to put an unwanted full-height 0.3 chamfer on all four vertical
  corner edges (0.127 mm^2 missing from every section, end-face flat beside the
  channel 0.5 instead of 0.8). The wedge cut runs into the round with a curved edge
  and mitres with the flank / notch chamfers on its own (the union of two 45 deg
  half-space cuts at a convex corner is the mitre MakeChamfer would draw).
* Strap channels (SPEC 3.8): strap_gap 1.8 at strap_angle_deg 40, bar 4.0 wide on
  the back face (strap_bar_back_w; the v0.1 3.0 bar yielded at ~100 N). Back-face
  opening y 15.2 -> 18.0 (y_strap_open_inner / _outer), end-face opening z 3.356 ->
  5.706 (z_strap_exit_low / _high), bar 3.356 tall on the end face, 2.594 of solid
  above the exit; caseback flange 29.4 keeps flange_to_strap_margin 0.5 inside the
  opening. Channel edges R 0.4, bar outer edge R 0.8.
* Ring keyway (SPEC 3.7): 2.1 wide x 0.7 deep at 9 o'clock, OPEN FROM THE BACK FACE
  (z 0) through the thread zone up to z 3.7 (z_cb_inner + ring_key_len + 0.2); it
  interrupts the caseback thread over its width. Resin (helix): the floor is pushed
  out to r 14.25 (1.0 deep) so it does not sit tangent to the thread groove root; see
  keyway_geometry().
* Crown pocket 7.0 wide x 0.8 deep with R 0.3 vertical corners, tube hole 2.0 (resin
  1.9, reamed) at z_stem 2.68.

Self-test:  python3 parts/case_body.py   builds both profiles, probes the voids and walls (including
            the chamfer around the pocket, the keyway mouth on the back face, the corner-chamfer back
            edge chamfer and the end-face / corner-chamfer vertical edges), checks that no chamfer
            face sits on those vertical edges and that the constant-z section through the ledge zone
            has exactly the analytic outline area, measures the strap channel openings on the solid
            against params, sweeps the spacer ring in from the back through the thread zone, exports
            STL to $TERRA_SCRATCH (or the system temp dir) and checks it is watertight. Exits non-zero
            on any failed check or any skipped edge break.
"""
from __future__ import annotations

import math
import os
import sys
import time

import cadquery as cq
from cadquery import selectors

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import params  # noqa: E402
from parts import threads  # noqa: E402

PART = "case_body"
MATERIAL = {"titanium": "Ti Grade 2", "resin": "ABS-like resin"}

# Edge breaks that could not be applied, as "<profile>: <label>". Filled by build().
SKIPPED: list[str] = []

OVERRUN = 1.0      # how far a through-cutting tool is pushed past a face (mm)
OVERLAP = 0.3      # minimum overlap into an existing void, keeps OCC away from near-tangent faces
STRAP_EXT = 3.0    # strap channel band extended this far past the back face and the end face


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, label: str) -> cq.Workplane:
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"{label}: solid is not valid")
    return wp


def _cut(wp: cq.Workplane, tool, label: str) -> cq.Workplane:
    """Cut one tool (Workplane or Solid) and verify the result."""
    if isinstance(tool, cq.Shape):
        tool = cq.Workplane("XY").newObject([tool])
    return _check(wp.cut(tool), label)


def _volume(wp: cq.Workplane) -> float:
    return wp.solids().val().Volume()


def _cylinder_z(r: float, z0: float, z1: float) -> cq.Workplane:
    """Cylinder about Z from z0 to z1, centred on the Z axis."""
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def _cylinder_x(r: float, y: float, z: float, x0: float, x1: float) -> cq.Workplane:
    """Cylinder about an X-parallel axis through (y, z), from x0 to x1."""
    return cq.Workplane("YZ", origin=(x0, 0, 0)).center(y, z).circle(r).extrude(x1 - x0)


def _box(x0, x1, y0, y1, z0, z1) -> cq.Workplane:
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=(False, False, False)) \
        .translate((x0, y0, z0))


def _skip(p: params.Params, label: str, err: Exception | str) -> None:
    msg = f"{p.profile}: {label}"
    if msg not in SKIPPED:
        SKIPPED.append(msg)
    print(f"WARNING [{PART}/{p.profile}] skipped {label}: {err}", file=sys.stderr)


def _edge_break(wp: cq.Workplane, edges: list, amount: float, kind: str,
                p: params.Params, label: str) -> tuple[cq.Workplane, bool]:
    """Fillet ('round') or chamfer a list of edges. Returns (wp, applied).

    A break that raises, leaves several solids, or produces an invalid or
    implausible solid (volume grew, or shrank by more than 5 %) is rejected and
    the untouched workplane is returned.
    """
    if not edges:
        _skip(p, label, "no edges selected")
        return wp, False
    v0 = _volume(wp)
    try:
        sel = wp.newObject(edges)
        out = sel.fillet(amount) if kind == "round" else sel.chamfer(amount)
        if out.solids().size() != 1 or not out.val().isValid():
            raise RuntimeError("result invalid or split")
        v1 = _volume(out)
        if v1 > v0 + 1e-6 or v1 < 0.95 * v0:
            raise RuntimeError(f"implausible volume change {v0:.2f} -> {v1:.2f}")
    except Exception as e:  # OCC raises StdFail_NotDone / Standard_Failure etc.
        _skip(p, label, e)
        return wp, False
    return out, True


def _tool_break(wp: cq.Workplane, tool: cq.Workplane, amount: float,
                p: params.Params, label: str) -> tuple[cq.Workplane, bool]:
    """An edge break done as a boolean cut with an explicit tool instead of MakeChamfer / MakeFillet.
    Same contract and plausibility rules as _edge_break: a cut that raises, splits or invalidates
    the solid, removes nothing (the tool missed its edge) or removes more than 5 % is rejected and
    the untouched workplane is returned, with the label recorded in SKIPPED."""
    v0 = _volume(wp)
    try:
        out = wp.cut(tool)
        if out.solids().size() != 1 or not out.val().isValid():
            raise RuntimeError("result invalid or split")
        v1 = _volume(out)
        if v1 > v0 - 1e-6 or v1 < 0.95 * v0:
            raise RuntimeError(f"implausible volume change {v0:.2f} -> {v1:.2f} for a {amount} break")
    except Exception as e:  # noqa: BLE001
        _skip(p, label, e)
        return wp, False
    return out, True


def _edges_near(wp: cq.Workplane, points: list[tuple], tol: float = 0.15) -> list:
    """Edges whose centre lies within `tol` (box) of any of the given points."""
    out = []
    for pt in points:
        lo = (pt[0] - tol, pt[1] - tol, pt[2] - tol)
        hi = (pt[0] + tol, pt[1] + tol, pt[2] + tol)
        out.extend(wp.edges(selectors.BoxSelector(lo, hi)).vals())
    return out


def _planar_face_at_z(wp: cq.Workplane, z: float, tol: float = 1e-3) -> cq.Face:
    """The (unique) planar face lying in the plane z = const."""
    hits = []
    for f in wp.faces().vals():
        if f.geomType() != "PLANE":
            continue
        bb = f.BoundingBox()
        if abs(bb.zmin - z) < tol and abs(bb.zmax - z) < tol:
            hits.append(f)
    if len(hits) != 1:
        raise RuntimeError(f"expected one planar face at z={z}, found {len(hits)}")
    return hits[0]


def _outline_edges(wp: cq.Workplane, z: float) -> list:
    """Edges of the outer wire of the planar face at z (the case outline on that face)."""
    return list(_planar_face_at_z(wp, z).outerWire().Edges())


# --------------------------------------------------------------------------- features
def keyway_geometry(p: params.Params) -> dict:
    """SPEC 3.7 ring keyway at 9 o'clock (-X), as cut by build(): an open slot from the back face
    (z 0) through the thread zone up to z1 = z_cb_inner + ring_key_len + 0.2, width kw = ring_key_w + 0.1,
    nominal radial depth kd = ring_key_h + 0.1 from the movement bore, floor at radius r_floor.

    The slot has to start at the back face: the ring is inserted from the back and its key tip
    (ring_od/2 + ring_key_h) is larger than both the thread minor and major radii, so a keyway that
    started at z_cb_inner would be a blind pocket behind the thread that neither the ring nor a cutter
    can reach. The slot therefore interrupts the caseback thread over its width kw.

    Helix profile: when the nominal floor (r_mvt + kd) lands within OVERLAP of the thread groove root
    (major/2 + clearance) the floor is pushed out to root + OVERLAP; a planar floor tangent to the
    helical groove root along a line leaves degenerate faces in OCC. The floor is not a locating surface
    (the ring is located by its OD in the bore), so the extra depth only adds clearance over the key tip.
    """
    r_mvt = p.mvt_bore_d / 2
    kw = p.ring_key_w + 0.1
    kd = p.ring_key_h + 0.1
    r_floor = r_mvt + kd
    if p.thread_model == "helix":
        r_groove = p.cb_thread_major / 2 + p.cb_thread_clearance
        if abs(r_floor - r_groove) < OVERLAP:
            r_floor = r_groove + OVERLAP
    return dict(kw=kw, kd=kd, r_floor=r_floor, depth=r_floor - r_mvt, z0=0.0,
                z1=p.z_cb_inner + p.ring_key_len + 0.2, r_key_tip=p.ring_od / 2 + p.ring_key_h)


def _strap_channel_tool(p: params.Params, sy: int) -> cq.Workplane:
    """SPEC 3.8: straight band of perpendicular thickness strap_gap in the YZ section,
    axis through (y_strap_axis_back, 0) climbing at strap_angle toward the end face,
    modelled as a parallelogram with horizontal ends 3 mm below the back face and past
    the end face, extruded over x in +-strap_slot_w/2."""
    a = p.strap_angle
    c, s = math.cos(a), math.sin(a)
    g = p.strap_gap / 2.0
    ya = p.y_strap_axis_back

    # y of the inboard (+n) and outboard (-n) band edges at height z
    def y_in(z):
        return ya + (z - g * c) * c / s - g * s

    def y_out(z):
        return ya + (z + g * c) * c / s + g * s

    z_lo = -STRAP_EXT
    # height at which the inboard edge has passed the end face by STRAP_EXT
    z_hi = (p.y_end + STRAP_EXT - ya + g * s) * s / c + g * c
    pts = [(y_in(z_lo), z_lo), (y_out(z_lo), z_lo), (y_out(z_hi), z_hi), (y_in(z_hi), z_hi)]
    pts = [(sy * y, z) for (y, z) in pts]
    half = p.strap_slot_w / 2.0
    return cq.Workplane("YZ").polyline(pts).close().extrude(half, both=True)


def _crown_pocket_tool(p: params.Params) -> cq.Workplane:
    """SPEC 3.9: notch in the +X flank, y in +-crown_pocket_w/2, from x_pocket_floor
    outward, full height, vertical corner radii crown_pocket_corner_r."""
    x0 = p.x_pocket_floor
    x1 = p.x_flank + OVERRUN
    tool = _box(x0, x1, -p.crown_pocket_w / 2, p.crown_pocket_w / 2, -OVERRUN, p.H + OVERRUN)
    if p.crown_pocket_corner_r > 0:
        tool = tool.edges("|Z").fillet(p.crown_pocket_corner_r)
    return tool


def _chamfer_notch_tool(p: params.Params, sx: int, sy: int) -> cq.Workplane:
    """SPEC 3.11: box centred on chamfer_mid, chamfer_notch_w along the chamfer face,
    chamfer_notch_depth inward normal to it, chamfer_notch_h up from the back face."""
    mx, my = p.chamfer_mid(sx, sy)
    ox, oy = sx / math.sqrt(2), sy / math.sqrt(2)          # outward normal of the chamfer face
    tx, ty = -oy, ox                                        # along the chamfer face
    across = p.chamfer_notch_depth + OVERRUN                # inward depth + overrun outside
    tool = cq.Workplane("XY").box(p.chamfer_notch_w, across, p.chamfer_notch_h + OVERRUN,
                                  centered=(True, True, False))
    # box local +y == inward after rotation by the angle of t; shift so that it reaches
    # chamfer_notch_depth inward and OVERRUN outward, and starts OVERRUN below the back face
    tool = tool.translate((0, (p.chamfer_notch_depth - OVERRUN) / 2.0, -OVERRUN))
    tool = tool.rotate((0, 0, 0), (0, 0, 1), math.degrees(math.atan2(ty, tx)))
    return tool.translate((mx, my, 0))


def _front_slot_tool(p: params.Params, sx: int, sy: int) -> cq.Workplane:
    """SPEC 3.11: stadium front_slot_len x front_slot_w, blind front_slot_depth from the
    front face, centred on front_slot_center, long axis at front_slot_angle_deg from +X
    (same angle for the 180-degree-symmetric corner pair; mirrored for the other pair)."""
    cx, cy = p.front_slot_center(sx, sy)
    ang = p.front_slot_angle_deg * (1 if sx * sy > 0 else -1)
    z0 = p.H - p.front_slot_depth
    return (cq.Workplane("XY").workplane(offset=z0).center(cx, cy)
            .slot2D(p.front_slot_len, p.front_slot_w, ang).extrude(p.front_slot_depth + OVERRUN))


def _back_groove_tool(p: params.Params, sx: int, sy: int) -> cq.Workplane:
    """SPEC 3.11: stadium on the back face, parallel to Y at x = sx*(W/2 - back_groove_inboard),
    from y = sy*(L/2 - corner_chamfer - 0.5) toward the centre for back_groove_len."""
    x = sx * (p.W / 2 - p.back_groove_inboard)
    y_start = sy * (p.L / 2 - p.corner_chamfer - 0.5)
    y_c = y_start - sy * p.back_groove_len / 2.0
    return (cq.Workplane("XY").workplane(offset=-OVERRUN).center(x, y_c)
            .slot2D(p.back_groove_len, p.back_groove_w, 90).extrude(p.back_groove_depth + OVERRUN))


def _corner_chamfer_wedge_tool(p: params.Params, sx: int, sy: int, d: float) -> cq.Workplane:
    """SPEC 3.12 back outline chamfer (leg d, 45 deg) on the back-face / corner-chamfer edge of corner
    (sx, sy), as a boolean tool rather than a MakeChamfer edge: a triangular prism whose hypotenuse lies
    on the plane  z + n = d  (n = distance inward from the corner chamfer plane), running along the
    whole corner chamfer face and OVERRUN past both of its ends.

    Only the part of the prism with z >= 0 can meet the case, and there it is a strip within d of the
    (extended) corner chamfer plane; OVERRUN (1.0) past the face's ends that strip is outside the flank /
    end face (x > W/2 resp. |y| > L/2 as soon as the overrun exceeds d), so the end caps never cut. The
    cut therefore removes exactly {z + n < d} along the edge: it ends inside the strap_bar_round with a
    curved edge and mitres with the flank chamfer and the chamfer-notch chamfers by itself (at a convex
    corner the union of two 45 deg half-space cuts is the mitre). Through a chamfer notch the prism
    passes through void only (the notch is chamfer_notch_depth >= d deep and chamfer_notch_h >= d high).
    """
    m = OVERRUN
    mx, my = p.chamfer_mid(sx, sy)
    ox, oy = sx / math.sqrt(2), sy / math.sqrt(2)          # outward normal of the chamfer face
    tx, ty = -oy, ox                                        # along the chamfer face
    half = p.corner_chamfer / math.sqrt(2) + OVERRUN        # half of (face length + 2 overrun)
    # cross-section in the (n, z) plane, n along the box-local +y which the rotation below maps to inward:
    # right triangle with the hypotenuse on n + z = d and its right-angle corner OVERRUN outside / below
    tool = (cq.Workplane("YZ").polyline([(-m, -m), (d + m, -m), (-m, d + m)]).close()
            .extrude(half, both=True))
    tool = tool.rotate((0, 0, 0), (0, 0, 1), math.degrees(math.atan2(ty, tx)))
    return tool.translate((mx, my, 0))


# --------------------------------------------------------------------------- build
def build(p: params.Params) -> cq.Workplane:
    """One valid solid of the case body in assembly coordinates."""
    SKIPPED[:] = [s for s in SKIPPED if not s.startswith(p.profile + ":")]
    r_crystal = p.crystal_bore_d / 2
    r_apert = p.dial_aperture_d / 2
    r_mvt = p.mvt_bore_d / 2
    r_tube = p.tube_hole_d / 2

    # 1. slab
    wp = cq.Workplane("XY").box(p.W, p.L, p.H, centered=(True, True, False))
    # 2. corner chamfers (45 deg, leg corner_chamfer) on the four vertical corner edges
    wp = _check(wp.edges("|Z").chamfer(p.corner_chamfer), "corner chamfers")

    # 3. crystal bore from the front face down to the crystal seat, with a lead chamfer
    wp = _cut(wp, _cylinder_z(r_crystal, p.z_crystal_seat, p.H + OVERRUN), "crystal bore")
    lc = p.crystal_bore_lead_chamfer
    if lc > 0:
        # 45 deg chamfer: radius r_crystal at z = H - lc opening to r_crystal + lc on the front face. The cone
        # runs OVERLAP past both ends so its end faces lie inside the bore void / above the part.
        cone = cq.Solid.makeCone(r_crystal - OVERLAP, r_crystal + lc + OVERLAP, lc + 2 * OVERLAP,
                                 cq.Vector(0, 0, p.H - lc - OVERLAP), cq.Vector(0, 0, 1))
        wp = _cut(wp, cone, "crystal bore lead chamfer")

    # 4. dial aperture through the ledge (tool overlaps up into the crystal bore void)
    wp = _cut(wp, _cylinder_z(r_apert, p.z_ledge_bottom, p.z_crystal_seat + OVERLAP), "dial aperture")

    # 5. movement bore, ledge underside down to the caseback inner face
    wp = _cut(wp, _cylinder_z(r_mvt, p.z_cb_inner, p.z_ledge_bottom), "movement bore")

    # 6. caseback thread zone z 0 -> cb_thread_len
    if p.thread_model == "helix":
        wp = threads.cut_internal(wp, p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                                  clearance=p.cb_thread_clearance, z0=0.0, lead_chamfer=0.2)
    else:
        wp = threads.cut_internal_cosmetic(wp, p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                                           z0=0.0, lead_chamfer=0.2)
    wp = _check(wp, "caseback thread zone")

    # 7. ring keyway at 9 o'clock in the movement bore wall: an open slot from the back face through
    #    the thread zone (see keyway_geometry() for why it cannot be blind from z_cb_inner). The tool
    #    starts OVERRUN outside the back face; its inner face sits OVERLAP inside the bore / thread-zone
    #    void; the slot interrupts the caseback thread over its width.
    kg = keyway_geometry(p)
    wp = _cut(wp, _box(-kg["r_floor"], -(r_mvt - OVERLAP), -kg["kw"] / 2, kg["kw"] / 2,
                       -OVERRUN, kg["z1"]), "ring keyway")

    # 8. strap channels, both ends
    for sy in (1, -1):
        wp = _cut(wp, _strap_channel_tool(p, sy), f"strap channel {'12' if sy > 0 else '6'} o'clock")
    # bar outer edge: back face / end face edge over the full flat width, both ends
    bar_edges = _edges_near(wp, [(0, sy * p.y_end, 0) for sy in (1, -1)])
    wp, _ = _edge_break(wp, bar_edges, p.strap_bar_round, "round", p, "strap bar outer edge round")
    # the four channel edges per end (two on the back face, two on the end face)
    pts = []
    for sy in (1, -1):
        pts += [(0, sy * p.y_strap_open_inner, 0), (0, sy * p.y_strap_open_outer, 0),
                (0, sy * p.y_end, p.z_strap_exit_low), (0, sy * p.y_end, p.z_strap_exit_high)]
    ch_edges = _edges_near(wp, pts)
    wp, ok = _edge_break(wp, ch_edges, p.strap_fillet, "round", p, "strap channel edge rounds")
    if not ok:
        # fall back to one edge at a time so that at least the easy ones are rounded
        for e, pt in zip(ch_edges, pts):
            wp, _ = _edge_break(wp, [e], p.strap_fillet, "round", p, f"strap channel edge round at {pt}")

    # 9. crown pocket in the +X flank
    wp = _cut(wp, _crown_pocket_tool(p), "crown pocket")

    # 10. tube hole along X at (0, z_stem), from inside the movement bore out through the pocket floor
    wp = _cut(wp, _cylinder_x(r_tube, 0.0, p.z_stem, r_mvt - OVERRUN, p.x_pocket_floor + OVERRUN), "tube hole")

    # 11. gate details
    for sx, sy in p.gate_corners:
        tag = f"corner ({sx:+d},{sy:+d})"
        if p.front_slot:
            wp = _cut(wp, _front_slot_tool(p, sx, sy), f"front slot {tag}")
        if p.chamfer_notch:
            wp = _cut(wp, _chamfer_notch_tool(p, sx, sy), f"chamfer notch {tag}")
        if p.back_groove:
            wp = _cut(wp, _back_groove_tool(p, sx, sy), f"back groove {tag}")

    # 12. edge breaks: front outline, then back outline (minus the end edges that carry the round)
    if p.edge_chamfer_front > 0:
        wp = _outline_chamfer(wp, p.H, p.edge_chamfer_front, p, "front outline chamfer", lambda e: True)
    if p.edge_chamfer_back > 0:
        d = p.edge_chamfer_back
        y_lim = p.y_end - p.strap_bar_round - 0.05          # drop the strap_bar_round tangent lines
        corner_sum = p.x_flank + p.y_end - p.corner_chamfer  # |x| + |y| on the four corner chamfer planes

        def keep_back(e):
            # Not the round's tangent lines, and not the back-face / corner-chamfer edges: after the bar
            # round those are G1-continuous (through the round's end arc) with the end-face / corner-
            # chamfer vertical edges, and MakeChamfer propagates along tangent chains, which chamfered
            # the vertical edges over their full height. They are cut as wedge prisms below instead.
            c = e.Center()
            return abs(c.y) < y_lim and abs(abs(c.x) + abs(c.y) - corner_sum) > 1e-3

        wp = _outline_chamfer(wp, 0.0, d, p, "back outline chamfer", keep_back)
        for sx in (1, -1):
            for sy in (1, -1):
                wp, _ = _tool_break(wp, _corner_chamfer_wedge_tool(p, sx, sy, d), d, p,
                                    f"back outline chamfer {d} on corner chamfer edge ({sx:+d},{sy:+d})")

    return _check(wp, "case body")


def _outline_chamfer(wp: cq.Workplane, z: float, d: float, p: params.Params, label: str, keep) -> cq.Workplane:
    """Chamfer the outline (outer wire) of the planar face at z by d.

    Tries the whole outline first. If OCC refuses, the plain flank / end / corner-chamfer
    edges (those that `keep` lets through) go as one group, then the edges of each feature
    that bites into the outline (crown pocket, each chamfer notch) as a group and finally
    one by one. Every attempt re-selects its edges from the current solid, so a success
    never leaves stale edges. Whatever still fails is recorded in SKIPPED with the OCC reason.

    `keep` must drop every edge that is tangent-continuous with an edge outside the set:
    OCC's MakeChamfer propagates along G1 chains, so a kept edge that runs smoothly into
    an unkept one chamfers the unkept one as well (build() drops the back-face / corner-
    chamfer edges for exactly this reason and cuts them as wedges).
    """
    def outline():
        return [e for e in _outline_edges(wp, z) if keep(e)]

    def is_plain(e):
        c = e.Center()
        return (abs(abs(c.x) - p.x_flank) < 1e-3 or abs(abs(c.y) - p.y_end) < 1e-3
                or abs(abs(c.x) + abs(c.y) - (p.x_flank + p.y_end - p.corner_chamfer)) < 1e-3)

    def feature_of(e):
        c = e.Center()
        if c.x > p.x_pocket_floor - 0.01 and abs(c.y) < p.crown_pocket_w / 2 + 0.01:
            return "crown pocket edges"
        for sx, sy in p.gate_corners:
            mx, my = p.chamfer_mid(sx, sy)
            if math.hypot(c.x - mx, c.y - my) < p.chamfer_notch_w:
                return f"chamfer notch ({sx:+d},{sy:+d}) edges"
        return "other feature edges"

    def refresh(centers, tol=0.3):
        """Current outline edges nearest to the remembered centres (a neighbouring break may
        have moved or consumed an edge)."""
        cur = outline()
        found = []
        for c in centers:
            best = min(cur, key=lambda e: (e.Center() - c).Length, default=None)
            if best is not None and (best.Center() - c).Length < tol and best not in found:
                found.append(best)
        return found

    wp_all, ok = _edge_break(wp, outline(), d, "chamfer", p, label)
    if ok:
        return wp_all
    SKIPPED.remove(f"{p.profile}: {label}")              # replaced by the finer records below

    groups: dict[str, list] = {"flank/end/corner edges": []}
    for e in outline():
        groups.setdefault("flank/end/corner edges" if is_plain(e) else feature_of(e), []).append(e.Center())
    for name, centers in groups.items():
        if not centers:
            continue
        note = ""
        if name == "crown pocket edges" and p.crown_pocket_depth - p.crown_pocket_corner_r < d:
            note = (f" [straight pocket side wall {p.crown_pocket_depth - p.crown_pocket_corner_r:.2f}"
                    f" < chamfer {d}]")
        wp, ok = _edge_break(wp, refresh(centers), d, "chamfer", p, f"{label} {d} on {name}{note}")
        if ok or len(centers) == 1:
            continue
        group_tag = f"{label} {d} on {name}{note}"
        SKIPPED.remove(f"{p.profile}: {group_tag}")
        rescued, tags = 0, []
        for c in centers:
            edge = refresh([c])
            tag = f"{label} {d} on {name} edge at ({c.x:.1f},{c.y:.1f}){note}"
            tags.append(tag)
            if not edge:
                _skip(p, tag, "edge consumed by a neighbouring break")
                continue
            wp, ok = _edge_break(wp, edge, d, "chamfer", p, tag)
            rescued += int(ok)
        if rescued == 0:                                   # nothing gained: one record for the group
            SKIPPED[:] = [s for s in SKIPPED if s not in {f"{p.profile}: {t}" for t in tags}]
            _skip(p, group_tag, f"no edge of the group could be chamfered ({len(centers)} edges)")
    return wp


def bom(p: params.Params) -> list[dict]:
    """Only the raw stock for the machinist; the crystal, I-ring etc. are in purchased.py."""
    if p.profile == "titanium":
        return [{
            "item": "case body raw stock",
            "spec": f"Ti Grade 2 plate {p.W + 3:.0f} x {p.L + 3:.0f} x {p.H + 2:.1f} mm (finish 32 x 44 x 8.3)",
            "qty": 1,
            "source": "titanium stockist",
        }]
    return []


# --------------------------------------------------------------------------- self-test
def _probe_points(p: params.Params) -> tuple[list, list]:
    """(must_be_empty, must_be_solid) points for the verifier / self-test."""
    a = p.strap_angle
    empty, solid = [], []
    for sy in (1, -1):
        t = 1.5
        empty.append(("strap channel", (0.0, sy * (p.y_strap_axis_back + t * math.cos(a)), t * math.sin(a))))
    empty.append(("crown pocket", ((p.x_pocket_floor + p.x_flank) / 2, 0.0, p.H - 2.0)))
    empty.append(("tube hole", ((r := p.mvt_bore_d / 2) + (p.x_pocket_floor - r) / 2, 0.0, p.z_stem)))
    for sx, sy in p.gate_corners:
        cx, cy = p.front_slot_center(sx, sy)
        empty.append((f"front slot ({sx:+d},{sy:+d})", (cx, cy, p.H - p.front_slot_depth / 2)))
    kg = keyway_geometry(p)
    x_key = -(p.ring_od / 2 + p.ring_key_h / 2)          # mid-height of the ring key, on its path
    # z 0.02: the keyway mouth on the back face itself (SPEC 3.7: the slot is open to the back). The
    # thread lead chamfer cone is only r_minor + 0.18 wide there, inside the key path radius.
    for z in (0.02, p.cb_thread_len / 2, p.z_cb_inner + 0.1, (p.z_cb_inner + kg["z1"]) / 2):
        empty.append((f"ring key path at z {z:.2f}", (x_key, 0.0, z)))
    for sy in (1, -1):
        empty.append((f"ring key path, y {sy * (p.ring_key_w / 2 - 0.05):+.2f} edge", (x_key, sy * (p.ring_key_w / 2 - 0.05), p.cb_thread_len / 2)))
    solid.append(("wall behind the keyway floor", (-(kg["r_floor"] + 0.3), 0.0, p.cb_thread_len / 2)))
    solid.append(("wall beside the keyway in the thread zone", (-(p.cb_thread_major / 2 + 0.6), kg["kw"] / 2 + 0.3, p.cb_thread_len / 2)))
    solid.append(("wall above the keyway top", (x_key, 0.0, kg["z1"] + 0.1)))
    solid.append(("crown-side wall at thread zone", (p.x_pocket_floor - 0.7, 0.0, p.cb_thread_len / 2)))
    solid.append(("ledge", (0.0, (p.dial_aperture_d + p.crystal_bore_d) / 4, (p.z_ledge_bottom + p.z_crystal_seat) / 2)))
    solid.append(("strap bar", (0.0, p.y_end - 1.0, 0.6)))
    solid.append(("wall above channel exit", (0.0, p.y_end - 1.0, p.H - 1.5)))
    # front chamfer around the crown pocket (SPEC 3.12): a 45 deg chamfer of leg d removes every point whose
    # in-plane distance to the pocket wall plus its distance below the front face is < d. Probe d/3 + d/3
    # (inside the chamfer -> empty) and d + d (below it -> solid) at the floor edge, a side wall and a
    # corner arc. Only meaningful when the straight side wall is longer than the chamfer leg.
    d = p.edge_chamfer_front
    xf, w2, r = p.x_pocket_floor, p.crown_pocket_w / 2, p.crown_pocket_corner_r
    if d > 0 and p.crown_pocket_depth - r > d:
        e, s = d / 3, d
        x_side = (xf + r + p.x_flank) / 2                # middle of the straight side wall
        k_e, k_s = (r + e) / math.sqrt(2), (r + s) / math.sqrt(2)
        empty.append(("front chamfer, pocket floor edge", (xf - e, 0.0, p.H - e)))
        empty.append(("front chamfer, pocket side edge +y", (x_side, w2 + e, p.H - e)))
        empty.append(("front chamfer, pocket side edge -y", (x_side, -(w2 + e), p.H - e)))
        empty.append(("front chamfer, pocket corner arc +y", (xf + r - k_e, (w2 - r) + k_e, p.H - e)))
        empty.append(("front chamfer, pocket corner arc -y", (xf + r - k_e, -((w2 - r) + k_e), p.H - e)))
        solid.append(("wall below chamfer, pocket floor", (xf - s, 0.0, p.H - s)))
        solid.append(("wall below chamfer, pocket side +y", (x_side, w2 + s, p.H - s)))
        solid.append(("wall below chamfer, pocket corner +y", (xf + r - k_s, (w2 - r) + k_s, p.H - s)))
    # SPEC 3.2 / 3.12: the four end-face / corner-chamfer vertical edges carry no edge break. Probe 0.1 inside
    # the end-face flat beside the corner (0.05 in from the end face, 0.106 in from the chamfer plane) at
    # mid-height: the back outline chamfer once propagated onto these edges and made this point void.
    for sx in (1, -1):
        for sy in (1, -1):
            solid.append((f"end-face/corner-chamfer vertical edge ({sx:+d},{sy:+d})",
                          (sx * (p.x_flank - p.corner_chamfer - 0.1), sy * (p.y_end - 0.05), p.H / 2)))
    # the intended back outline chamfer on the back-face / corner-chamfer edges (cut as wedges): inside the
    # 45 deg chamfer (n + z = 2d/3 < d -> empty) and below it (n + z = 2d > d -> solid), probed on the flank
    # side of the notch, clear of the notch's own chamfers and of the mitre with the flank chamfer.
    db = p.edge_chamfer_back
    if db > 0:
        along = p.chamfer_notch_w / 2 + 0.5
        for sx in (1, -1):
            for sy in (1, -1):
                mx, my = p.chamfer_mid(sx, sy)
                ux, uy = sx / math.sqrt(2), -sy / math.sqrt(2)        # along the chamfer face toward the flank
                ix, iy = -sx / math.sqrt(2), -sy / math.sqrt(2)       # inward normal of the chamfer face
                e = db / 3
                empty.append((f"back chamfer, corner chamfer edge ({sx:+d},{sy:+d})",
                              (mx + along * ux + e * ix, my + along * uy + e * iy, e)))
                solid.append((f"wall below back chamfer, corner edge ({sx:+d},{sy:+d})",
                              (mx + along * ux + db * ix, my + along * uy + db * iy, db)))
    return empty, solid


def stray_corner_chamfer_faces(sol: cq.Solid, tol: float = 0.01) -> list:
    """Planar faces whose normal bisects an end-face normal and a corner-chamfer normal, (+-sin 22.5,
    +-cos 22.5, 0) for the 45 deg corner chamfers: the faces a chamfer on one of the four end-face /
    corner-chamfer vertical edges would have. SPEC 3.2 / 3.12 call for none, so the list must be empty;
    MakeChamfer produced four (z 0.8 -> 8.1, 4.0 mm^2 each) while the back outline chamfer was allowed to
    propagate from the corner-chamfer back edge through the strap_bar_round's end arc. Returns
    (nx, ny, zmin, zmax, area) per offending face."""
    bis = cq.Vector(1 / math.sqrt(2), 1 + 1 / math.sqrt(2), 0).normalized()
    out = []
    for f in sol.Faces():
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt()
        if abs(n.z) < 1e-3 and abs(abs(n.x) - bis.x) < tol and abs(abs(n.y) - bis.y) < tol:
            bb = f.BoundingBox()
            out.append((round(n.x, 3), round(n.y, 3), round(bb.zmin, 2), round(bb.zmax, 2), round(f.Area(), 3)))
    return out


def ledge_section_area(wp: cq.Workplane, p: params.Params) -> tuple[float, float, float] | None:
    """(z, B-rep area, analytic area) of the constant-z section midway through the ledge (between
    z_ledge_bottom and z_crystal_seat). There the section is the plan outline (the chamfered octagon)
    minus the dial aperture and the crown pocket, so any stray edge break shows up as an area deficit
    (the propagated vertical-edge chamfer took 0.127 mm^2 from every section). None when the parameters
    put another feature into that plane (channel exit, keyway, tube hole, front slot, front chamfer)."""
    z = (p.z_ledge_bottom + p.z_crystal_seat) / 2
    top = p.H - max(p.edge_chamfer_front, p.front_slot_depth if p.front_slot else 0.0)
    bottom = max(p.z_strap_exit_high, keyway_geometry(p)["z1"], p.z_stem + p.tube_hole_d / 2)
    if not bottom < z < top:
        return None
    r = p.crown_pocket_corner_r
    analytic = (p.W * p.L - 2 * p.corner_chamfer ** 2
                - math.pi * (p.dial_aperture_d / 2) ** 2
                - (p.crown_pocket_depth * p.crown_pocket_w - 2 * (1 - math.pi / 4) * r * r))
    area = sum(f.Area() for f in wp.section(z).faces().vals())
    return z, area, analytic


def _measure_strap_openings(sol: cq.Solid, p: params.Params) -> dict:
    """Strap channel openings measured on the built solid, per end ('12' / '6'):
    (y_open_inner, y_open_outer) on the back face and (z_exit_low, z_exit_high) on the end face.

    The channel walls are found by bisection on isInside along a line 1 mm inside each face (clear of
    the strap_fillet and strap_bar_round) and projected onto the face along the channel's own angle
    (the walls are straight at strap_angle, so y shifts by z / tan(a) and z by dy * tan(a))."""
    a = p.strap_angle
    ta, ca = math.tan(a), 1.0 / math.tan(a)

    def wall(p_in: tuple, p_out: tuple) -> cq.Vector:
        lo, hi = cq.Vector(*p_in), cq.Vector(*p_out)
        if not sol.isInside(lo) or sol.isInside(hi):
            raise RuntimeError(f"no solid/empty transition between {p_in} and {p_out}")
        for _ in range(40):
            mid = (lo + hi) * 0.5
            if sol.isInside(mid):
                lo = mid
            else:
                hi = mid
        return (lo + hi) * 0.5

    out = {}
    for sy in (1, -1):
        z = 1.0                                           # back-face opening, measured at z = 1
        y_in = sy * wall((0, sy * (p.y_strap_open_inner + z * ca - 0.5), z),
                         (0, sy * (p.y_strap_open_inner + z * ca + 0.5), z)).y - z * ca
        y_out = sy * wall((0, sy * (p.y_strap_open_outer + z * ca + 0.5), z),
                          (0, sy * (p.y_strap_open_outer + z * ca - 0.5), z)).y - z * ca
        y = p.y_end - 1.0                                 # end-face opening, measured 1 inside the end face
        dz = (p.y_end - y) * ta
        z_lo = wall((0, sy * y, (y - p.y_strap_open_outer) * ta - 0.5),
                    (0, sy * y, (y - p.y_strap_open_outer) * ta + 0.5)).z + dz
        z_hi = wall((0, sy * y, (y - p.y_strap_open_inner) * ta + 0.5),
                    (0, sy * y, (y - p.y_strap_open_inner) * ta - 0.5)).z + dz
        out["12" if sy > 0 else "6"] = dict(y_open_inner=y_in, y_open_outer=y_out, z_exit_low=z_lo, z_exit_high=z_hi)
    return out


if __name__ == "__main__":
    import tempfile
    scratch = os.environ.get("TERRA_SCRATCH") or os.path.join(tempfile.gettempdir(), "terra-proto-selftest")
    os.makedirs(scratch, exist_ok=True)
    failures = 0
    for prof in ("titanium", "resin"):
        p = params.get(prof)
        t0 = time.time()
        wp = build(p)
        dt = time.time() - t0
        sol = wp.solids().val()
        assert wp.solids().size() == 1 and sol.isValid(), f"{prof}: not one valid solid"
        bb = sol.BoundingBox()
        print(f"[{prof}] built in {dt:.1f} s  volume {sol.Volume():.1f} mm^3  "
              f"bbox x {bb.xmin:.2f}..{bb.xmax:.2f} y {bb.ymin:.2f}..{bb.ymax:.2f} z {bb.zmin:.2f}..{bb.zmax:.2f}")
        empty, solid = _probe_points(p)
        for name, pt in empty:
            ins = sol.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if not ins else 'BAD'} empty  {name:34s} {tuple(round(v, 2) for v in pt)}")
            failures += int(ins)
        for name, pt in solid:
            ins = sol.isInside(cq.Vector(*pt))
            print(f"    {'ok ' if ins else 'BAD'} solid  {name:34s} {tuple(round(v, 2) for v in pt)}")
            failures += int(not ins)
        # SPEC 3.2 / 3.12: no chamfer face on the end-face / corner-chamfer vertical edges, and the section
        # through the ledge zone is exactly the analytic outline minus aperture and crown pocket
        stray = stray_corner_chamfer_faces(sol)
        print(f"    {'ok ' if not stray else 'BAD'} no chamfer face on the end-face/corner-chamfer vertical edges"
              f"{'' if not stray else ': ' + str(stray)}")
        failures += int(bool(stray))
        sec = ledge_section_area(wp, p)
        if sec is None:
            print("    (ledge section area check not applicable with these parameters)")
        else:
            z_sec, area, analytic = sec
            ok = abs(area - analytic) < 0.01
            print(f"    {'ok ' if ok else 'BAD'} section at z {z_sec:.2f}: {area:.3f} mm^2 vs analytic outline "
                  f"{analytic:.3f} (diff {area - analytic:+.4f})")
            failures += int(not ok)
        # strap channel numbers (SPEC 3.8): params vs the openings measured on the solid, both ends
        print(f"    strap channel (params): back opening y {p.y_strap_open_inner:.3f} -> {p.y_strap_open_outer:.3f} "
              f"(bar {p.strap_bar_back_w:.3f} wide), end opening z {p.z_strap_exit_low:.3f} -> {p.z_strap_exit_high:.3f} "
              f"(bar {p.strap_bar_end_h:.3f} tall, {p.strap_front_wall:.3f} above), flange margin {p.flange_to_strap_margin:.3f}")
        try:
            meas = _measure_strap_openings(sol, p)
        except Exception as e:  # noqa: BLE001
            print(f"    BAD strap channel measurement failed: {e}")
            failures += 1
            meas = {}
        want = dict(y_open_inner=p.y_strap_open_inner, y_open_outer=p.y_strap_open_outer,
                    z_exit_low=p.z_strap_exit_low, z_exit_high=p.z_strap_exit_high)
        for end, m in meas.items():
            worst = max(abs(m[k] - want[k]) for k in want)
            ok = worst < 0.01
            print(f"    {'ok ' if ok else 'BAD'} strap channel {end:>2s} o'clock (solid): back opening y {m['y_open_inner']:.3f} -> "
                  f"{m['y_open_outer']:.3f}, end opening z {m['z_exit_low']:.3f} -> {m['z_exit_high']:.3f} "
                  f"(max deviation from params {worst:.4f})")
            failures += int(not ok)
        # insertion sweep: the real spacer ring (with its key) pushed in from the back must never touch the
        # case on its way to the seat (dz 0). dz -3.5 puts the key just outside the back face.
        try:
            from parts import spacer_ring
            ring = spacer_ring.build(p)
            kg = keyway_geometry(p)
            print(f"    keyway: {kg['kw']:.2f} wide x {kg['depth']:.2f} deep (floor r {kg['r_floor']:.3f}), z {kg['z0']:.1f} .. {kg['z1']:.2f}; "
                  f"key tip r {kg['r_key_tip']:.3f} vs thread minor r {p.cb_thread_minor / 2:.3f}")
            for dz in (-0.5, -1.0, -1.5, -2.0, -2.5, -3.0, -3.5):
                inter = ring.translate((0, 0, dz)).intersect(wp)
                v = sum(s.Volume() for s in inter.solids().vals())
                ok = v < 0.01
                print(f"    {'ok ' if ok else 'BAD'} ring inserted from the back, shifted {dz:+.1f}: ring/case {v:.4f} mm^3")
                failures += int(not ok)
        except Exception as e:  # noqa: BLE001
            print(f"    BAD insertion sweep failed: {e}")
            failures += 1
        out = os.path.join(scratch, f"{PART}_{prof}.stl")
        cq.exporters.export(wp, out, tolerance=0.01, angularTolerance=0.1)
        print(f"    exported {out}")
        try:
            import trimesh
            m = trimesh.load(out, force="mesh")
            ok = bool(m.is_watertight) and abs(m.volume - sol.Volume()) < 0.01 * sol.Volume()
            print(f"    {'ok ' if ok else 'BAD'} STL watertight={m.is_watertight} volume {m.volume:.1f} mm^3 "
                  f"({len(m.faces)} triangles)")
            failures += int(not ok)
        except ImportError:
            print("    (trimesh not installed: STL watertightness not checked)")
        print(f"    bom: {bom(p)}")
    print("SKIPPED:", SKIPPED if SKIPPED else "none (every edge break applied)")
    if SKIPPED:
        # SPEC 3.12 tolerates a skipped edge break in build(); the self-test does not, because with the
        # v0.2 parameters every break is expected to succeed (see the module docstring).
        print(f"FAILED: {len(SKIPPED)} edge break(s) skipped")
        failures += len(SKIPPED)
    if failures:
        print(f"FAILED: {failures} check(s)")
        sys.exit(1)
    print("all checks passed")
