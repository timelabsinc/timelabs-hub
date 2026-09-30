"""
purchased -- placeholder solids for the bought parts of the Terra-style GL32 case (SPEC.md section 6), for the
assembly STEP and the interference checks, plus the purchasing BOM.

Module contract (SPEC.md section 2): PARTS = {name: builder}, every builder is build_x(p) -> cq.Workplane with
ONE valid solid in assembly coordinates (+X = 3 o'clock, +Y = 12 o'clock, +Z = front, case back face z = 0),
and bom(p) -> list[dict].  MATERIAL / COLORS give build.py a material string and an RGB(A) colour per name.

    crystal         flat sapphire disc O crystal_d x crystal_t, z_crystal_seat .. z_crystal_top
    iring           Hytrel I-ring: annulus ID = crystal_d, wall iring_wall, height iring_h, on the crystal seat.
                    Its OD (crystal_d + 2 * iring_wall) is the UNCOMPRESSED size: it is 0.1 per side larger than
                    the crystal bore by design (the ring is crushed into the bore), so case/iring interferes.
    oring           torus CS oring_cs on mean O oring_groove_mean_d centred at z = -oring_groove_depth/2, i.e.
                    lying in the caseback groove. Uncompressed: its section stands 0.075 proud of z = 0 (the
                    squeeze against the case) and 0.075 into the groove floor; only its own pairs are checked.
    tube            case tube O tube_od x tube_len along +X from x_bore_wall at (y 0, z z_stem), bored TUBE_ID
                    for the stem
    crown           octagonal prism across-flats crown_d (round when crown_octagonal is False) x crown_len from
                    x_crown_start along +X, skirt bored O tube_od + CROWN_SKIRT_CLEARANCE over the tube end
    movement        Miyota GL32 envelope: circle O mvt_l cut by flats at +-mvt_w/2 (the same outline the spacer
                    ring's pocket is cut for), z_mvt_back .. z_mvt_back + mvt_h; centre pipe stub O PIPE_D up to
                    z_dial_seat + mvt_pipe_h, necked to dial_center_hole_d - PIPE_NECK_CLEARANCE where it passes
                    through the dial; stem rod O STEM_D from the +X flat to x_crown_start at (0, z_stem)
    hands_envelope  cylinder O HANDS_D x hands_top_above_dial_face on the dial face (must clear the crystal)
    battery         SR621SW O 6.8 x 2.15, cosmetic, inside the movement envelope at BATTERY_XY (UNVERIFIED
                    position; it overlaps the movement placeholder by design)

Run `python3 parts/purchased.py [out_dir]` from the terra-proto directory for the self-test: every part in
both profiles, one valid solid each, volumes / bounding boxes checked against params.py, pairwise checks
between the purchased parts themselves, STLs exported and checked watertight.
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
from parts.spacer_ring import outline_tool, outline_area   # noqa: E402  (GL32 outline shared with the ring pocket)

OVERLAP = 0.3                 # fused tools reach this far into the solid they join
STEM_D = 0.9                  # tap 10 stem
TUBE_ID = 1.2                 # generic 2.0 mm case tube bore for a 0.9 stem (placeholder, not a catalogue value)
CROWN_SKIRT_CLEARANCE = 0.2   # crown skirt bore = tube_od + this (diametral)
CROWN_HEAD_MIN = 0.8          # solid crown head kept beyond the skirt bore
PIPE_D = 3.0                  # centre pipe stub envelope above the dial (cannon pinion / hour wheel / seconds pipe)
PIPE_NECK_CLEARANCE = 0.2     # neck through the dial = dial_center_hole_d - this
HANDS_D = 20.0                # hands envelope diameter
BATTERY_D = 6.8               # SR621SW
BATTERY_H = 2.15
BATTERY_XY = (-4.0, 2.3)      # cosmetic position inside the outline (around 10 o'clock); UNVERIFIED

MATERIAL = {
    "crystal": "sapphire (flat, polished)",
    "iring": "Hytrel (TPC-ET) I-ring gasket",
    "oring": "NBR 70 Sh O-ring",
    "tube": "316L stainless case tube",
    "crown": "316L stainless push-pull crown (Terra: titanium)",
    "movement": "Miyota GL32 quartz movement (envelope)",
    "hands_envelope": "hands swept volume (not a part)",
    "battery": "SR621SW silver-oxide cell",
}
COLORS = {
    "crystal": (0.70, 0.88, 1.00, 0.35),
    "iring": (0.15, 0.15, 0.15, 1.0),
    "oring": (0.05, 0.05, 0.05, 1.0),
    "tube": (0.72, 0.72, 0.76, 1.0),
    "crown": (0.55, 0.57, 0.62, 1.0),
    "movement": (0.85, 0.72, 0.30, 1.0),
    "hands_envelope": (1.00, 0.45, 0.10, 0.30),
    "battery": (0.80, 0.80, 0.82, 1.0),
}


# --------------------------------------------------------------------------- helpers
def _check(wp: cq.Workplane, step: str) -> cq.Workplane:
    n = wp.solids().size()
    if n != 1:
        raise RuntimeError(f"purchased/{step}: expected 1 solid, got {n}")
    if not wp.val().isValid():
        raise RuntimeError(f"purchased/{step}: solid is not valid")
    return wp


def _cylinder_z(r: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(r).extrude(z1 - z0)


def _cylinder_x(r: float, y: float, z: float, x0: float, x1: float) -> cq.Workplane:
    """Cylinder about an X-parallel axis through (y, z), from x0 to x1 (YZ workplane: local (x, y) = global (Y, Z))."""
    return cq.Workplane("YZ", origin=(x0, 0, 0)).center(y, z).circle(r).extrude(x1 - x0)


def tube_id(p: Params) -> float:
    return min(TUBE_ID, p.tube_od - 0.5)


def crown_skirt_depth(p: Params) -> float:
    """How deep the crown is bored over the tube: past the tube end by OVERLAP, keeping a solid head."""
    x_tube_end = p.x_bore_wall + p.tube_len
    return max(0.0, min(x_tube_end - p.x_crown_start + OVERLAP, p.crown_len - CROWN_HEAD_MIN))


# --------------------------------------------------------------------------- builders
def build_crystal(p: Params) -> cq.Workplane:
    return _check(_cylinder_z(p.crystal_d / 2, p.z_crystal_seat, p.z_crystal_top), "crystal")


def build_iring(p: Params) -> cq.Workplane:
    r_in = p.crystal_d / 2
    wp = (cq.Workplane("XY").workplane(offset=p.z_crystal_seat)
          .circle(r_in + p.iring_wall).circle(r_in).extrude(p.iring_h))
    return _check(wp, "iring")


def build_oring(p: Params) -> cq.Workplane:
    t = cq.Solid.makeTorus(p.oring_groove_mean_d / 2, p.oring_cs / 2,
                           cq.Vector(0, 0, -p.oring_groove_depth / 2), cq.Vector(0, 0, 1))
    return _check(cq.Workplane("XY").newObject([t]), "oring")


def build_tube(p: Params) -> cq.Workplane:
    x0 = p.x_bore_wall
    x1 = x0 + p.tube_len
    wp = (cq.Workplane("YZ", origin=(x0, 0, 0)).center(0, p.z_stem)
          .circle(p.tube_od / 2).circle(tube_id(p) / 2).extrude(x1 - x0))
    return _check(wp, "tube")


def build_crown(p: Params) -> cq.Workplane:
    x0 = p.x_crown_start
    wp = cq.Workplane("YZ", origin=(x0, 0, 0)).center(0, p.z_stem)
    if p.crown_octagonal:
        wp = wp.polygon(8, p.crown_d, circumscribed=True)     # circumscribed: diameter = across-flats
    else:
        wp = wp.circle(p.crown_d / 2)
    wp = _check(wp.extrude(p.crown_len), "crown prism")
    depth = crown_skirt_depth(p)
    if depth > 0:
        bore = _cylinder_x((p.tube_od + CROWN_SKIRT_CLEARANCE) / 2, 0.0, p.z_stem, x0 - OVERLAP, x0 + depth)
        wp = _check(wp.cut(bore), "crown skirt bore")
    return wp


def build_movement(p: Params) -> cq.Workplane:
    z0 = p.z_mvt_back
    z1 = z0 + p.mvt_h                      # = z_dial_seat
    wp = outline_tool(p.mvt_l / 2, p.mvt_w / 2, z0, z1)
    _check(wp, "movement body")
    # centre pipe: neck through the dial, full-size stub above the dial face
    z_pipe_top = p.z_dial_seat + p.mvt_pipe_h
    if p.mvt_pipe_h > 0:
        neck_d = p.dial_center_hole_d - PIPE_NECK_CLEARANCE
        neck_top = min(p.z_dial_face + OVERLAP, z_pipe_top)
        wp = _check(wp.union(_cylinder_z(neck_d / 2, z1 - OVERLAP, neck_top)), "pipe neck")
        if z_pipe_top > p.z_dial_face + 0.1:
            wp = _check(wp.union(_cylinder_z(PIPE_D / 2, p.z_dial_face, z_pipe_top)), "pipe stub")
    # stem rod from inside the +X flat out to the crown
    wp = _check(wp.union(_cylinder_x(STEM_D / 2, 0.0, p.z_stem, p.mvt_w / 2 - OVERLAP, p.x_crown_start)), "stem")
    return wp


def build_hands_envelope(p: Params) -> cq.Workplane:
    return _check(_cylinder_z(HANDS_D / 2, p.z_dial_face, p.z_dial_face + p.hands_top_above_dial_face), "hands")


def build_battery(p: Params) -> cq.Workplane:
    z0 = p.z_mvt_back + (p.mvt_h - BATTERY_H) / 2
    return _check(_cylinder_z(BATTERY_D / 2, z0, z0 + BATTERY_H, *BATTERY_XY), "battery")


PARTS = {
    "crystal": build_crystal,
    "iring": build_iring,
    "oring": build_oring,
    "tube": build_tube,
    "crown": build_crown,
    "movement": build_movement,
    "hands_envelope": build_hands_envelope,
    "battery": build_battery,
}


# --------------------------------------------------------------------------- BOM
def bom(p: Params) -> list[dict]:
    """The real purchasable items behind the placeholders (same for both profiles)."""
    groove_id = p.oring_groove_mean_d - p.oring_groove_w
    oring_id = round(groove_id + 0.1, 1)                       # same rule as caseback.bom
    squeeze = 100 * (p.oring_cs - p.oring_groove_depth) / p.oring_cs
    iring_od = p.crystal_d + 2 * p.iring_wall
    crush = iring_od - p.crystal_bore_d
    hands_r = HANDS_D / 2
    return [
        {
            "item": "crystal, flat sapphire",
            "spec": (f"O {p.crystal_d:.1f} x {p.crystal_t:.1f} mm flat sapphire, polished both faces, plain edge "
                     f"(0.1 mm diameter steps exist; seats {p.crystal_engagement:.1f} below the front face, "
                     f"{p.crystal_proud:.1f} proud); order the I-ring for the same crystal diameter"),
            "qty": 1,
            "source": "watch-parts supplier, generic flat sapphire crystal stock",
        },
        {
            "item": "I-ring gasket, Hytrel, for 2.0 mm flat glass",
            "spec": (f"for crystal O {p.crystal_d:.1f}, wall {p.iring_wall:.2f} x height {p.iring_h:.2f} mm "
                     f"(OD {iring_od:.1f} uncompressed, crush {crush:.2f} diametral in the O {p.crystal_bore_d:.1f} bore)"),
            "qty": 1,
            "source": "watch-parts supplier, Hytrel I-ring for 2.0 mm glass (individual size)",
        },
        {
            "item": "O-ring, caseback face seal",
            "spec": (f"ID {oring_id:.1f} x CS {p.oring_cs:.2f} mm, NBR 70 Sh; groove mean O {p.oring_groove_mean_d} x "
                     f"{p.oring_groove_w} wide x {p.oring_groove_depth} deep ({squeeze:.0f}% squeeze); same item as caseback.bom"),
            "qty": 1,
            "source": "watch-parts supplier, generic 0.50 CS O-ring (0.1 mm ID steps)",
        },
        {
            "item": "case tube, press fit",
            "spec": (f"O {p.tube_od:.1f} OD stainless case tube for a tap 10 (0.90 mm) stem, length >= "
                     f"{p.tube_len:.2f} mm (fitted: {p.tube_engagement:.2f} engaged in the case, {p.tube_protrusion:.1f} "
                     f"protruding into the crown pocket); hole in the case O {p.tube_hole_d:.1f} H7, retain with anaerobic adhesive"),
            "qty": 1,
            "source": "watch-parts supplier, generic 2.0 mm case tube",
        },
        {
            "item": "crown, waterproof push-pull",
            "spec": (f"O {p.crown_d:.1f} mm generic waterproof push-pull crown, tap 10 (0.90 mm), for a {p.tube_od:.1f} mm tube, "
                     f"head ~{p.crown_len:.1f} long, with gasket; the octagonal placeholder is styling only (stock crowns are round)"),
            "qty": 1,
            "source": "watch-parts supplier, generic waterproof crown 4.0 mm / tube 2.0 mm",
        },
        {
            "item": "movement, Miyota GL32",
            "spec": (f"Miyota GL32 quartz, 6 3/4 x 8''' ({p.mvt_w:.1f} x {p.mvt_l:.1f} mm outline, 18.2 per some listings), "
                     f"casing height {p.mvt_h:.2f} mm, 3 hands, no date, stem at 3 o'clock, supplied with stem "
                     f"(cut to length: crown face at x = {p.x_crown_start:.1f} from the case centre)"),
            "qty": 1,
            "source": "Miyota distributor / watch-parts supplier",
        },
        {
            "item": "battery",
            "spec": f"SR621SW (364) silver oxide 1.55 V, O {BATTERY_D:.1f} x {BATTERY_H:.2f} mm",
            "qty": 1,
            "source": "any battery retailer",
        },
        {
            "item": "hands set for Miyota GL30 / GL32",
            "spec": (f"hour pipe O 1.20, minute O 0.70, seconds O 0.17 (0.18 per some suppliers); lengths for the O {p.dial_aperture_d:.1f} "
                     f"aperture within the O {HANDS_D:.0f} hands envelope: minute / seconds <= {hands_r:.0f} mm, hour ~{0.65 * hands_r:.0f} mm; "
                     f"stack height above the dial face <= {p.hands_top_above_dial_face:.1f} mm ({p.hands_clearance:.2f} available to the crystal)"),
            "qty": 1,
            "source": "watch-parts supplier, hands for Miyota GL30/GL32 (hole sizes 1.20 / 0.70 / 0.17)",
        },
        {
            "item": "strap, 22 mm single-pass",
            "spec": (f"{p.strap_w:.0f} mm wide single-pass strap (nylon or thin leather), thickness <= "
                     f"{p.strap_gap - 0.3:.1f} mm to pass the {p.strap_gap:.1f} mm angled channels ({p.strap_slot_w:.1f} wide)"),
            "qty": 1,
            "source": "strap retailer, 22 mm single-pass / single-layer",
        },
    ]


# --------------------------------------------------------------------------- self-test
def _volume(wp: cq.Workplane) -> float:
    return sum(s.Volume() for s in wp.solids().vals())


def _intersection_volume(a: cq.Workplane, b: cq.Workplane) -> float:
    return _volume(a.intersect(b))


def expected_volumes(p: Params) -> dict:
    r_c = p.crystal_d / 2
    v = {
        "crystal": math.pi * r_c ** 2 * p.crystal_t,
        "iring": math.pi * ((r_c + p.iring_wall) ** 2 - r_c ** 2) * p.iring_h,
        "oring": 2 * math.pi ** 2 * (p.oring_groove_mean_d / 2) * (p.oring_cs / 2) ** 2,
        "tube": math.pi / 4 * (p.tube_od ** 2 - tube_id(p) ** 2) * p.tube_len,
        "hands_envelope": math.pi * (HANDS_D / 2) ** 2 * p.hands_top_above_dial_face,
        "battery": math.pi * (BATTERY_D / 2) ** 2 * BATTERY_H,
    }
    if p.crown_octagonal:
        a_crown = 2 * (math.sqrt(2) - 1) * p.crown_d ** 2      # regular octagon, across-flats crown_d
    else:
        a_crown = math.pi * (p.crown_d / 2) ** 2
    v["crown"] = a_crown * p.crown_len - math.pi / 4 * (p.tube_od + CROWN_SKIRT_CLEARANCE) ** 2 * crown_skirt_depth(p)
    body = outline_area(p.mvt_l / 2, p.mvt_w / 2) * p.mvt_h
    z_pipe_top = p.z_dial_seat + p.mvt_pipe_h
    neck_d = p.dial_center_hole_d - PIPE_NECK_CLEARANCE
    pipe = 0.0
    if p.mvt_pipe_h > 0:
        pipe += math.pi / 4 * neck_d ** 2 * (min(p.z_dial_face, z_pipe_top) - p.z_dial_seat)
        if z_pipe_top > p.z_dial_face + 0.1:
            pipe += math.pi / 4 * PIPE_D ** 2 * (z_pipe_top - p.z_dial_face)
    stem = math.pi / 4 * STEM_D ** 2 * (p.x_crown_start - p.mvt_w / 2)
    v["movement"] = body + pipe + stem
    return v


def _selftest(out_dir: str) -> None:
    import trimesh
    os.makedirs(out_dir, exist_ok=True)
    for prof in ("titanium", "resin"):
        p = params.get(prof)
        print(f"[{prof}]")
        built = {}
        vexp = expected_volumes(p)
        for name, builder in PARTS.items():
            wp = builder(p)
            s = wp.val()
            assert wp.solids().size() == 1 and s.isValid(), f"{name}: not one valid solid"
            vol = s.Volume()
            bb = s.BoundingBox()
            print(f"  {name:15s} volume {vol:9.3f} mm^3 (expected {vexp[name]:9.3f}, {100 * (vol - vexp[name]) / vexp[name]:+.3f} %)"
                  f"  bbox x {bb.xmin:7.3f}..{bb.xmax:7.3f}  y {bb.ymin:7.3f}..{bb.ymax:7.3f}  z {bb.zmin:6.3f}..{bb.zmax:6.3f}")
            assert abs(vol - vexp[name]) / vexp[name] < 2e-3, f"{name}: volume disagrees with the closed-form value"
            built[name] = (wp, s, bb, vol)
            path = os.path.join(out_dir, f"purchased_{name}_{prof}.stl")
            cq.exporters.export(wp, path, tolerance=0.005, angularTolerance=0.05)
            m = trimesh.load(path)
            assert m.is_watertight, f"{name}: STL not watertight"
            assert abs(m.volume - vol) / vol < 0.01, f"{name}: mesh volume disagrees"
        # placement checks against params.py
        bb = built["crystal"][2]
        assert abs(bb.zmin - p.z_crystal_seat) < 0.01 and abs(bb.zmax - p.z_crystal_top) < 0.01 and abs(bb.xmax - p.crystal_d / 2) < 0.01
        bb = built["iring"][2]
        assert abs(bb.zmin - p.z_crystal_seat) < 0.01 and abs(bb.zmax - p.z_crystal_seat - p.iring_h) < 0.01
        assert abs(bb.xmax - (p.crystal_d / 2 + p.iring_wall)) < 0.01
        bb = built["oring"][2]
        assert abs(bb.zmin - (-p.oring_groove_depth / 2 - p.oring_cs / 2)) < 0.01
        assert abs(bb.xmax - (p.oring_groove_mean_d + p.oring_cs) / 2) < 0.01
        assert (p.oring_groove_mean_d + p.oring_cs) / 2 <= (p.oring_groove_mean_d + p.oring_groove_w) / 2, "O-ring wider than its groove"
        bb = built["tube"][2]
        assert abs(bb.xmin - p.x_bore_wall) < 0.01 and abs(bb.xmax - (p.x_bore_wall + p.tube_len)) < 0.01
        assert abs(bb.zmin - (p.z_stem - p.tube_od / 2)) < 0.01 and abs(bb.ymax - p.tube_od / 2) < 0.01
        bb = built["crown"][2]
        assert abs(bb.xmin - p.x_crown_start) < 0.01 and abs(bb.xmax - (p.x_crown_start + p.crown_len)) < 0.01
        assert abs(bb.ymax - p.crown_d / 2) < 0.01 and abs(bb.zmax - (p.z_stem + p.crown_d / 2)) < 0.01, "crown across-flats != crown_d"
        bb = built["movement"][2]
        assert abs(bb.zmin - p.z_mvt_back) < 0.01 and abs(bb.zmax - (p.z_dial_seat + p.mvt_pipe_h)) < 0.01
        assert abs(bb.xmin + p.mvt_w / 2) < 0.01 and abs(bb.xmax - p.x_crown_start) < 0.01 and abs(bb.ymax - p.mvt_l / 2) < 0.01
        bb = built["hands_envelope"][2]
        assert abs(bb.zmin - p.z_dial_face) < 0.01 and abs(bb.zmax - (p.z_dial_face + p.hands_top_above_dial_face)) < 0.01
        assert bb.zmax < p.z_crystal_seat, "hands envelope touches the crystal"
        bb = built["battery"][2]
        assert bb.zmin >= p.z_mvt_back - 1e-9 and bb.zmax <= p.z_dial_seat + 1e-9
        s_m = built["movement"][1]
        assert s_m.isInside(cq.Vector(*BATTERY_XY, p.z_mvt_back + p.mvt_h / 2)), "battery centre outside the movement"
        # the pipe neck passes a dial hole of dial_center_hole_d; the stem sits where the case tube hole is
        assert s_m.isInside(cq.Vector(0, 0, p.z_dial_face - 0.05)) and not s_m.isInside(cq.Vector(0, p.dial_center_hole_d / 2, p.z_dial_face - 0.05))
        assert s_m.isInside(cq.Vector((p.mvt_w / 2 + p.x_crown_start) / 2, 0, p.z_stem)), "stem rod missing"
        # pairwise checks among the purchased parts (case-level pairs are verify.py's job)
        pairs = [("crystal", "iring", 0.01), ("crystal", "hands_envelope", 0.01), ("tube", "movement", 0.01),
                 ("crown", "tube", 0.01), ("crown", "movement", 0.01), ("movement", "hands_envelope", None)]
        for a, b, lim in pairs:
            v = _intersection_volume(built[a][0], built[b][0])
            note = "" if lim is None else ("  ok" if v < lim else "  BAD")
            print(f"  {a}/{b} intersection {v:.4f} mm^3{note}")
            if lim is not None:
                assert v < lim, f"{a}/{b} interfere: {v:.4f} mm^3"
        v_bat = _intersection_volume(built["battery"][0], built["movement"][0])
        print(f"  battery inside the movement envelope: {100 * v_bat / built['battery'][3]:.1f} %")
        assert abs(v_bat - built["battery"][3]) < 0.01, "battery is not inside the movement envelope"
        print(f"  hands clearance to the crystal underside: {p.z_crystal_seat - built['hands_envelope'][2].zmax:.2f} mm")
    for row in bom(params.get("titanium")):
        print(f"BOM: {row['item']} | {row['spec']} | x{row['qty']} | {row['source']}")
    print(f"STLs written to {out_dir}")
    print("purchased self-test passed")


if __name__ == "__main__":
    import tempfile
    _out = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("TERRA_SCRATCH", os.path.join(tempfile.gettempdir(), "terra-proto-selftest")))
    _selftest(_out)
