#!/usr/bin/env python3
"""
Verify the Terra-style GL32 case prototype against SPEC.md section 8 (v0.3).

    python3 verify.py [--profile titanium|resin|all] [--out out] [--scratch DIR]

For every requested profile (p = params.get(profile)) this script

  1. builds every part module (parts/case_body, caseback, spacer_ring, dial_blank) and every purchased
     placeholder (parts/purchased.PARTS) through the module contract and requires ONE valid solid each,
     with a well-formed bom(); only the caseback seal that cb_seal selects is built and taken into the
     assembly (parts.purchased.seal_part_name(p) when the module has it, the same cb_seal mapping here
     otherwise; SPEC 4 / 6); then checks that every solid sits where SPEC sections 3-7 place it in assembly
     coordinates (bounding box against params.py; the spacer ring must not stand proud of its OD anywhere --
     the v0.2 raised key is gone);
  2. runs the parametric assertions of SPEC section 8.2, the section 3.6 rule (thread minor >= movement
     bore - 0.05), the section 3 / 5 sanity walls, the v0.3 rib / slot clearances (SPEC 3.7 / 5: slot wider,
     deeper and taller than the rib, rib above the caseback boss and below the ledge, slot below the ring step
     and clear of the movement pocket), the crystal stack (crystal seat = H - crystal_engagement, ledge underside
     = seat - ledge_t, I-ring hidden below the front face, dial face below the ledge) and the flat gasket's derived
     sizes (gasket_id / gasket_od from gasket_radial_clear; gasket_fill within 0.5 .. 0.9), straight from params.py;
  3. computes the pairwise assembly interference volumes of SPEC section 8.3 (plus a few extra pairs) with
     OCC booleans and fails any pair >= 0.01 mm^3, with the two documented exceptions:
       (a) thread_model "plain": the case/caseback pair is scored OUTSIDE the thread-zone envelope
           (z 0 .. cb_thread_len, r <= major/2) and the overlap INSIDE it must equal the cosmetic tooth
           annulus pi/4 (D^2 - D1^2) L minus the lead chamfer within 1 % -- no slot term: the rib sits above
           the thread zone and the thread is continuous (SPEC 3.7);
       (b) a tube hole printed undersize (tube_hole_d < tube_od): the case/tube pair is judged after reaming
           the hole to tube_od, and the as-printed overlap must equal the ream allowance within 1 %.
     The selected seal: its overlap with the case inside its recess / groove (z <= 0) is reported, not scored --
     the case has no material below its back face, so that SPEC 8.3 exclusion is zero by construction for any seal
     size or position.  Scored instead is the uncompressed protrusion past z = 0 (the designed squeeze): it must
     meet the back face over the whole seating band except where SPEC puts a hole in that face -- the thread mouth
     with its lead chamfer (3.6) and, on the helix profile, the groove run-out; nothing else, the back face and the
     thread are continuous in v0.3 -- within 2 % of seal_seat_model() (the protrusion built from params.py minus
     those features, the thread cut with the parts/threads.py tools themselves); the seal's inner radius must clear
     the lead chamfer by 0.1; and the seal must lie in its caseback seat.  The I-ring crush is measured against its
     designed value.  No SPEC 8.3 row is relaxed beyond that;
  3b. SPEC 8.3b: the spacer ring is translated toward the back in 0.25 mm steps until it is fully outside the
     case and must never intersect the case (the ring slot is open at the bottom, the rib sits above the caseback
     boss, the thread minor O exceeds ring_od); the rib exists -- the case is solid at (-key_r_in - 0.1, 0, rib
     mid z) and the ring is void there (its slot) -- and its size is measured: a probe prism over the rib and the
     wall behind it, against the same prism at 6 o'clock (wall only), must hold the closed-form rib volume from
     params.py within 2 %, an unslotted ring envelope must collide with the rib by exactly that volume (negative
     control), and the ring's slot must be void over its whole SPEC 5 extent; axial float: ring top =
     z_ledge_bottom and caseback inner face = ring bottom within 1e-3 (the drawing's +ring_axial_preload height
     tolerance is not modelled), the ledge underside really is at z_ledge_bottom over the ring lip, the movement
     and the dial sit where the stack-up says; and the asserts ring_lip_wall >= 0.3, dial_ledge_overlap >= 0.25,
     flange_to_strap_margin >= 0.3, strap_bar_end_h >= 2.5;
  4. on the helix (resin) profile runs the thread screw-in test: the caseback rotated theta about Z and
     translated theta/360 * pitch along the screw-in path for theta in {0, 120, 240}, the bare boss advanced
     against the threaded zone, and a negative control (180 deg, no advance) that must interfere;
  5. probes points: every strap channel, front slot, the crown pocket, the tube hole, the movement bore inside,
     above, below and beside the rib, the gate details, the bores, the crystal seat plane and both lead chamfers
     must be void; every wall that must be solid must be solid -- the rib itself, the thread-zone wall and the
     back face at 9 o'clock (no keyway mouth), the seating band under the seal; the spacer ring, the caseback
     (with the selected seal's recess / groove) and the dial get their own probes;
  6. exports every solid to STL in the scratch directory (same tessellation as build.py), checks it is
     watertight with trimesh and that the mesh volume is within 1 % of the B-rep volume;
  7. estimates masses from the volumes and density_* (density_dial for the dial blank);
  8. writes out/<profile>/verify_report.md and exits 1 if any check failed in any profile.  Every failed
     row names the file that has to change (a part module, or SPEC.md / params.py when the SPEC itself
     asks for two incompatible things), so the report separates module defects from design decisions.

There is no tolerance for a short spacer ring: parts/spacer_ring.py raises when params.py leaves a rehaut lip
thinner than 0.3, and that raise is a build failure here.

Relative --out paths are resolved against this file's directory.  STLs never land in the repo: the scratch
directory defaults to $TERRA_SCRATCH or a folder under the system temp directory.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import math
import os
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cadquery as cq  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402

import params  # noqa: E402
from params import Params  # noqa: E402
from parts import threads  # noqa: E402

# --------------------------------------------------------------------------- configuration
PROFILES = ("titanium", "resin")
PART_MODULES = ("parts.case_body", "parts.caseback", "parts.spacer_ring", "parts.dial_blank")
PURCHASED_MODULE = "parts.purchased"
PURCHASED_NAMES = ("crystal", "iring", "gasket", "oring", "tube", "crown", "movement", "hands_envelope", "battery")
BOM_KEYS = ("item", "spec", "qty", "source")

# the two caseback seal placeholders; exactly one goes into the assembly (SPEC 4 / 6). parts.purchased.SEAL_NAMES /
# seal_part_name(p) take precedence when the module has them.
SEAL_NAMES_DEFAULT = ("gasket", "oring")
SEAL_BY_CB_SEAL = {"flat_gasket": "gasket", "oring": "oring"}

DENSITY_ATTR = {                    # g/cm3, same mapping as build.py
    "case_body": "density_case",
    "caseback": "density_caseback",
    "spacer_ring": "density_ring",
    "dial_blank": "density_dial",
}

INTERFERENCE_LIMIT = 0.01           # mm^3, SPEC 8.3 / 8.3b / 8.4
ANNULUS_TOL = 0.01                  # 1 %, SPEC 8.3 (a): overlap inside the thread zone vs the cosmetic tooth material
REAM_TOL = 0.01                     # 1 %, SPEC 8.3 (b): as-printed case/tube overlap vs the ream allowance
IRING_CRUSH_TOL = 0.02              # 2 %: case/iring overlap vs the designed I-ring crush
SEAL_SEAT_TOL = 0.02                # 2 %: case/seal overlap above z = 0 vs seal_seat_model() (a 0.01 z error on the 0.10 squeeze is 10 %)
SEAL_ID_MARGIN = 0.10               # mm, seal inner radius past the thread-mouth lead chamfer (cb_thread_minor/2 + CB_LEAD_CHAMFER)
SWEEP_STEP = 0.25                   # mm, SPEC 8.3b ring insertion sweep
FLOAT_TOL = 1e-3                    # mm, SPEC 8.3b axial float
STL_TOLERANCE = 0.01                # same tessellation as build.py
STL_ANGULAR_TOLERANCE = 0.1
STL_VOLUME_TOL = 0.01               # 1 %, SPEC 8.1
CB_LEAD_CHAMFER = 0.2               # the lead chamfer case_body.py passes to threads (SPEC 3.6)
RIB_VOL_TOL = 0.02                  # 2 %: rib volume measured on the case vs the closed form from params.py (SPEC 3.7 / 8.3b)
RIB_CLEAR_MIN = 0.1                 # mm, least rib / slot clearance params.py may leave (SPEC 3.7 / 5; params give 0.1 per side, 0.25, 0.2)
RIB_ENGAGE_MIN = 0.3                # mm, least radial rib height (key_h) that still locates the ring
GASKET_FILL_RANGE = (0.5, 0.9)      # gasket_fill (SPEC 4: incompressible rubber must never hold the rim land off the case)
PLACEMENT_TOL = 0.02                # mm, bounding box of every solid against its SPEC position
BOOLEAN_TOL = 1e-4                  # mm^3, a split intersection must add up to the whole

# SPEC 8.3 pairs (short name -> built name)
ALIAS = {"case": "case_body", "ring": "spacer_ring", "dial": "dial_blank"}
REQUIRED_PAIRS = [
    ("case", "caseback"), ("case", "ring"), ("case", "movement"), ("case", "crystal"), ("case", "tube"),
    ("case", "crown"), ("ring", "movement"), ("ring", "dial"), ("caseback", "movement"), ("caseback", "tube"),
    ("crystal", "hands_envelope"), ("crystal", "iring"),
]
EXTRA_PAIRS = [                     # not in SPEC 8.3, but every one of them must be clear too
    ("ring", "caseback"), ("ring", "tube"), ("ring", "crown"), ("dial", "movement"), ("dial", "case"),
    ("dial", "hands_envelope"), ("case", "hands_envelope"), ("crown", "tube"), ("crown", "movement"),
    ("tube", "movement"),
]


# --------------------------------------------------------------------------- bookkeeping
class Check:
    def __init__(self, group: str, name: str, ok, value: str, limit: str = "", note: str = "", blame: str = "") -> None:
        self.group = group
        self.name = name
        self.ok = ok                # True / False / None (informational row, not scored)
        self.value = value
        self.limit = limit
        self.note = note
        self.blame = blame          # for a failure: the file that has to change (module, or SPEC.md / params.py)

    @property
    def result(self) -> str:
        return "info" if self.ok is None else ("PASS" if self.ok else "FAIL")


class Built:
    def __init__(self, name: str, wp: cq.Workplane, material: str, kind: str, seconds: float) -> None:
        self.name = name
        self.wp = wp
        self.solid: cq.Solid = wp.solids().val()
        self.material = material
        self.kind = kind
        self.seconds = seconds
        self.volume = self.solid.Volume()


def _fmt(v: float, nd: int = 3) -> str:
    return f"{v:.{nd}f}"


def _exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e).strip().splitlines()[0] if str(e).strip() else ''}"


def _vol(wp: cq.Workplane) -> float:
    return sum(s.Volume() for s in wp.solids().vals())


def _ivol(a: cq.Workplane, b: cq.Workplane) -> float:
    """Volume of a INTERSECT b (0 when disjoint)."""
    return _vol(a.intersect(b))


def _one_valid(wp: cq.Workplane, what: str) -> cq.Workplane:
    if wp.solids().size() != 1 or not wp.val().isValid():
        raise RuntimeError(f"{what} is not one valid solid ({wp.solids().size()} solids)")
    return wp


def _cylinder_z(r: float, z0: float, z1: float) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def _cylinder_x(r: float, y: float, z: float, x0: float, x1: float) -> cq.Workplane:
    return cq.Workplane("YZ", origin=(x0, 0, 0)).center(y, z).circle(r).extrude(x1 - x0)


def _box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Workplane:
    return (cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=(False, False, False))
            .translate((x0, y0, z0)))


def _module_of(short: str) -> str:
    """The file a built name comes from: a part module, or parts/purchased.py for a placeholder."""
    name = ALIAS.get(short, short)
    return f"parts/{name}.py" if name in ("case_body", "caseback", "spacer_ring", "dial_blank") else "parts/purchased.py"


def _blame_pair(a: str, b: str) -> str:
    ma, mb = _module_of(a), _module_of(b)
    return ma if ma == mb else f"{ma} or {mb}"


# --------------------------------------------------------------------------- SPEC-derived geometry (params only)
def rib(p: Params) -> dict:
    """SPEC 3.7 anti-rotation rib on the case's movement-bore wall at 9 o'clock (-X) and its mate, the SPEC 5 slot in the
    spacer ring's OD, from params.py alone.  Rib: |y| <= w/2 (key_w), from radius r_in (key_r_in) out to the bore wall
    r_out (mvt_bore_d/2), z0 (key_z0) .. z1 (key_z1); volume in closed form (the strip of the annulus).  Slot: slot_w
    (key_slot_w) wide, flat floor at radius r_slot_floor = ring_od/2 - key_slot_depth, from the ring bottom (open) up to
    slot_z1 (key_slot_z1).  Clearances: clear_w per side, clear_r (rib tip to slot floor), clear_top (slot top above the
    rib top), clear_thread (rib bottom above the thread zone = above the caseback boss), clear_ledge (rib top below the
    ledge underside), slot_wall (slot floor to the movement-pocket flat), slot_to_step (ring step above the slot top)."""
    w = p.key_w / 2
    r_out = p.mvt_bore_d / 2
    r_slot_floor = p.ring_od / 2 - p.key_slot_depth
    ok = 0 < w < p.key_r_in < r_out and p.key_z1 > p.key_z0
    vol = _strip_area(p.key_r_in, r_out, w) * (p.key_z1 - p.key_z0) if ok else 0.0
    return dict(w=p.key_w, h=p.key_h, r_in=p.key_r_in, r_out=r_out, z0=p.key_z0, z1=p.key_z1,
                z_mid=(p.key_z0 + p.key_z1) / 2, length=p.key_z1 - p.key_z0, volume=vol,
                slot_w=p.key_slot_w, slot_depth=p.key_slot_depth, slot_z0=p.z_cb_inner, slot_z1=p.key_slot_z1,
                r_slot_floor=r_slot_floor, clear_w=(p.key_slot_w - p.key_w) / 2, clear_r=p.key_r_in - r_slot_floor,
                clear_top=p.key_slot_z1 - p.key_z1, clear_thread=p.key_z0 - p.cb_thread_len,
                clear_ledge=p.z_ledge_bottom - p.key_z1, slot_wall=r_slot_floor - p.ring_pocket_w / 2,
                slot_to_step=p.z_dial_seat - p.key_slot_z1)


def seal_seat(p: Params) -> tuple[float, float, float, str]:
    """SPEC 4: (inner radius, outer radius, depth, label) of the seal recess / groove in the flange's inner face."""
    if p.cb_seal == "flat_gasket":
        return p.cb_thread_major / 2, p.gasket_recess_od / 2, p.gasket_recess_depth, "gasket recess"
    if p.cb_seal == "oring":
        return ((p.oring_groove_mean_d - p.oring_groove_w) / 2, (p.oring_groove_mean_d + p.oring_groove_w) / 2,
                p.oring_groove_depth, "O-ring groove")
    raise ValueError(f"cb_seal must be one of {sorted(SEAL_BY_CB_SEAL)}, got {p.cb_seal!r}")


def seal_protrusion(p: Params) -> tuple[float, float, float, float]:
    """SPEC 6: the selected seal uncompressed -- (protrusion past z = 0, volume of that protruding part in closed form,
    inner radius, outer radius).  Flat gasket: the annulus x (gasket_t - gasket_recess_depth).  O-ring: the torus cap
    above z = 0, by Pappus 2 pi R_mean x the circular segment of the tube section (the segment's centroid sits on the
    mean radius because the cutting plane is parallel to the torus plane)."""
    if p.cb_seal == "flat_gasket":
        ri, ro = p.gasket_id / 2, p.gasket_od / 2
        h = p.gasket_t - p.gasket_recess_depth
        return h, math.pi * (ro ** 2 - ri ** 2) * max(0.0, h), ri, ro
    if p.cb_seal == "oring":
        rc, r_mean = p.oring_cs / 2, p.oring_groove_mean_d / 2
        h = (p.oring_cs - p.oring_groove_depth) / 2
        hc = min(max(h, 0.0), 2 * rc)
        seg = rc * rc * math.acos(1.0 - hc / rc) - (rc - hc) * math.sqrt(max(0.0, 2 * rc * hc - hc * hc))
        return h, 2 * math.pi * r_mean * seg, r_mean - rc, r_mean + rc
    raise ValueError(f"cb_seal must be one of {sorted(SEAL_BY_CB_SEAL)}, got {p.cb_seal!r}")


def seal_seat_model(p: Params, lead_chamfer: float) -> dict:
    """SPEC 4 / 6 / 8.3: what the uncompressed seal's protrusion past z = 0 meets on the case back face.  The protruding
    part is built from params.py alone -- flat gasket: the annulus gasket_id/2..gasket_od/2, z 0..gasket_t -
    gasket_recess_depth; O-ring: the torus of SPEC 6 (mean O oring_groove_mean_d, CS oring_cs, centred at
    -oring_groove_depth/2) clipped to z >= 0 -- so the expected value does not follow a misplaced placeholder.  It would
    overlap the case completely, except where the back face is not there: the thread mouth with its 45 deg lead chamfer
    at the minor O (SPEC 3.6) and, on the helix profile, the thread groove where it runs out at the back face.  Nothing
    else interrupts the back face: the v0.3 anti-rotation rib (SPEC 3.7) stands on the bore wall above the thread zone,
    so the seating face is continuous all round (the v0.2 keyway mouth is gone).  Those are cut from the protruding part
    one tool at a time -- the chamfer cone from params.py, the thread zone with the parts/threads.py tools case_body.py
    uses, started one pitch (helix) / 1 mm (plain) below the back face: a thread shifted by whole pitches is the same
    thread, and no tool face then lies on the part's z = 0 face.  What remains is the expected case/seal overlap.  Returns
    proud (the protruding part's volume, to be compared with seal_protrusion()'s closed form), the chamfer / thread bites
    and expected."""
    r_minor, L, P = p.cb_thread_minor / 2, p.cb_thread_len, p.cb_thread_pitch
    protrusion, _, r_in, r_out = seal_protrusion(p)
    if protrusion <= 0 or r_out <= r_in:
        raise ValueError(f"params.py: the {p.cb_seal} seal does not protrude past z = 0 ({protrusion:.3f}), nothing seats")
    if p.cb_seal == "flat_gasket":
        proud = cq.Workplane("XY").circle(r_out).circle(r_in).extrude(protrusion)
    else:
        torus = cq.Solid.makeTorus((r_in + r_out) / 2, (r_out - r_in) / 2, cq.Vector(0, 0, -p.oring_groove_depth / 2),
                                   cq.Vector(0, 0, 1))
        proud = cq.Workplane("XY").newObject([torus]).intersect(_box(-p.W, p.W, -p.L, p.L, 0.0, p.cb_flange_t + 1.0))
    proud = _one_valid(proud, f"{p.cb_seal} protrusion past z = 0, from params.py")
    v0 = _vol(proud)
    # thread-mouth lead chamfer: r_minor + lead_chamfer at z = 0 down to r_minor at z = lead_chamfer, 0.3 past both ends
    cone = cq.Solid.makeCone(r_minor + lead_chamfer + 0.3, r_minor - 0.3, lead_chamfer + 0.6, cq.Vector(0, 0, -0.3),
                             cq.Vector(0, 0, 1))
    m = _one_valid(proud.cut(cq.Workplane("XY").newObject([cone])), "seat model minus the lead chamfer")
    v1 = _vol(m)
    # the thread zone: bore at the minor O; helix: the grooves out to major/2 + clearance, which run out at the back face
    if p.thread_model == "helix":
        m = threads.cut_internal(m, p.cb_thread_major, P, L + P, clearance=p.cb_thread_clearance, z0=-P, lead_chamfer=0.0)
    else:
        m = threads.cut_internal_cosmetic(m, p.cb_thread_major, P, L + 1.0, z0=-1.0, lead_chamfer=0.0)
    m = _one_valid(m, "seat model minus the thread zone")
    v2 = _vol(m)
    return dict(proud=v0, chamfer=v0 - v1, thread=v1 - v2, expected=v2)


def _arc_integral(R: float, w: float) -> float:
    """Integral of sqrt(R^2 - y^2) over -w..w (w <= R): the area under the circle r = R inside the strip |y| <= w,
    one side of the axis."""
    w = min(w, R)
    return w * math.sqrt(R * R - w * w) + R * R * math.asin(w / R)


def _strip_area(a: float, b: float, w: float) -> float:
    """Area between the circles r = a and r = b (b > a) inside the strip |y| <= w, one side of the axis (w < a)."""
    if b <= a:
        return 0.0
    return _arc_integral(b, w) - _arc_integral(a, w)


def cosmetic_tooth_volume(p: Params, lead_chamfer: float) -> dict:
    """SPEC 8.3 (a): the material between the cosmetic bore (minor O, SPEC 3.6) and the cosmetic boss (major O,
    SPEC 4) over the thread zone -- what the machinist turns into thread: the annulus pi/4 (D^2 - D1^2) L minus
    the lead chamfer cone.  No slot term: the v0.3 anti-rotation rib (SPEC 3.7) starts key_z_margin above the
    thread zone, so the thread is continuous.  Closed form for the annulus, quadrature in z for the chamfer."""
    r_major, r_minor, L = p.cb_thread_major / 2, p.cb_thread_minor / 2, p.cb_thread_len
    z = np.linspace(0.0, L, 40001)
    r_in = np.minimum(r_major, r_minor + np.maximum(0.0, lead_chamfer - z))      # bore radius at height z
    ring_area = math.pi * (r_major ** 2 - r_in ** 2)
    trapz = getattr(np, "trapezoid", None) or np.trapz
    annulus = math.pi * (r_major ** 2 - r_minor ** 2) * L
    chamfer = annulus - float(trapz(ring_area, z))
    return dict(annulus=annulus, chamfer=chamfer, expected=annulus - chamfer)


# --------------------------------------------------------------------------- the verifier
class Verifier:
    def __init__(self, profile: str, out_root: str, scratch_root: str) -> None:
        self.profile = profile
        self.p: Params = params.get(profile)
        self.out_dir = os.path.join(out_root, profile)
        self.scratch = os.path.join(scratch_root, profile)
        self.checks: list[Check] = []
        self.built: dict[str, Built] = {}
        self.module_warnings: list[str] = []
        self.masses: list[tuple[str, str, float, float | None, float | None]] = []
        self.seal: str | None = None                  # PARTS name of the seal in the assembly
        self.seal_names: tuple[str, ...] = SEAL_NAMES_DEFAULT
        self.t0 = time.perf_counter()

    # -- helpers
    def add(self, group: str, name: str, ok, value, limit: str = "", note: str = "", blame: str = "") -> Check:
        c = Check(group, name, ok, value if isinstance(value, str) else _fmt(value), limit, note, blame)
        self.checks.append(c)
        tag = c.result
        print(f"  [{tag:4s}] {name}: {c.value}" + (f"  (limit {limit})" if limit else "") + (f"  -- {note}" if note else ""))
        return c

    def error(self, group: str, name: str, e: BaseException, blame: str = "") -> Check:
        tb = traceback.format_exception(type(e), e, e.__traceback__, limit=-3)
        print("".join(tb), file=sys.stderr)
        return self.add(group, name, False, "error", "", _exc(e), blame=blame or f"parts / verify.py ({group})")

    def wp(self, short: str) -> cq.Workplane:
        return self.built[ALIAS.get(short, short)].wp

    def has(self, *shorts: str) -> bool:
        return all(ALIAS.get(s, s) in self.built for s in shorts)

    def bbox(self, short: str):
        return self.built[ALIAS.get(short, short)].solid.BoundingBox()

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    # ------------------------------------------------------------------ 1. build
    def run_build(self) -> None:
        print(f"\n== build ({self.profile})")
        g = "build"
        p = self.p
        for modname in PART_MODULES:
            stem = modname.rsplit(".", 1)[-1]
            try:
                mod = importlib.import_module(modname)
            except Exception as e:  # noqa: BLE001
                self.error(g, f"{stem}: import", e, blame=f"parts/{stem}.py")
                continue
            missing = [a for a in ("PART", "MATERIAL", "build", "bom") if not hasattr(mod, a)]
            if missing:
                self.add(g, f"{stem}: module contract", False, f"missing {missing}", "PART, MATERIAL, build, bom",
                         blame=f"parts/{stem}.py")
                continue
            name = str(mod.PART)
            if name != stem:
                self.add(g, f"{stem}: PART name", False, name, stem, blame=f"parts/{stem}.py")
            material = mod.MATERIAL.get(self.profile, "?") if isinstance(mod.MATERIAL, dict) else str(mod.MATERIAL)
            self._build_one(name, mod.build, material, "part")
            self._check_bom(name, mod)
            for attr in ("SKIPPED", "WARNINGS"):
                for w in getattr(mod, attr, []) or []:
                    if isinstance(w, str) and w.startswith(self.profile + ":"):
                        self.module_warnings.append(f"{name}: {w[len(self.profile) + 1:].strip()}")
        try:
            pmod = importlib.import_module(PURCHASED_MODULE)
        except Exception as e:  # noqa: BLE001
            self.error(g, "purchased: import", e, blame="parts/purchased.py")
            return
        parts_dict = getattr(pmod, "PARTS", None)
        if not isinstance(parts_dict, dict) or not hasattr(pmod, "bom"):
            self.add(g, "purchased: module contract", False, "no PARTS dict / bom()", "PARTS, bom", blame="parts/purchased.py")
            return
        missing = [n for n in PURCHASED_NAMES if n not in parts_dict]
        self.add(g, "purchased: PARTS covers SPEC section 6 (both seal placeholders included)", not missing,
                 "all present" if not missing else f"missing {missing}", ", ".join(PURCHASED_NAMES),
                 blame="" if not missing else "parts/purchased.py")
        # -- seal selection (SPEC 4 / 6): the assembly takes the ONE seal cb_seal selects
        names = getattr(pmod, "SEAL_NAMES", None)
        self.seal_names = tuple(str(n) for n in names) if isinstance(names, (tuple, list)) and names else SEAL_NAMES_DEFAULT
        self.seal = self._select_seal(pmod)
        if self.seal is not None:
            left_out = [n for n in self.seal_names if n != self.seal]
            self.add(g, f"purchased: caseback seal placeholder for cb_seal {p.cb_seal!r}", self.seal in parts_dict, self.seal,
                     " | ".join(self.seal_names),
                     f"only this seal is built and taken into the assembly (SPEC 6); left out: {', '.join(left_out) or 'none'}",
                     blame="" if self.seal in parts_dict else "parts/purchased.py")
            self._seal_fit(pmod)
        for name, fn in parts_dict.items():
            if name in self.seal_names and name != self.seal:
                continue                                    # the unselected seal (or, with no valid selection, both)
            material = pmod.MATERIAL.get(name, "purchased") if isinstance(getattr(pmod, "MATERIAL", None), dict) else "purchased"
            self._build_one(str(name), fn, material, "purchased")
        self._check_bom("purchased", pmod)

    def _select_seal(self, pmod) -> str | None:
        """parts.purchased.seal_part_name(p) when present, else the cb_seal mapping; None (and a failed row) when neither
        gives a seal name."""
        p = self.p
        fn = getattr(pmod, "seal_part_name", None)
        if callable(fn):
            try:
                seal = fn(p)
            except Exception as e:  # noqa: BLE001
                self.error("build", f"purchased: seal_part_name(p) for cb_seal {p.cb_seal!r}", e,
                           blame="params.py (cb_seal) or parts/purchased.py")
                return None
            if not isinstance(seal, str) or not seal:
                self.add("build", "purchased: seal_part_name(p)", False, repr(seal), "a PARTS name", blame="parts/purchased.py")
                return None
            return seal
        if p.cb_seal in SEAL_BY_CB_SEAL:
            return SEAL_BY_CB_SEAL[p.cb_seal]
        self.add("build", "purchased: seal selected by cb_seal (module has no seal_part_name)", False, repr(p.cb_seal),
                 " | ".join(sorted(SEAL_BY_CB_SEAL)), blame="params.py (cb_seal)")
        return None

    def _seal_fit(self, pmod) -> None:
        """parts.purchased.seal_fit_problems(p), when the module has it: what params.py makes impossible for the selected
        seal in its seat.  Scored: the geometry rows in section 3 measure the same conflict, this row names it."""
        fn = getattr(pmod, "seal_fit_problems", None)
        if not callable(fn):
            return
        try:
            problems = list(fn(self.p))
        except Exception as e:  # noqa: BLE001
            self.error("build", "purchased: seal_fit_problems(p)", e, blame="parts/purchased.py")
            return
        self.add("build", f"purchased: {self.seal} fits its seat as params.py describes it (seal_fit_problems)", not problems,
                 "fits" if not problems else "; ".join(str(m) for m in problems), "no problems",
                 "" if not problems else "the placeholder is built exactly as params.py says; the seal rows in section 3 show the overlap",
                 blame="" if not problems else "params.py (gasket_* / oring_* against cb_thread_major / cb_flange_d / gasket_recess_od)")

    def _build_one(self, name: str, fn, material: str, kind: str) -> None:
        t0 = time.perf_counter()
        try:
            obj = fn(self.p)
            if isinstance(obj, cq.Shape):
                obj = cq.Workplane("XY").newObject([obj])
            if not isinstance(obj, cq.Workplane):
                raise TypeError(f"build() returned {type(obj).__name__}")
            n = obj.solids().size()
            if n != 1:
                raise ValueError(f"{n} solids")
            if not obj.solids().val().isValid():
                raise ValueError("solid is not valid")
        except Exception as e:  # noqa: BLE001
            blame = f"parts/{'purchased' if kind == 'purchased' else name}.py"
            if isinstance(e, ValueError) and "params.py" in str(e):
                blame = f"params.py (see the message) -- {blame} refuses to build an impossible part"
            self.error("build", f"{name}: one valid solid", e, blame=blame)
            return
        b = Built(name, obj, material, kind, time.perf_counter() - t0)
        self.built[name] = b
        bb = b.solid.BoundingBox()
        self.add("build", f"{name}: one valid solid", True,
                 f"{b.volume:.2f} mm^3, bbox x {bb.xmin:.2f}..{bb.xmax:.2f} y {bb.ymin:.2f}..{bb.ymax:.2f} "
                 f"z {bb.zmin:.2f}..{bb.zmax:.2f}", "1 solid, isValid", f"{b.seconds:.1f} s, {material}")

    def _check_bom(self, name: str, mod) -> None:
        try:
            rows = mod.bom(self.p)
        except Exception as e:  # noqa: BLE001
            self.error("build", f"{name}: bom()", e, blame=f"parts/{name}.py")
            return
        ok = isinstance(rows, list) and all(isinstance(r, dict) and all(k in r for k in BOM_KEYS) for r in rows)
        self.add("build", f"{name}: bom() rows", ok, f"{len(rows) if isinstance(rows, list) else '?'} rows",
                 "list of dicts with item/spec/qty/source", blame="" if ok else f"parts/{name}.py")

    # ------------------------------------------------------------------ 1b. placement
    def run_placement(self) -> None:
        """Bounding box of every built solid against the position SPEC sections 3-7 give it (params.py values)."""
        print(f"\n== placement in assembly coordinates ({self.profile})")
        p = self.p
        g = "placement"
        r_ir = p.crystal_d / 2 + p.iring_wall
        r_or = (p.oring_groove_mean_d + p.oring_cs) / 2
        z_or = -p.oring_groove_depth / 2
        z_g0 = -p.gasket_recess_depth
        # SPEC 5: nothing stands proud of the ring OD (the anti-rotation feature is a slot IN the OD); at +X the stem
        # slot removes the OD at y = 0 up to z_stem + 1.2, above which the lip reaches the full OD again unless the slot
        # runs out through the top
        r_ring = p.ring_od / 2
        x_ring_max = r_ring if p.z_stem + 1.2 < p.z_ledge_bottom - 0.05 else math.sqrt(r_ring ** 2 - (p.ring_stem_slot_w / 2) ** 2)
        expected = {
            "case_body": ("SPEC 3.1", dict(x=(-p.W / 2, p.W / 2), y=(-p.L / 2, p.L / 2), z=(0.0, p.H)), "parts/case_body.py"),
            "caseback": ("SPEC 4", dict(x=(-p.cb_flange_d / 2, p.cb_flange_d / 2), z=(-p.cb_flange_t, p.cb_thread_len)),
                         "parts/caseback.py"),
            "spacer_ring": ("SPEC 5: z_cb_inner .. z_ledge_bottom (ring_h), nothing proud of the OD (slot, not key)",
                            dict(x=(-r_ring, x_ring_max), y=(-r_ring, r_ring), z=(p.z_cb_inner, p.z_ledge_bottom)),
                            "parts/spacer_ring.py"),
            "dial_blank": ("SPEC 6", dict(x=(-p.dial_d / 2, p.dial_d / 2), z=(p.z_dial_seat, p.z_dial_face)), "parts/dial_blank.py"),
            "crystal": ("SPEC 6", dict(x=(-p.crystal_d / 2, p.crystal_d / 2), z=(p.z_crystal_seat, p.z_crystal_top)), "parts/purchased.py"),
            "iring": ("SPEC 6", dict(x=(-r_ir, r_ir), z=(p.z_crystal_seat, p.z_crystal_seat + p.iring_h)), "parts/purchased.py"),
            "gasket": ("SPEC 6 (on the recess floor, gasket_t - gasket_recess_depth proud of z = 0)",
                       dict(x=(-p.gasket_od / 2, p.gasket_od / 2), y=(-p.gasket_od / 2, p.gasket_od / 2), z=(z_g0, z_g0 + p.gasket_t)),
                       "parts/purchased.py"),
            "oring": ("SPEC 6 (in the caseback groove)", dict(x=(-r_or, r_or), z=(z_or - p.oring_cs / 2, z_or + p.oring_cs / 2)),
                      "parts/purchased.py"),
            "tube": ("SPEC 6", dict(x=(p.x_bore_wall, p.x_bore_wall + p.tube_len), y=(-p.tube_od / 2, p.tube_od / 2),
                     z=(p.z_stem - p.tube_od / 2, p.z_stem + p.tube_od / 2)), "parts/purchased.py"),
            "crown": ("SPEC 6", dict(x=(p.x_crown_start, p.x_crown_start + p.crown_len), y=(-p.crown_d / 2, p.crown_d / 2),
                      z=(p.z_stem - p.crown_d / 2, p.z_stem + p.crown_d / 2)), "parts/purchased.py"),
            "movement": ("SPEC 6", dict(x=(-p.mvt_w / 2, p.x_crown_start), y=(-p.mvt_l / 2, p.mvt_l / 2),
                         z=(p.z_mvt_back, p.z_dial_seat + p.mvt_pipe_h)), "parts/purchased.py"),
            "hands_envelope": ("SPEC 6", dict(z=(p.z_dial_face, p.z_dial_face + p.hands_top_above_dial_face)), "parts/purchased.py"),
        }
        for name, (ref, axes, blame) in expected.items():
            if name not in self.built:
                continue
            bb = self.built[name].solid.BoundingBox()
            got = {"x": (bb.xmin, bb.xmax), "y": (bb.ymin, bb.ymax), "z": (bb.zmin, bb.zmax)}
            bad = [ax for ax, (lo, hi) in axes.items()
                   if abs(got[ax][0] - lo) > PLACEMENT_TOL or abs(got[ax][1] - hi) > PLACEMENT_TOL]
            meas = "; ".join(f"{ax} {got[ax][0]:.2f}..{got[ax][1]:.2f} (SPEC {lo:.2f}..{hi:.2f})" for ax, (lo, hi) in axes.items())
            self.add(g, f"{name}: bounding box at its SPEC position ({ref})", not bad, meas, f"+-{PLACEMENT_TOL}",
                     "" if not bad else f"off on {', '.join(bad)}", blame=blame if bad else "")
        if "battery" in self.built and "movement" in self.built:
            bb = self.built["battery"].solid.BoundingBox()
            ok = bb.zmin >= p.z_mvt_back - PLACEMENT_TOL and bb.zmax <= p.z_dial_seat + PLACEMENT_TOL
            self.add(g, "battery: inside the movement envelope height (SPEC 6, cosmetic)", ok,
                     f"z {bb.zmin:.2f}..{bb.zmax:.2f} (movement {p.z_mvt_back:.2f}..{p.z_dial_seat:.2f})", "inside",
                     "", blame="" if ok else "parts/purchased.py")

    # ------------------------------------------------------------------ 2. parametric
    def run_parametric(self) -> None:
        print(f"\n== parametric assertions ({self.profile})")
        p = self.p
        g = "parametric"
        r_thread_out = p.cb_thread_major / 2 + (p.cb_thread_clearance if p.thread_model == "helix" else 0.0)
        wall_crystal = p.x_pocket_floor - p.crystal_bore_d / 2
        wall_thread = p.x_pocket_floor - r_thread_out
        ring_wall = min(p.ring_od - p.ring_pocket_l, p.ring_od - p.ring_pocket_w) / 2
        rg = rib(p)
        c = RIB_CLEAR_MIN
        rows = [
            ("hands_clearance >= hands_clearance_min", p.hands_clearance, p.hands_clearance_min, ""),
            ("tube_to_thread_margin >= 0.1", p.tube_to_thread_margin, 0.1, "driven by UNVERIFIED stem_below_dial_seat"),
            ("cb_thread_minor >= ring_od + 0.1", p.cb_thread_minor, p.ring_od + 0.1, "ring passes the thread from the back"),
            ("cb_thread_minor >= mvt_bore_d - 0.05 (SPEC 3.6)", p.cb_thread_minor, p.mvt_bore_d - 0.05, ""),
            ("strap_front_wall >= 2.0", p.strap_front_wall, 2.0, "solid above the end-face exit"),
            ("strap_bar_end_h >= 1.5", p.strap_bar_end_h, 1.5, "bar height on the end face (SPEC 8.3b asks >= 2.5, see section 3b)"),
            ("end_face_flat_half_w >= strap_slot_w/2 + 0.3", p.end_face_flat_half_w, p.strap_slot_w / 2 + 0.3, ""),
            ("crown-side wall at the crystal bore inside the pocket >= 1.0", wall_crystal, 1.0,
             f"x_pocket_floor {p.x_pocket_floor:.2f} - crystal_bore_d/2 {p.crystal_bore_d / 2:.3f}"),
            ("crown-side wall at the thread inside the pocket >= 1.0", wall_thread, 1.0,
             f"x_pocket_floor {p.x_pocket_floor:.2f} - thread outer radius {r_thread_out:.3f}"),
            ("ring pocket wall >= 2.5", ring_wall, 2.5, "(ring_od - ring_pocket_l)/2, thinnest at 12/6"),
            ("tube engagement >= 1.5", p.tube_engagement, 1.5, ""),
            ("ring_dial_clearance >= 2 x ring_pocket_clearance (SPEC 5: the dial never binds)", p.ring_dial_clearance,
             2 * p.ring_pocket_clearance, "dial radial clearance vs movement-pocket clearance"),
            # -- crystal stack (SPEC 3.3 / 3.4 / 7): everything read from crystal_engagement / ledge_t
            ("crystal_engagement >= iring_h (SPEC 3.3: the I-ring sits below the front face, no open gap around the crystal)",
             p.crystal_engagement, p.iring_h,
             f"crystal seat z_crystal_seat = H - crystal_engagement = {p.z_crystal_seat:.2f}; I-ring top {p.z_crystal_seat + p.iring_h:.2f} "
             f"vs front face {p.H:.2f}; designed margin 0.1"),
            ("dial face below the ledge underside: z_ledge_bottom - z_dial_face >= 0.5 (the dial hides dial_ledge_overlap under the ledge)",
             p.z_ledge_bottom - p.z_dial_face, 0.5,
             f"ledge underside z_ledge_bottom = z_crystal_seat - ledge_t = {p.z_ledge_bottom:.2f} (ledge_t {p.ledge_t:.2f}); dial face {p.z_dial_face:.2f}"),
            # -- anti-rotation rib / slot (SPEC 3.7 / 5): the ring must slide over the rib from the back and the rib must
            #    stay clear of the caseback boss (thread zone), the ledge and the ring's dial recess / movement pocket
            (f"rib / slot: slot width clears the rib, (key_slot_w - key_w)/2 >= {c / 2:g} per side (SPEC 3.7 / 5)", rg["clear_w"], c / 2,
             f"slot {rg['slot_w']:.2f} wide for a {rg['w']:.2f} rib (key_slot_clear_w {p.key_slot_clear_w:g})"),
            (f"rib / slot: rib tip clears the slot floor, key_r_in - (ring_od/2 - key_slot_depth) >= {c:g}", rg["clear_r"], c,
             f"rib inner r {rg['r_in']:.3f} vs slot floor r {rg['r_slot_floor']:.3f} (slot {rg['slot_depth']:.2f} deep for a {rg['h']:.2f} rib)"),
            (f"rib / slot: slot top above the rib top, key_slot_z1 - key_z1 >= {c:g}", rg["clear_top"], c,
             f"slot up to z {rg['slot_z1']:.3f}, rib up to z {rg['z1']:.3f}"),
            (f"rib above the caseback boss / thread zone: key_z0 - cb_thread_len >= {c:g} (SPEC 3.7: thread and gasket face uninterrupted)",
             rg["clear_thread"], c, f"rib from z {rg['z0']:.3f}; caseback inner face / thread zone end z {p.z_cb_inner:.3f}"),
            (f"rib below the ledge underside: z_ledge_bottom - key_z1 >= {c:g}", rg["clear_ledge"], c,
             f"rib top z {rg['z1']:.3f}, ledge underside z {p.z_ledge_bottom:.3f}"),
            (f"rib radial height key_h >= {RIB_ENGAGE_MIN:g} (locates the ring)", rg["h"], RIB_ENGAGE_MIN,
             f"rib r {rg['r_in']:.3f} .. {rg['r_out']:.3f} on the bore wall"),
            ("rib axial length key_z1 - key_z0 >= 0.5", rg["length"], 0.5,
             f"z {rg['z0']:.3f} .. {rg['z1']:.3f}; key_z_margin {p.key_z_margin:g} above the boss and below the dial seat"),
            ("ring slot stays below the ring step: z_dial_seat - key_slot_z1 >= 0 (SPEC 5: the rehaut lip above it is untouched)",
             rg["slot_to_step"], 0.0, f"slot top z {rg['slot_z1']:.3f}, step (dial seat) z {p.z_dial_seat:.3f}"),
            ("ring slot floor to the movement-pocket flat >= 0.5 (SPEC 5: the slot stays in the thick lower wall)", rg["slot_wall"], 0.5,
             f"slot floor r {rg['r_slot_floor']:.3f} vs pocket flat at |x| {p.ring_pocket_w / 2:.3f}"),
        ]
        blame = {
            "tube_to_thread_margin >= 0.1": "params.py (stem_below_dial_seat, UNVERIFIED)",
            "hands_clearance >= hands_clearance_min": "params.py (hands_top_above_dial_face / crystal_engagement)",
        }
        for name, val, lim, note in rows:
            if name.startswith("rib") or name.startswith("ring slot"):
                bl = "params.py (key_w / key_h / key_slot_clear_w / key_slot_clear_h / key_z_margin vs mvt_bore_d / ring_od_clearance)"
            elif name.startswith("crystal_engagement") or name.startswith("dial face"):
                bl = "params.py (crystal_engagement / ledge_t / iring_h vs the movement stack)"
            else:
                bl = blame.get(name, "params.py")
            self.add(g, name, val >= lim - 1e-9, val, f">= {lim:.3f}", note, blame=bl)
        # -- caseback seal sizes (SPEC 4): the flat gasket is derived from the boss and the recess (gasket_radial_clear)
        if p.cb_seal == "flat_gasket":
            lo, hi = GASKET_FILL_RANGE
            squeeze = (p.gasket_t - p.gasket_recess_depth) / p.gasket_t if p.gasket_t > 0 else float("nan")
            self.add(g, f"gasket_fill within {lo:g} .. {hi:g} (SPEC 4: the incompressible gasket never holds the rim land off the case)",
                     lo - 1e-9 <= p.gasket_fill <= hi + 1e-9, f"{p.gasket_fill:.3f}", f"{lo:g} .. {hi:g}",
                     f"gasket {p.gasket_id:.2f} x {p.gasket_od:.2f} x {p.gasket_t:g} in the recess {p.cb_thread_major:g} .. "
                     f"{p.gasket_recess_od:g} x {p.gasket_recess_depth:g}; squeeze {100 * squeeze:.0f} % when the land seats",
                     blame="params.py (gasket_t / gasket_radial_clear / gasket_recess_depth / gasket_recess_od)")
            self.add(g, "gasket_id = cb_thread_major + 2 x gasket_radial_clear, gasket_od = gasket_recess_od - 2 x gasket_radial_clear, "
                        "gasket_radial_clear >= 0.1 (SPEC 4)", p.gasket_radial_clear >= 0.1 - 1e-9,
                     f"ID {p.gasket_id:.2f} / OD {p.gasket_od:.2f}, radial clearance {p.gasket_radial_clear:g} to the boss and the recess wall",
                     ">= 0.100", f"rim land (cb_flange_d - gasket_recess_od)/2 = {(p.cb_flange_d - p.gasket_recess_od) / 2:.2f}",
                     blame="params.py (gasket_radial_clear)")
        else:
            fill = (math.pi / 4 * p.oring_cs ** 2) / (p.oring_groove_w * p.oring_groove_depth)
            self.add(g, "O-ring groove fill (SPEC 4 'oring' option)", None, f"{fill:.3f}", "",
                     f"CS {p.oring_cs:g} in a {p.oring_groove_w:g} x {p.oring_groove_depth:g} groove")
        # stack-up sanity (informational)
        self.add(g, "stack-up: crystal seat / ledge / crystal top / total height", None,
                 f"crystal seat {p.z_crystal_seat:.2f} (H {p.H:.2f} - crystal_engagement {p.crystal_engagement:.2f}), ledge underside "
                 f"{p.z_ledge_bottom:.2f} (ledge_t {p.ledge_t:.2f}), crystal {p.crystal_proud:+.2f} proud, total {p.total_height:.2f}", "", "")
        self.add(g, "stack-up: dial face below the crystal seat / ring lip", None,
                 f"dial face {p.z_dial_face:.2f} -> crystal seat {p.z_crystal_seat:.2f} ({p.hands_clearance:.2f}); "
                 f"rehaut lip {p.ring_lip_wall:.2f} thick, {p.z_ledge_bottom - p.z_dial_face:.2f} tall above the dial face", "", "")
        self.add(g, "stack-up: anti-rotation rib / ring slot (SPEC 3.7 / 5)", None,
                 f"rib {rg['w']:.2f} wide x {rg['h']:.2f} high, z {rg['z0']:.2f} .. {rg['z1']:.2f} ({rg['volume']:.3f} mm^3); ring slot "
                 f"{rg['slot_w']:.2f} wide x {rg['slot_depth']:.2f} deep, open at the bottom up to z {rg['slot_z1']:.2f}; ring height "
                 f"{p.ring_h:.2f} +{p.ring_axial_preload:g}/+{p.ring_axial_preload + 0.03:g} on the drawing (nominal modelled)", "", "")

    # ------------------------------------------------------------------ 3. interference
    def run_interference(self) -> None:
        print(f"\n== assembly interference ({self.profile})")
        p = self.p
        g = "interference"
        lim = f"< {INTERFERENCE_LIMIT} mm^3"
        for pairs, label in ((REQUIRED_PAIRS, "SPEC 8.3"), (EXTRA_PAIRS, "extra")):
            for a, b in pairs:
                name = f"{a}/{b}" + ("" if label == "SPEC 8.3" else " (extra pair)")
                if not self.has(a, b):
                    self.add(g, name, False, "not built", lim, blame=_blame_pair(a, b))
                    continue
                if (a, b) == ("case", "caseback") and p.thread_model == "plain":
                    self._pair_case_caseback_plain(name, lim)          # SPEC 8.3 exception (a)
                    continue
                if (a, b) == ("case", "tube") and p.tube_hole_d < p.tube_od:
                    self._pair_case_tube_reamed(name, lim)             # SPEC 8.3 exception (b)
                    continue
                try:
                    v = _ivol(self.wp(a), self.wp(b))
                except Exception as e:  # noqa: BLE001
                    self.error(g, name, e, blame=_blame_pair(a, b))
                    continue
                self.add(g, name, v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim, blame=_blame_pair(a, b))
        self._iring_crush(lim)
        self._seal_rows(lim)

    def _pair_case_caseback_plain(self, name: str, lim: str) -> None:
        """SPEC 8.3 (a), thread_model 'plain': the cosmetic boss (major O, SPEC 4) sits in the cosmetic bore (minor O,
        SPEC 3.6).  The pair is scored outside the thread-zone envelope z 0..cb_thread_len, r <= major/2 (exact faces;
        the split is checked to add up), and the overlap inside it must be the cosmetic tooth material."""
        p = self.p
        g = "interference"
        r_major, L = p.cb_thread_major / 2, p.cb_thread_len
        tv = cosmetic_tooth_volume(p, CB_LEAD_CHAMFER)
        env_txt = f"z 0..{L:g}, r <= {r_major:.3f}"
        try:
            inter = self.wp("case").intersect(self.wp("caseback"))
            raw = _vol(inter)
            if raw > 0.0:
                env = _cylinder_z(r_major, 0.0, L)
                inside = _ivol(inter, env)
                outside_cut = _vol(inter.cut(env))
            else:
                inside = outside_cut = 0.0
        except Exception as e:  # noqa: BLE001
            self.error(g, name, e)
            return
        outside = raw - inside
        consistent = abs(inside + outside_cut - raw) < BOOLEAN_TOL
        note = (f"thread_model 'plain' (SPEC 8.3 (a)): the overlap inside the envelope, {inside:.3f} mm^3, is the cosmetic "
                f"tooth material (next row); raw pair overlap {raw:.3f}")
        if not consistent:
            note += f"; BOOLEAN INCONSISTENT: inside {inside:.4f} + outside {outside_cut:.4f} != raw {raw:.4f}"
        self.add(g, f"{name} outside the thread-zone envelope ({env_txt}; SPEC 8.3 (a), thread_model plain)",
                 consistent and outside < INTERFERENCE_LIMIT, f"{outside:.4f} mm^3", lim, note,
                 blame="parts/caseback.py (flange / boss outside the thread zone) or parts/case_body.py")
        exp = tv["expected"]
        dev = abs(inside - exp) / exp if exp > 0 else float("inf")
        self.add(g, f"{name} inside the envelope equals the cosmetic tooth annulus pi/4(D^2-D1^2)L minus the lead chamfer "
                    f"(SPEC 8.3 (a))", dev < ANNULUS_TOL,
                 f"{inside:.3f} mm^3 vs expected {exp:.3f} ({100 * dev:.2f} %)", f"within {100 * ANNULUS_TOL:.0f} %",
                 f"annulus {tv['annulus']:.3f} - lead chamfer {tv['chamfer']:.3f}; no slot term: the anti-rotation rib starts "
                 f"{p.key_z0 - p.cb_thread_len:.2f} above the thread zone, so the thread is continuous (SPEC 3.7); the machinist cuts "
                 f"M{p.cb_thread_major:g} x {p.cb_thread_pitch:g} in this material",
                 blame="parts/case_body.py (cosmetic bore, lead chamfer, a rib reaching into the thread zone) or parts/caseback.py (cosmetic boss)")

    def _pair_case_tube_reamed(self, name: str, lim: str) -> None:
        """SPEC 8.3 (b): the tube hole is printed undersize (tube_hole_d) and reamed to tube_od.  The pair is judged on the
        case with the hole reamed; the as-printed overlap must be exactly the ream allowance."""
        p = self.p
        g = "interference"
        allow = math.pi / 4 * (p.tube_od ** 2 - p.tube_hole_d ** 2) * p.tube_engagement
        try:
            raw = _ivol(self.wp("case"), self.wp("tube"))
            reamer = _cylinder_x(p.tube_od / 2, 0.0, p.z_stem, p.x_bore_wall - 1.0, p.x_pocket_floor + 1.0)
            reamed = _one_valid(self.wp("case").cut(reamer), "reamed case")
            removed = self.built["case_body"].volume - _vol(reamed)
            v = _ivol(reamed, self.wp("tube"))
        except Exception as e:  # noqa: BLE001
            self.error(g, name, e)
            return
        self.add(g, f"{name} after reaming the printed {p.tube_hole_d:g} hole to {p.tube_od:g} (SPEC 8.3 (b))",
                 v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim,
                 f"reamer O {p.tube_od:g} along the tube axis from the movement bore out through the pocket floor "
                 f"({removed:.4f} mm^3 removed); the tube is pressed into the reamed hole (SPEC 1 / 11)",
                 blame="parts/case_body.py (tube hole) or parts/purchased.py (tube)")
        dev = abs(raw - allow) / allow if allow > 0 else float("inf")
        self.add(g, f"{name} as printed equals the ream allowance pi/4(tube_od^2 - tube_hole_d^2) x tube_engagement (SPEC 8.3 (b))",
                 dev < REAM_TOL, f"{raw:.4f} mm^3 vs expected {allow:.4f} ({100 * dev:.2f} %)", f"within {100 * REAM_TOL:.0f} %",
                 f"hole {p.tube_hole_d:g} -> {p.tube_od:g} over the {p.tube_engagement:.2f} wall between the movement bore and "
                 f"the crown-pocket floor",
                 blame="parts/case_body.py (tube hole diameter / position) or parts/purchased.py (tube)")

    def _iring_crush(self, lim: str) -> None:
        p = self.p
        g = "interference"
        if not self.has("case", "iring"):
            return
        od = p.crystal_d + 2 * p.iring_wall
        crush = math.pi / 4 * (od ** 2 - p.crystal_bore_d ** 2) * p.iring_h if od > p.crystal_bore_d else 0.0
        try:
            v = _ivol(self.wp("case"), self.wp("iring"))
        except Exception as e:  # noqa: BLE001
            self.error(g, "case/iring", e)
            return
        dev = abs(v - crush) / crush if crush else v
        self.add(g, "case/iring equals the designed I-ring crush (uncompressed OD vs bore)", dev < IRING_CRUSH_TOL,
                 f"{v:.4f} mm^3 vs expected {crush:.4f}", f"within {100 * IRING_CRUSH_TOL:.0f} %",
                 f"I-ring OD {od:.2f} in the {p.crystal_bore_d:.2f} bore: {od - p.crystal_bore_d:.2f} diametral crush",
                 blame="parts/case_body.py (crystal bore) or parts/purchased.py (iring)")

    def _seal_rows(self, lim: str) -> None:
        """The selected seal (SPEC 4 / 6 / 8.3) against the case and the caseback.  Inside its recess / groove (z <= 0) the
        overlap with the case is 0 by construction -- the case has no material below its back face -- so that SPEC 8.3
        exclusion is reported, not scored.  Scored: the uncompressed protrusion past z = 0 meets the back face over the
        whole seating band except the thread mouth (lead chamfer) and the helix groove run-out (seal_seat_model, within
        SEAL_SEAT_TOL; in v0.3 nothing else interrupts the back face, SPEC 3.7); the seal's inner radius clears the lead
        chamfer by SEAL_ID_MARGIN; and the seal lies in its caseback seat without touching its walls (flat gasket; the
        O-ring's own seat is informational)."""
        p = self.p
        g = "interference"
        seal = self.seal
        if seal is None or seal not in self.built:
            return
        r_si, r_so, depth, seat = seal_seat(p)
        protrusion, v_out_exp, r_in, r_out = seal_protrusion(p)
        is_gasket = p.cb_seal == "flat_gasket"
        helix = p.thread_model == "helix"
        r_crest = p.cb_thread_major / 2 - (p.cb_thread_clearance if helix else 0.0)          # boss crest
        r_thread_out = p.cb_thread_major / 2 + (p.cb_thread_clearance if helix else 0.0)     # thread's outer radius in the case
        r_mouth = p.cb_thread_minor / 2 + CB_LEAD_CHAMFER                                    # lead chamfer on the back face
        wp_seal = self.wp(seal)
        cl_in, cl_out = r_in - r_crest, r_so - r_out
        if is_gasket:
            seat_note = (f"gasket ID {p.gasket_id:g} / OD {p.gasket_od:g} x {p.gasket_t:g} in the {seat} r {r_si:.3f}..{r_so:.3f} x "
                         f"{depth:g} deep: radial clearance {cl_in:+.3f} to the boss crest (r {r_crest:.3f}), {cl_out:+.3f} to the "
                         f"recess wall")
        else:
            seat_note = (f"O-ring CS {p.oring_cs:g} on mean O {p.oring_groove_mean_d:g} in the {seat} r {r_si:.3f}..{r_so:.3f} x "
                         f"{depth:g} deep: radial clearance {cl_in:+.3f} to the boss crest, {cl_out:+.3f} to the groove wall; "
                         f"the uncompressed section stands {protrusion:.3f} into the groove floor by the placeholder's construction")
        # -- against the case: below the back face (0 by construction, reported) and the protruding part (scored)
        if self.has("case"):
            case = self.wp("case")
            z_case = self.bbox("case").zmin
            z_case = 0.0 if abs(z_case) < 1e-6 else z_case                                 # no "-0.000" in the note
            try:
                seal_in = _one_valid(wp_seal.intersect(_box(-p.W, p.W, -p.L, p.L, -p.cb_flange_t - 1.0, 0.0)),
                                     f"{seal} clipped to z <= 0")
                v_case_in = _ivol(seal_in, case)
                v_case_all = _ivol(wp_seal, case)
            except Exception as e:  # noqa: BLE001
                self.error(g, f"case/{seal}", e)
            else:
                v_out = v_case_all - v_case_in
                self.add(g, f"case/{seal} inside the {seat} (z <= 0; the {protrusion:.2f} protrusion past z = 0 is excluded, SPEC 8.3)", None,
                         f"{v_case_in:.4f} mm^3", "",
                         f"0 by construction for any seal size or position: the case has no material below its back face "
                         f"(case z >= {z_case:.3f}), so this exclusion cannot fail and is not scored; the protruding part is scored next")
                try:
                    md = seal_seat_model(p, CB_LEAD_CHAMFER)
                except Exception as e:  # noqa: BLE001
                    self.error(g, f"case/{seal}: seating-band model", e,
                               blame="params.py (see the message) or verify.py (seal_seat_model)")
                else:
                    exp = md["expected"]
                    dev = abs(v_out - exp) / exp if exp > 0 else float("inf")
                    clip_ok = v_out_exp > 0 and abs(md["proud"] - v_out_exp) / v_out_exp < 0.01     # model vs closed form
                    band = r_out - r_in
                    note = (f"protruding part (from params.py) {md['proud']:.3f} mm^3 (closed form {v_out_exp:.3f}) - lead chamfer "
                            f"{md['chamfer']:.3f} (mouth r {r_mouth:.3f} vs seal inner r {r_in:.3f}) - thread zone {md['thread']:.3f}; the "
                            f"back face is otherwise continuous (SPEC 3.7: the rib sits on the bore wall above the thread zone, no keyway mouth)")
                    if md["thread"] > INTERFERENCE_LIMIT:
                        note += (f" (the M{p.cb_thread_major:g} x {p.cb_thread_pitch:g} groove, root r {r_thread_out:.3f}, runs out at the "
                                 f"back face and interrupts the inner {max(0.0, r_thread_out - r_in):.2f} of the {band:.2f} wide band; the "
                                 f"seat rows judge that fit)")
                    if not clip_ok:
                        note += " -- MODEL INCONSISTENT with the closed form, the row cannot be trusted"
                    self.add(g, f"case/{seal}: the protruding part (z 0..{protrusion:.2f}, uncompressed) meets the case back face over the "
                                f"whole seating band but the thread mouth (lead chamfer) and the thread groove run-out (SPEC 4 / 6 / 8.3; "
                                f"no keyway in v0.3)",
                             clip_ok and dev < SEAL_SEAT_TOL, f"{v_out:.4f} mm^3 vs expected {exp:.4f} ({100 * dev:.2f} %)",
                             f"within {100 * SEAL_SEAT_TOL:.0f} %", note,
                             blame="parts/purchased.py (seal placement) or parts/case_body.py (back face: thread zone, lead chamfer, "
                                   "anything cut into the seating band)")
        # -- the seal's inner radius vs the thread mouth (params only)
        cl = r_in - r_mouth
        inner = "gasket_id" if is_gasket else "(oring_groove_mean_d - oring_cs)"
        self.add(g, f"{seal} inner radius clears the thread-mouth lead chamfer: {inner}/2 >= cb_thread_minor/2 + {CB_LEAD_CHAMFER:g} "
                    f"+ {SEAL_ID_MARGIN:g}", cl >= SEAL_ID_MARGIN - 1e-9, f"{cl:+.3f} mm", f">= {SEAL_ID_MARGIN:g}",
                 f"seal inner r {r_in:.3f} vs chamfer mouth r {r_mouth:.3f} (minor r {p.cb_thread_minor / 2:.3f} + {CB_LEAD_CHAMFER:g}); the "
                 f"seating band r {r_in:.2f}..{r_out:.2f} is continuous outside the thread's outer radius {r_thread_out:.3f}: "
                 f"{max(0.0, r_out - max(r_in, r_thread_out)):.2f} of its {r_out - r_in:.2f} width",
                 blame="params.py (gasket_radial_clear / oring_groove_mean_d, oring_cs vs cb_thread_major, cb_thread_pitch)")
        # -- against the caseback (the seal lies in its recess / groove)
        if self.has("caseback"):
            try:
                v_cb = _ivol(wp_seal, self.wp("caseback"))
            except Exception as e:  # noqa: BLE001
                self.error(g, f"{seal}/caseback", e)
                return
            if is_gasket:
                blame = ("params.py (gasket_radial_clear / gasket_t vs cb_thread_major / gasket_recess_od / gasket_recess_depth; "
                         "gasket_id / gasket_od are derived) or parts/caseback.py (recess) / parts/purchased.py (gasket)")
                self.add(g, f"{seal}/caseback: the gasket lies in the {seat} without touching its walls (SPEC 4 / 8.3)",
                         v_cb < INTERFERENCE_LIMIT, f"{v_cb:.4f} mm^3", lim, seat_note, blame=blame)
            else:
                self.add(g, f"{seal}/caseback (uncompressed O-ring in the {seat})", None, f"{v_cb:.4f} mm^3", "", seat_note)

    # ------------------------------------------------------------------ 3b. ring insertion sweep, axial float
    def run_insertion(self) -> None:
        print(f"\n== ring insertion sweep and axial float (SPEC 8.3b) ({self.profile})")
        p = self.p
        g = "insertion"
        lim = f"< {INTERFERENCE_LIMIT} mm^3"
        rows = [
            ("ring_lip_wall >= 0.3", p.ring_lip_wall, 0.3, "(ring_od - ring_dial_recess_d)/2: the rehaut lip around the dial",
             "params.py (dial_d / ring_dial_clearance vs mvt_bore_d / ring_od_clearance)"),
            ("dial_ledge_overlap >= 0.25", p.dial_ledge_overlap, 0.25, "(dial_d - dial_aperture_d)/2 hidden under the ledge",
             "params.py (dial_d / dial_aperture_d)"),
            ("flange_to_strap_margin >= 0.3", p.flange_to_strap_margin, 0.3,
             f"caseback flange r {p.cb_flange_d / 2:.2f} to the strap opening at y {p.y_strap_open_inner:.2f}",
             "params.py (cb_flange_d / strap_bar_back_w / strap_gap / strap_angle_deg)"),
            ("strap_bar_end_h >= 2.5", p.strap_bar_end_h, 2.5, "bar height on the end face (SPEC 8.2 asks >= 1.5)",
             "params.py (strap_bar_back_w / strap_angle_deg / strap_gap)"),
        ]
        for name, val, lm, note, blame in rows:
            self.add(g, f"{name} (SPEC 8.3b)", val >= lm - 1e-9, val, f">= {lm:.3f}", note, blame=blame)
        if not self.has("ring", "case"):
            self.add(g, "ring insertion sweep (SPEC 8.3b)", False, "ring or case not built", lim,
                     blame="parts/spacer_ring.py or parts/case_body.py")
            return
        ring, case = self.wp("ring"), self.wp("case")
        rb = self.bbox("ring")
        kg = keyway(p)
        # -- sweep: translate toward the back in SWEEP_STEP steps until the ring top is at or below the back face
        n_steps = max(1, math.floor(rb.zmax / SWEEP_STEP + 1e-9) + 1)     # last position: ring top strictly below z = 0
        vols: list[tuple[float, float]] = []
        errors: list[str] = []
        for k in range(1, n_steps + 1):
            dz = -SWEEP_STEP * k
            try:
                v = _ivol(ring.translate((0, 0, dz)), case)
            except Exception as e:  # noqa: BLE001
                errors.append(f"dz {dz:+.2f}: {_exc(e)}")
                print(f"    dz {dz:+.2f}: error {_exc(e)}")
                continue
            vols.append((dz, v))
            print(f"    dz {dz:+.2f}: ring/case {v:.4f} mm^3 (ring z {rb.zmin + dz:.2f}..{rb.zmax + dz:.2f})")
        bad = [(dz, v) for dz, v in vols if v >= INTERFERENCE_LIMIT]
        worst_dz, worst = max(vols, key=lambda t: t[1]) if vols else (0.0, float("nan"))
        ok = not errors and not bad and len(vols) == n_steps
        note = (f"keyway open to the back face, {kg['kw']:.2f} wide, floor r {kg['r_floor']:.3f} vs key tip r {kg['r_key_tip']:.3f}; "
                f"thread minor r {p.cb_thread_minor / 2:.3f} vs ring OD r {p.ring_od / 2:.3f}")
        if bad:
            note += "; interferes at " + ", ".join(f"dz {dz:+.2f} ({v:.4f})" for dz, v in bad)
        if errors:
            note += "; errors: " + "; ".join(errors)
        self.add(g, f"ring insertion sweep: ring translated toward the back in {SWEEP_STEP:g} steps, {n_steps} positions "
                    f"dz {-SWEEP_STEP:+.2f} .. {-SWEEP_STEP * n_steps:+.2f} (fully outside the case at the last, ring top z {rb.zmax - SWEEP_STEP * n_steps:.2f}), never touching the case (SPEC 8.3b)",
                 ok, f"worst {worst:.4f} mm^3 at dz {worst_dz:+.2f}", lim, note,
                 blame="parts/case_body.py (keyway / thread zone / lead chamfer) or parts/spacer_ring.py (OD / key)")
        # -- axial float: the ring is clamped between the ledge underside and the caseback inner face
        d_top = rb.zmax - p.z_ledge_bottom
        self.add(g, "axial float: ring top = z_ledge_bottom (SPEC 8.3b)", abs(d_top) < FLOAT_TOL,
                 f"{rb.zmax:.4f} vs {p.z_ledge_bottom:.4f} ({d_top:+.4f})", f"+-{FLOAT_TOL:g}",
                 "the ledge underside stops the ring", blame="parts/spacer_ring.py")
        if self.has("caseback"):
            cbb = self.bbox("caseback")
            d_bot = cbb.zmax - rb.zmin
            self.add(g, "axial float: caseback inner face = ring bottom (SPEC 8.3b)", abs(d_bot) < FLOAT_TOL,
                     f"{cbb.zmax:.4f} vs {rb.zmin:.4f} ({d_bot:+.4f})", f"+-{FLOAT_TOL:g}",
                     "the caseback clamps the ring against the ledge", blame="parts/caseback.py or parts/spacer_ring.py")
        r_lip = (p.ring_dial_recess_d / 2 + p.ring_od / 2) / 2
        s_case = self.built["case_body"].solid
        above = s_case.isInside(cq.Vector(0.0, r_lip, p.z_ledge_bottom + 0.05))
        below = s_case.isInside(cq.Vector(0.0, r_lip, p.z_ledge_bottom - 0.05))
        self.add(g, "axial float: case ledge underside at z_ledge_bottom over the ring lip (solid 0.05 above, void 0.05 below)",
                 above and not below, f"r {r_lip:.2f}: above {'solid' if above else 'void'}, below {'solid' if below else 'void'}",
                 "solid / void", "", blame="parts/case_body.py (movement bore / ledge)")
        s_ring = self.built["spacer_ring"].solid
        y_wall = (p.ring_pocket_l / 2 + p.ring_od / 2) / 2
        step_under = s_ring.isInside(cq.Vector(0.0, y_wall, p.z_dial_seat - 0.05))
        step_over = s_ring.isInside(cq.Vector(0.0, y_wall, p.z_dial_seat + 0.05))
        self.add(g, "axial float: ring step (dial seat) at z_dial_seat (solid 0.05 below, void 0.05 above, in the pocket wall)",
                 step_under and not step_over,
                 f"r {y_wall:.2f}: below {'solid' if step_under else 'void'}, above {'solid' if step_over else 'void'}",
                 "solid / void", "", blame="parts/spacer_ring.py")
        if self.has("dial"):
            db = self.bbox("dial")
            self.add(g, "axial float: dial rests on the ring step at z_dial_seat", abs(db.zmin - p.z_dial_seat) < FLOAT_TOL,
                     f"{db.zmin:.4f} vs {p.z_dial_seat:.4f} ({db.zmin - p.z_dial_seat:+.4f})", f"+-{FLOAT_TOL:g}", "",
                     blame="parts/dial_blank.py")
        if self.has("movement", "caseback"):
            fl = self.bbox("movement").zmin - self.bbox("caseback").zmax
            self.add(g, "axial float: movement back - caseback inner face = mvt_axial_clearance (the movement / dial cannot float more)",
                     abs(fl - p.mvt_axial_clearance) < FLOAT_TOL, f"{fl:.4f} vs {p.mvt_axial_clearance:.4f}", f"+-{FLOAT_TOL:g}",
                     "movement top = dial seat = ring step; the caseback clamps ring -> ledge and movement -> dial (SPEC 11)",
                     blame="parts/purchased.py (movement) or parts/caseback.py")

    # ------------------------------------------------------------------ 4. thread screw-in
    def run_thread(self) -> None:
        print(f"\n== thread screw-in test ({self.profile})")
        p = self.p
        g = "thread"
        lim = f"< {INTERFERENCE_LIMIT} mm^3"
        if p.thread_model != "helix":
            self.add(g, "screw-in test", None, "n/a", "",
                     f"thread_model={p.thread_model!r}: cosmetic bores only (M{p.cb_thread_major} x {p.cb_thread_pitch} "
                     "is cut by the machinist); the helix is tested on the resin profile")
            return
        if not self.has("case", "caseback"):
            self.add(g, "screw-in test", False, "case or caseback not built", lim, blame="parts/case_body.py or parts/caseback.py")
            return
        P = p.cb_thread_pitch
        case = self.wp("case")
        cb = self.wp("caseback")
        try:
            boss = threads.boss(p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                                clearance=p.cb_thread_clearance, z0=0.0)
            zone = case.intersect(cq.Workplane("XY").box(p.W + 2, p.L + 2, p.cb_thread_len, centered=(True, True, False)))
            _one_valid(zone, "threaded zone slice")
        except Exception as e:  # noqa: BLE001
            self.error(g, "thread test set-up (bare boss, threaded zone)", e)
            return
        # Right-hand helix about +Z: the thread surface is invariant under (rotate +t, translate +t/360*P).
        # Screwing IN advances the caseback toward +Z; the seated position (theta 0) is where the flange meets the
        # back face, so the positions the caseback really passes through are BEHIND the seat: rotate theta in the
        # unscrewing sense (-theta about +Z) with the matching axial retreat -theta/360*P.
        for theta in (0.0, 120.0, 240.0):
            dz = theta / 360.0 * P
            try:
                moved = cb.rotate((0, 0, 0), (0, 0, 1), -theta).translate((0, 0, -dz))
                v = _ivol(moved, case)
                self.add(g, f"caseback rotated {theta:.0f} deg, translated {dz:.3f} mm along the screw-in path / case",
                         v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim,
                         "seated" if theta == 0 else f"{theta / 360:.2f} turn before seating",
                         blame="parts/case_body.py or parts/caseback.py (thread)")
            except Exception as e:  # noqa: BLE001
                self.error(g, f"screw-in {theta:.0f} deg", e)
        # the same alignment on the advancing side, judged against the threaded zone only (the seat excluded)
        for theta in (120.0, 240.0):
            dz = theta / 360.0 * P
            try:
                moved = boss.rotate((0, 0, 0), (0, 0, 1), theta).translate((0, 0, dz))
                v = _ivol(moved, zone)
                self.add(g, f"bare boss rotated +{theta:.0f} deg, advanced +{dz:.3f} mm / threaded zone z 0..{p.cb_thread_len}",
                         v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim, "thread flanks only, seat excluded",
                         blame="parts/case_body.py or parts/threads.py")
                v_full = _ivol(moved, case)
                self.add(g, f"bare boss advanced +{dz:.3f} mm past the seat / whole case", None, f"{v_full:.4f} mm^3", "",
                         "informational: the boss cannot go deeper than seated; the overlap is the movement-bore wall "
                         "above the thread zone, not the thread")
            except Exception as e:  # noqa: BLE001
                self.error(g, f"advance {theta:.0f} deg", e)
        # negative control: half a turn out of phase without the axial shift must clash
        try:
            bad = boss.rotate((0, 0, 0), (0, 0, 1), 180.0)
            v = _ivol(bad, case)
            self.add(g, "negative control: boss rotated 180 deg, not translated / case", v > 1.0, f"{v:.3f} mm^3",
                     "> 1 mm^3", "proves the intersection test can see a clash", blame="verify.py / parts/threads.py")
        except Exception as e:  # noqa: BLE001
            self.error(g, "negative control", e)
        self.add(g, "thread fit", None,
                 f"M{p.cb_thread_major} x {P}, radial clearance {p.cb_thread_clearance}, "
                 f"{p.cb_thread_len / P:.1f} turns engaged", "", "")

    # ------------------------------------------------------------------ 5. probes
    def _case_probes(self) -> tuple[list, list]:
        p = self.p
        a = p.strap_angle
        c, s = math.cos(a), math.sin(a)
        r_crystal = p.crystal_bore_d / 2
        r_apert = p.dial_aperture_d / 2
        r_mvt = p.mvt_bore_d / 2
        r_minor = p.cb_thread_minor / 2
        r_thread_out = p.cb_thread_major / 2 + (p.cb_thread_clearance if p.thread_model == "helix" else 0.0)
        half = p.strap_slot_w / 2
        kg = keyway(p)
        empty, solid = [], []
        for sy in (1, -1):
            tag = "12" if sy > 0 else "6"
            for t in (1.0, 2.5):
                empty.append((f"strap channel {tag} o'clock, on the axis {t:.1f} mm from the back face",
                              (0.0, sy * (p.y_strap_axis_back + t * c), t * s)))
            for sx in (1, -1):
                empty.append((f"strap channel {tag} o'clock, near the x = {sx * (half - 0.5):+.1f} end",
                              (sx * (half - 0.5), sy * (p.y_strap_axis_back + 1.5 * c), 1.5 * s)))
            solid.append((f"strap bar {tag} o'clock", (0.0, sy * (p.y_end - 1.0), 0.6)))
            solid.append((f"strap bar {tag} o'clock, near the channel end x = 11.0", (11.0, sy * (p.y_end - 1.0), 0.6)))
            solid.append((f"wall above the channel exit {tag} o'clock", (0.0, sy * (p.y_end - 1.0), p.H - 1.5)))
            solid.append((f"end-face flat beside the channel {tag} o'clock", (half + 0.3, sy * (p.y_end - 0.6), p.H / 2)))
            solid.append((f"wall between the thread zone and the channel {tag} o'clock",
                          (0.0, sy * (r_thread_out + p.y_strap_open_inner) / 2, 0.75)))
        empty.append(("crown pocket, centre", ((p.x_pocket_floor + p.x_flank) / 2, 0.0, p.H / 2)))
        for sy in (1, -1):
            empty.append((f"crown pocket, y = {sy * (p.crown_pocket_w / 2 - 0.8):+.1f}",
                          (p.x_pocket_floor + 0.3, sy * (p.crown_pocket_w / 2 - 0.8), p.H / 2)))
        x_tube = (r_mvt + p.x_pocket_floor) / 2
        empty.append(("tube hole, on the axis", (x_tube, 0.0, p.z_stem)))
        empty.append(("tube hole, 0.1 inside the wall", (x_tube, 0.0, p.z_stem + p.tube_hole_d / 2 - 0.1)))
        # SPEC 3.7 keyway: open from the back face through the thread zone up to z1; probed just inside its floor
        # (beyond the lead chamfer cone) and on the ring key's own path
        x_deep = -(kg["r_floor"] - 0.15)
        x_key = -(p.ring_od / 2 + p.ring_key_h / 2)
        empty.append(("ring keyway mouth on the back face (z 0.05, SPEC 3.7)", (x_deep, 0.0, 0.05)))
        empty.append(("ring keyway through the thread zone", (x_deep, 0.0, p.cb_thread_len / 2)))
        empty.append(("ring keyway above the thread zone, on the key path", (x_key, 0.0, p.z_cb_inner + p.ring_key_len / 2)))
        empty.append(("ring keyway just under its top", (x_key, 0.0, kg["z1"] - 0.1)))
        solid.append(("wall above the keyway top", (x_key, 0.0, kg["z1"] + 0.1)))
        solid.append(("wall behind the keyway floor", (-(kg["r_floor"] + 0.3), 0.0, p.cb_thread_len / 2)))
        solid.append(("wall beside the keyway in the thread zone", (-(r_thread_out + 0.6), kg["kw"] / 2 + 0.3, p.cb_thread_len / 2)))
        solid.append(("wall beside the keyway above the thread zone", (-(kg["r_floor"] + 0.3), 0.0, p.z_cb_inner + p.ring_key_len / 2)))
        for sx, sy in p.gate_corners:
            tag = f"({sx:+d},{sy:+d})"
            cx, cy = p.front_slot_center(sx, sy)
            if p.front_slot:
                empty.append((f"front slot {tag}", (cx, cy, p.H - p.front_slot_depth / 2)))
                solid.append((f"under the front slot {tag}", (cx, cy, p.H - p.front_slot_depth - 0.5)))
            mx, my = p.chamfer_mid(sx, sy)
            nx, ny = -sx / math.sqrt(2), -sy / math.sqrt(2)          # inward normal of the chamfer face
            if p.chamfer_notch:
                empty.append((f"chamfer notch {tag}", (mx + 0.4 * nx, my + 0.4 * ny, p.chamfer_notch_h / 2)))
                solid.append((f"behind the chamfer notch {tag}",
                              (mx + (p.chamfer_notch_depth + 0.5) * nx, my + (p.chamfer_notch_depth + 0.5) * ny, p.chamfer_notch_h / 2)))
                solid.append((f"above the chamfer notch {tag}", (mx + 0.4 * nx, my + 0.4 * ny, p.chamfer_notch_h + 0.5)))
            if p.back_groove:
                gx = sx * (p.W / 2 - p.back_groove_inboard)
                gy = sy * (p.L / 2 - p.corner_chamfer - 0.5 - p.back_groove_len / 2)
                empty.append((f"back groove {tag}", (gx, gy, p.back_groove_depth / 2)))
                solid.append((f"above the back groove floor {tag}", (gx, gy, p.back_groove_depth + 0.5)))
        empty.append(("crystal bore", (r_crystal - 0.3, 0.0, p.z_crystal_seat + 0.5)))
        empty.append(("dial aperture", (r_apert - 0.3, 0.0, (p.z_ledge_bottom + p.z_crystal_seat) / 2)))
        empty.append(("movement bore", (r_mvt - 0.3, 0.0, (p.z_cb_inner + p.z_ledge_bottom) / 2)))
        empty.append(("thread zone bore", (r_minor - 0.3, 0.0, p.cb_thread_len / 2)))
        empty.append(("crystal bore lead chamfer (r + 0.08 at z = H - 0.05)", (r_crystal + 0.08, 0.0, p.H - 0.05)))
        solid.append(("crown-side wall at the crystal bore, inside the pocket",
                      ((r_crystal + p.x_pocket_floor) / 2, 0.0, (p.z_crystal_seat + p.H) / 2 - 0.3)))
        solid.append(("crown-side wall at the thread zone, inside the pocket",
                      ((r_thread_out + p.x_pocket_floor) / 2, 0.0, p.cb_thread_len / 2)))
        solid.append(("ledge", (0.0, (r_apert + r_crystal) / 2, (p.z_ledge_bottom + p.z_crystal_seat) / 2)))
        solid.append(("crystal bore wall below the lead chamfer", (r_crystal + 0.08, 0.0, p.H - 0.6)))
        solid.append(("front face at the flank", (p.x_flank - 1.0, -8.0, p.H - 0.2)))
        return empty, solid

    def _caseback_probes(self) -> tuple[list, list]:
        """Tool holes, the SELECTED seal's recess / groove (SPEC 4), the rim land, the boss."""
        p = self.p
        z_out = -p.cb_flange_t
        rr = p.cb_tool_pcd / 2
        r_fl = p.cb_flange_d / 2
        r_si, r_so, depth, seat = seal_seat(p)
        r_mid = (r_si + r_so) / 2
        empty = [("tool hole at 12 o'clock", (0.0, rr, z_out + p.cb_tool_hole_depth - 0.05)),
                 (f"{seat}, mid width", (r_mid, 0.0, -depth / 2)),
                 (f"{seat} at its inner wall (r {r_si:.2f} + 0.1)", (0.0, r_si + 0.1, -depth + 0.05)),
                 (f"{seat} at its outer wall (r {r_so:.2f} - 0.1)", (0.0, r_so - 0.1, -depth + 0.05)),
                 ("above the boss", (0.0, 0.0, p.cb_thread_len + 0.1))]
        solid = [("boss, just under the inner face", (0.0, 5.0, p.cb_thread_len - 0.1)),
                 (f"flange under the {seat} floor", (r_mid, 0.0, -depth - 0.1)),
                 (f"flange inboard of the {seat}", (r_si - 0.1, 0.0, -depth / 2)),
                 (f"rim land outside the {seat}", ((r_so + r_fl) / 2, 0.0, -0.1)),
                 ("floor under the tool hole", (0.0, rr, z_out + p.cb_tool_hole_depth + 0.1))]
        if p.cb_seal == "oring":
            # the flat-gasket recess (boss .. gasket_recess_od) must NOT be cut: probe between the boss and the groove
            r_g = (p.cb_thread_major / 2 + r_si) / 2
            if r_si - p.cb_thread_major / 2 > 0.2:
                solid.append((f"no flat-gasket recess inboard of the O-ring groove (r {r_g:.2f})", (r_g, 0.0, -p.gasket_recess_depth / 2)))
        if p.cb_center_hole:
            empty.append(("centre dimple", (0.0, 0.0, z_out + p.cb_center_hole_depth / 2)))
        return empty, solid

    def _probe(self, g: str, part: str, s: cq.Solid, empty: list, solid: list, blame: str) -> None:
        for name, pt in empty:
            ins = s.isInside(cq.Vector(*pt))
            self.add(g, f"{part}: {name} is void", not ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}",
                     "void", blame=blame)
        for name, pt in solid:
            ins = s.isInside(cq.Vector(*pt))
            self.add(g, f"{part}: {name} is solid", ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}",
                     "solid", blame=blame)

    def run_probes(self) -> None:
        print(f"\n== probe points ({self.profile})")
        p = self.p
        g = "probes"
        if "case_body" not in self.built:
            self.add(g, "case body probes", False, "case not built", blame="parts/case_body.py")
        else:
            s = self.built["case_body"].solid
            empty, solid = self._case_probes()
            self._probe(g, "case", s, empty, solid, "parts/case_body.py")
            # thread lead chamfer: a ring of points just outside the minor radius at the mouth must all be void
            r = p.cb_thread_minor / 2 + 0.08
            hits = sum(s.isInside(cq.Vector(r * math.cos(t), r * math.sin(t), 0.05)) for t in [i * math.pi / 18 for i in range(36)])
            self.add(g, "case: thread lead chamfer (36 points at r_minor + 0.08, z = 0.05) all void", hits == 0,
                     f"{hits} solid hits / 36", "0", "SPEC 3.6 lead chamfer 0.2", blame="parts/case_body.py")
        if "spacer_ring" in self.built:
            try:
                from parts import spacer_ring
                empty, solid = spacer_ring.probe_points(p)
                self._probe(g, "ring", self.built["spacer_ring"].solid, empty, solid, "parts/spacer_ring.py")
            except Exception as e:  # noqa: BLE001
                self.error(g, "ring probes", e, blame="parts/spacer_ring.py")
        if "caseback" in self.built:
            try:
                empty, solid = self._caseback_probes()
                self._probe(g, "caseback", self.built["caseback"].solid, empty, solid, "parts/caseback.py")
            except Exception as e:  # noqa: BLE001
                self.error(g, "caseback probes", e, blame="params.py (cb_seal) or parts/caseback.py")
        if "dial_blank" in self.built:
            s = self.built["dial_blank"].solid
            zc = (p.z_dial_seat + p.z_dial_face) / 2
            self._probe(g, "dial", s, [("centre hole", (0.0, 0.0, zc))], [("disc", (p.dial_d / 4, 0.0, zc))], "parts/dial_blank.py")
        if self.seal == "gasket" and "gasket" in self.built:
            s = self.built["gasket"].solid
            zc = -p.gasket_recess_depth + p.gasket_t / 2
            self._probe(g, "gasket", s, [("bore (annulus centre)", (0.0, 0.0, zc)), ("inside the ID", (p.gasket_id / 2 - 0.05, 0.0, zc))],
                        [("ring section", ((p.gasket_id + p.gasket_od) / 4, 0.0, zc))], "parts/purchased.py")

    # ------------------------------------------------------------------ 6. STL
    def run_stl(self) -> None:
        print(f"\n== STL export and mesh checks ({self.profile}) -> {self.scratch}")
        g = "stl"
        os.makedirs(self.scratch, exist_ok=True)
        for name, b in self.built.items():
            path = os.path.join(self.scratch, f"{name}.stl")
            blame = f"parts/{'purchased' if b.kind == 'purchased' else name}.py"
            try:
                cq.exporters.export(b.wp, path, exportType="STL", tolerance=STL_TOLERANCE,
                                    angularTolerance=STL_ANGULAR_TOLERANCE)
                m = trimesh.load(path, force="mesh")
                wt = bool(m.is_watertight)
                mv = float(m.volume) if wt else float("nan")
                dev = abs(mv - b.volume) / b.volume if wt else float("nan")
                self.add(g, f"{name}: STL watertight", wt, f"{len(m.faces)} faces, watertight={wt}", "watertight", blame=blame)
                self.add(g, f"{name}: mesh volume within 1 % of the B-rep volume", wt and dev < STL_VOLUME_TOL,
                         f"mesh {mv:.2f} vs B-rep {b.volume:.2f} mm^3 ({100 * dev:.3f} %)" if wt else "n/a", "< 1 %", blame=blame)
            except Exception as e:  # noqa: BLE001
                self.error(g, f"{name}: STL export / mesh check", e, blame=blame)

    # ------------------------------------------------------------------ 7. masses
    def run_masses(self) -> None:
        print(f"\n== mass estimates ({self.profile})")
        p = self.p
        total = 0.0
        for name, b in self.built.items():
            attr = DENSITY_ATTR.get(name)
            dens = float(getattr(p, attr)) if attr else None
            mass = b.volume / 1000.0 * dens if dens is not None else None
            self.masses.append((name, b.material, b.volume, dens, mass))
            if mass is not None:
                total += mass
                self.add("mass", f"{name} mass", None, f"{mass:.2f} g ({b.volume:.1f} mm^3 x {dens:.2f} g/cm^3)", "", attr)
        self.add("mass", "made parts total", None, f"{total:.2f} g", "", "case body + caseback + spacer ring + dial blank")
        self.total_mass = total

    # ------------------------------------------------------------------ 8. report
    def write_report(self) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, "verify_report.md")
        p = self.p
        elapsed = time.perf_counter() - self.t0
        fails = self.failures
        n_scored = sum(1 for c in self.checks if c.ok is not None)
        lines: list[str] = []
        lines.append(f"# verify.py report -- profile `{self.profile}`")
        lines.append("")
        lines.append(f"Result: **{'PASS' if not fails else 'FAIL'}** -- {n_scored - len(fails)} / {n_scored} scored checks passed"
                     f", {len(fails)} failed; {len(self.checks) - n_scored} informational rows. "
                     f"Run {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')} in {elapsed:.0f} s. SPEC.md section 8 (v0.3).")
        lines.append("")
        lines.append(f"Profile: thread_model `{p.thread_model}`, M{p.cb_thread_major} x {p.cb_thread_pitch} "
                     f"(minor {p.cb_thread_minor:.3f}), tube hole {p.tube_hole_d}, hole_comp {p.hole_comp}, fit_extra {p.fit_extra}; "
                     f"seal `{p.cb_seal}` -> placeholder `{self.seal}`; densities case {p.density_case} / caseback {p.density_caseback} / "
                     f"ring {p.density_ring} / dial {p.density_dial} g/cm^3.")
        lines.append("")
        if fails:
            lines.append("## Failures")
            lines.append("")
            for c in fails:
                lines.append(f"* **{c.name}** -- {c.value}" + (f" (limit {c.limit})" if c.limit else "")
                             + (f". {c.note}" if c.note else "") + (f" **Suspected: {c.blame}**" if c.blame else ""))
            lines.append("")
            decisions = [c for c in fails if c.blame.startswith(("SPEC.md", "params.py"))]
            defects = [c for c in fails if c not in decisions]
            lines.append(f"{len(decisions)} of {len(fails)} failures need a SPEC.md / params.py decision (the part modules follow the "
                         f"SPEC as written); {len(defects)} are module / verifier defects.")
            lines.append("")
        groups = [("build", "1. Build (module contract, one valid solid)"),
                  ("placement", "1b. Placement in assembly coordinates (SPEC 3-7)"),
                  ("parametric", "2. Parametric assertions"),
                  ("interference", "3. Assembly interference (pairwise intersection volumes)"),
                  ("insertion", "3b. Ring insertion sweep and axial float (SPEC 8.3b)"),
                  ("thread", "4. Thread screw-in test"),
                  ("probes", "5. Probe points"),
                  ("stl", "6. STL export (trimesh)"),
                  ("mass", "7. Mass estimates")]
        n = 0
        for key, title in groups:
            rows = [c for c in self.checks if c.group == key]
            if not rows:
                continue
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| # | check | measured | limit | result | note |")
            lines.append("|---:|---|---|---|:---:|---|")
            for c in rows:
                n += 1
                cell = lambda t: str(t).replace("|", "\\|")  # noqa: E731
                note = c.note + (f" [suspected: {c.blame}]" if c.ok is False and c.blame else "")
                lines.append(f"| {n} | {cell(c.name)} | {cell(c.value)} | {cell(c.limit)} | {c.result} | {cell(note)} |")
            lines.append("")
        lines.append("## Mass table")
        lines.append("")
        lines.append("| part | material | volume mm^3 | density g/cm^3 | mass g |")
        lines.append("|---|---|---:|---:|---:|")
        for name, material, vol, dens, mass in self.masses:
            lines.append(f"| {name} | {material} | {vol:.1f} | {dens:.2f} | {mass:.2f} |" if mass is not None
                         else f"| {name} | {material} | {vol:.1f} | - | - |")
        lines.append(f"| **made parts total** | | | | **{getattr(self, 'total_mass', 0.0):.2f}** |")
        lines.append("")
        lines.append("## Module warnings (skipped edge breaks)")
        lines.append("")
        if self.module_warnings:
            for w in self.module_warnings:
                lines.append(f"* {w}")
        else:
            lines.append("* none")
        lines.append("")
        lines.append("## Parameters (params.summary())")
        lines.append("")
        lines.append("```")
        lines.append(p.summary())
        lines.append("```")
        lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path

    # ------------------------------------------------------------------ driver
    def run(self) -> bool:
        print(f"\n===== profile {self.profile} (thread_model {self.p.thread_model}, cb_seal {self.p.cb_seal}), report -> {self.out_dir}")
        for step in (self.run_build, self.run_placement, self.run_parametric, self.run_interference, self.run_insertion,
                     self.run_thread, self.run_probes, self.run_stl, self.run_masses):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - a crashed stage is a failure, not an abort
                self.error(step.__name__, f"{step.__name__} aborted", e)
        path = self.write_report()
        fails = self.failures
        print(f"\n{self.profile}: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s)); report {path}; "
              f"{time.perf_counter() - self.t0:.0f} s")
        for c in fails:
            print(f"  FAIL {c.name}: {c.value}" + (f" -- {c.note}" if c.note else "") + (f" [suspected: {c.blame}]" if c.blame else ""))
        return not fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=(*PROFILES, "all"), default="all")
    ap.add_argument("--out", default="out", help="report root; out/<profile>/verify_report.md (relative to this file)")
    ap.add_argument("--scratch", default=os.environ.get("TERRA_SCRATCH") or os.path.join(tempfile.gettempdir(), "terra-proto-verify"),
                    help="where the STLs for the mesh checks are written (default: $TERRA_SCRATCH or the system temp dir)")
    args = ap.parse_args(argv)
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    out_root = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
    ok = True
    t0 = time.perf_counter()
    for prof in profiles:
        ok = Verifier(prof, out_root, args.scratch).run() and ok
    print(f"\nverify: {'ALL PASSED' if ok else 'FAILURES'} in {time.perf_counter() - t0:.0f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
