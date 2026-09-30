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
lead chamfer (on the minor diameter, i.e. the internal crest), as separate single-solid
operations. verify.py re-checks the fit; `python3 -m parts.threads` runs a quick self-test.
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
    """Cut an internal thread into `wp`: bore at minor, helical grooves out to major (+clearance), and a 45 deg lead
    chamfer that breaks the minor-diameter crest from minor/2 + lead_chamfer at z0 down to minor/2 at z0 + lead_chamfer."""
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
        # 45 deg lead chamfer on the MINOR diameter (the internal thread's crest): radius r_root + lead_chamfer at the
        # mouth (z0), r_root at z0 + lead_chamfer, so only the first `lead_chamfer` of crest is broken and the boss
        # (crest at major/2 - clearance) still engages from z0 on. Built on r_crest (the groove root) this would be a
        # counterbore wider than the boss and the thread would start `lead_chamfer` deep instead. The cone runs
        # OVERLAP past both ends so its end faces lie in free space / inside the bore, never on an existing face.
        cone = cq.Solid.makeCone(r_root + lead_chamfer + OVERLAP, r_root - OVERLAP, lead_chamfer + 2 * OVERLAP,
                                 cq.Vector(0, 0, z0 - OVERLAP), cq.Vector(0, 0, 1))
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


def _self_test() -> None:
    """Quick geometry check on the resin thread (`python3 -m parts.threads` from the terra-proto directory):
    the lead chamfer must break the minor-diameter crest, not counterbore the groove root, and the boss must
    screw in without interference. Uses the resin profile so the numbers are the ones verify.py sees."""
    import math
    import params

    p = params.get("resin")
    D, P, L, C, LC = p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len, p.cb_thread_clearance, 0.2
    r_root = minor_dia(D, P) / 2
    r_boss_crest = D / 2 - C
    block = cq.Workplane("XY").box(32.0, 32.0, 3.0, centered=(True, True, False))
    block = cut_internal(block, D, P, L, clearance=C, z0=0.0, lead_chamfer=LC)
    assert block.solids().size() == 1 and block.val().isValid(), "threaded block is not one valid solid"
    solid = block.val()

    def bore_radius(z: float, ang_deg: float) -> float:
        a = math.radians(ang_deg)
        lo, hi = 12.5, 15.0
        for _ in range(30):
            m = 0.5 * (lo + hi)
            if solid.isInside(cq.Vector(m * math.cos(a), m * math.sin(a), z), 1e-6):
                hi = m
            else:
                lo = m
        return hi

    # smallest bore radius around the circumference = the internal crest (minor) unless the chamfer has cut it back
    for z in (0.05, 0.15, 0.30, 0.80):
        r_min = min(bore_radius(z, a) for a in range(0, 360, 45))
        expect = r_root + max(0.0, LC - z)
        assert abs(r_min - expect) < 5e-3, f"lead chamfer: bore radius {r_min:.4f} at z {z}, expected {expect:.4f}"
        assert r_min < r_boss_crest, f"bore at z {z} ({r_min:.4f}) is wider than the boss crest {r_boss_crest:.4f}: no thread there"
        print(f"  z {z:4.2f}: internal crest r {r_min:.4f} (expected {expect:.4f})")

    b = boss(D, P, L, clearance=C, z0=0.0)
    assert b.val().isValid()

    def ivol(a: cq.Workplane, c: cq.Workplane) -> float:
        i = a.intersect(c)
        return sum(s.Volume() for s in i.solids().vals()) if i.solids().size() else 0.0

    for theta in (0.0, 120.0, 240.0):
        dz = theta / 360.0 * P - (P if theta else 0.0)      # same helical alignment, one turn before seating
        v = ivol(b.rotate((0, 0, 0), (0, 0, 1), theta).translate((0, 0, dz)), block)
        assert v < 0.01, f"boss at {theta} deg / {dz:+.3f} mm interferes: {v:.4f} mm^3"
        print(f"  boss rotated {theta:5.1f} deg, dz {dz:+.3f}: overlap {v:.4f} mm^3")
    v_bad = ivol(b.rotate((0, 0, 0), (0, 0, 1), 180.0), block)
    assert v_bad > 1.0, f"negative control should clash, got {v_bad:.3f} mm^3"
    print(f"  negative control (180 deg, no shift): overlap {v_bad:.3f} mm^3")
    print("threads self-test OK")


if __name__ == "__main__":
    _self_test()
