"""
Helical thread geometry for CadQuery. Used only when Params.thread_model == "helix"
(resin fit-check prints). For the titanium profile the parts export plain cosmetic
bores and the drawing carries the thread callout.

All threads are right-hand, axis +Z, running from z=z0 to z=z0+length.

    teeth(r_in, r_out, pitch, length, ...)       -> cq.Solid : helical tooth ring grown outward from r_in
    boss(major, pitch, length, clearance, z0)    -> cq.Workplane : single fused threaded boss
    cut_internal(wp, major, pitch, length, ...)  -> cq.Workplane : wp with an internal thread cut into it
    boss_cosmetic / cut_internal_cosmetic        : plain cylinders / bores for the machinist

Implementation note: OCC booleans fail silently when a tool face lies within a few
hundredths of a millimetre of an existing face, so the tooth solid is grown 0.3 mm
past its root radius and the internal thread is cut teeth-first, then bore, then
lead chamfer, as separate single-solid operations. verify.py re-checks the fit.
"""
from __future__ import annotations

import cadquery as cq

OVERLAP = 0.30  # radial overlap: keeps the tooth inner face well away from the bore/core face (OCC booleans fail on near-tangent faces)


def minor_dia(major: float, pitch: float) -> float:
    """ISO metric internal minor diameter D1 = D - 1.0825 P."""
    return major - 1.0825 * pitch


def _trapezoid_wire(r_in: float, r_out: float, pitch: float, crest_frac: float, root_frac: float) -> cq.Wire:
    """Closed trapezoid in the plane y=0 (x = radius, z = axial), centred on z=0."""
    cw = pitch * crest_frac / 2
    rw = pitch * root_frac / 2
    pts = [cq.Vector(r_in, 0, -rw), cq.Vector(r_out, 0, -cw), cq.Vector(r_out, 0, cw), cq.Vector(r_in, 0, rw)]
    return cq.Wire.makePolygon(pts, close=True)


def teeth(r_in: float, r_out: float, pitch: float, length: float,
          crest_frac: float = 0.125, root_frac: float = 0.55, run_out: float = 1.0) -> cq.Solid:
    """Helical tooth solid from z=0 to z=length (trimmed), growing radially from r_in-OVERLAP to r_out."""
    extra = run_out * pitch
    helix = cq.Wire.makeHelix(pitch=pitch, height=length + 2 * extra, radius=r_in,
                              center=cq.Vector(0, 0, -extra))
    profile = _trapezoid_wire(r_in - OVERLAP, r_out, pitch, crest_frac, root_frac).translate(cq.Vector(0, 0, -extra))
    swept = cq.Solid.sweep(profile, [], helix, makeSolid=True, isFrenet=True)
    trim = cq.Solid.makeCylinder(r_out + 1.0, length, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    return swept.intersect(trim)


def boss(major: float, pitch: float, length: float, clearance: float = 0.0, z0: float = 0.0,
         minor: float | None = None) -> cq.Workplane:
    """Threaded boss: cylinder at (minor - 2*clearance) fused with teeth out to (major - 2*clearance)."""
    if minor is None:
        minor = minor_dia(major, pitch)
    r_root = minor / 2 - clearance
    r_crest = major / 2 - clearance
    core = cq.Workplane("XY").circle(r_root).extrude(length)
    t = teeth(r_root, r_crest, pitch, length, crest_frac=0.125, root_frac=0.5)
    out = core.union(cq.Workplane("XY").newObject([t]))
    assert out.solids().size() == 1, "thread boss did not fuse into one solid"
    return out.translate((0, 0, z0))


def cut_internal(wp: cq.Workplane, major: float, pitch: float, length: float, clearance: float = 0.0,
                 z0: float = 0.0, lead_chamfer: float = 0.0, minor: float | None = None) -> cq.Workplane:
    """Cut an internal thread into `wp`: bore at minor, helical grooves out to major (+clearance), lead chamfer."""
    if minor is None:
        minor = minor_dia(major, pitch)
    r_root = minor / 2
    r_crest = major / 2 + clearance
    # teeth first (helical faces meet the untouched block transversally), bore second
    t = teeth(r_root, r_crest, pitch, length, crest_frac=0.25, root_frac=0.65).translate(cq.Vector(0, 0, z0))
    wp = wp.cut(cq.Workplane("XY").newObject([t]))
    bore = cq.Workplane("XY").circle(r_root).extrude(length).translate((0, 0, z0))
    wp = wp.cut(bore)
    if lead_chamfer > 0:
        cone = cq.Solid.makeCone(r_crest + lead_chamfer, r_crest - lead_chamfer, 2 * lead_chamfer,
                                 cq.Vector(0, 0, z0 - lead_chamfer), cq.Vector(0, 0, 1))
        wp = wp.cut(cq.Workplane("XY").newObject([cone]))
    return wp


def boss_cosmetic(major: float, pitch: float, length: float, z0: float = 0.0) -> cq.Workplane:
    """Plain cylinder at the major diameter (machinist cuts the thread)."""
    return cq.Workplane("XY").circle(major / 2).extrude(length).translate((0, 0, z0))


def cut_internal_cosmetic(wp: cq.Workplane, major: float, pitch: float, length: float,
                          z0: float = 0.0, lead_chamfer: float = 0.0) -> cq.Workplane:
    """Plain bore at the minor diameter (machinist taps / single-points the thread)."""
    r = minor_dia(major, pitch) / 2
    wp = wp.cut(cq.Workplane("XY").circle(r).extrude(length).translate((0, 0, z0)))
    if lead_chamfer > 0:
        # 45 deg lead chamfer: radius r + lead_chamfer at the mouth (z0), r at z0 + lead_chamfer. The cone runs
        # OVERLAP past both ends so its end faces lie in free space / inside the bore, never on an existing face.
        cone = cq.Solid.makeCone(r + lead_chamfer + OVERLAP, r - OVERLAP, lead_chamfer + 2 * OVERLAP,
                                 cq.Vector(0, 0, z0 - OVERLAP), cq.Vector(0, 0, 1))
        wp = wp.cut(cq.Workplane("XY").newObject([cone]))
    return wp
