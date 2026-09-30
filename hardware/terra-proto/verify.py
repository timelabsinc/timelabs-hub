#!/usr/bin/env python3
"""
Verify the Terra-style GL32 case prototype against SPEC.md section 8.

    python3 verify.py [--profile titanium|resin|all] [--out out] [--scratch DIR]

For every requested profile (p = params.get(profile)) this script

  1. builds every part module (parts/case_body, caseback, spacer_ring, dial_blank) and every purchased
     placeholder (parts/purchased.PARTS) through the module contract and requires ONE valid solid each,
     with a well-formed bom(); then checks that every solid sits where SPEC sections 3-7 place it in
     assembly coordinates (bounding box against params.py);
  2. runs the parametric assertions of SPEC section 8.2, the section 3.6 rule (thread minor >= movement
     bore - 0.05) and the section 3 / 5 sanity walls, straight from params.py; when the spacer-ring lip row
     fails, an informational what-if (never scored) rebuilds the ring and the dial through params.get overrides
     with the params.py change the row recommends (WHATIF_RING) and shows the SPEC 5 ring then exists and
     clears its neighbours -- params.py itself is never touched;
  3. computes the pairwise assembly interference volumes of SPEC section 8.3 (plus a few extra pairs) with
     OCC booleans and fails any pair >= 0.01 mm^3.  Overlaps that the SPEC itself builds in (the cosmetic
     thread annulus of the titanium profile, the ream allowance of the resin tube hole, the uncompressed
     I-ring / O-ring) are measured against their expected value in additional rows, but the section 8.3
     rows are never relaxed;
  4. on the helix (resin) profile runs the thread screw-in test: the caseback rotated theta about Z and
     translated theta/360 * pitch along the screw-in path for theta in {0, 120, 240}, the bare boss advanced
     against the threaded zone, and a negative control (180 deg, no advance) that must interfere;
  5. probes points: every strap channel, front slot, the crown pocket, the tube hole, the keyway, the gate
     details, the bores and both lead chamfers must be void; every wall that must be solid must be solid;
     the spacer ring, caseback and dial get their own probes;
  6. exports every solid to STL in the scratch directory (same tessellation as build.py), checks it is
     watertight with trimesh and that the mesh volume is within 1 % of the B-rep volume;
  7. estimates masses from the volumes and density_* (dial blank: density_ring, as build.py does);
  8. writes out/<profile>/verify_report.md and exits 1 if any check failed in any profile.  Every failed
     row names the file that has to change (a part module, or SPEC.md / params.py when the SPEC itself
     asks for two incompatible things), so the report separates module defects from design decisions.

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
import trimesh  # noqa: E402

import params  # noqa: E402
from params import Params  # noqa: E402
from parts import threads  # noqa: E402

# --------------------------------------------------------------------------- configuration
PROFILES = ("titanium", "resin")
PART_MODULES = ("parts.case_body", "parts.caseback", "parts.spacer_ring", "parts.dial_blank")
PURCHASED_MODULE = "parts.purchased"
PURCHASED_NAMES = ("crystal", "iring", "oring", "tube", "crown", "movement", "hands_envelope", "battery")
BOM_KEYS = ("item", "spec", "qty", "source")

DENSITY_ATTR = {                    # g/cm3, same mapping as build.py
    "case_body": "density_case",
    "caseback": "density_caseback",
    "spacer_ring": "density_ring",
    "dial_blank": "density_ring",   # params.py has no brass density; build.py uses density_ring too
}

INTERFERENCE_LIMIT = 0.01           # mm^3, SPEC 8.3 / 8.4
STL_TOLERANCE = 0.01                # same tessellation as build.py
STL_ANGULAR_TOLERANCE = 0.1
STL_VOLUME_TOL = 0.01               # 1 %, SPEC 8.1
CB_LEAD_CHAMFER = 0.2               # the lead chamfer case_body.py passes to threads (SPEC 3.6)
RING_LIP_MIN = 0.30                 # parts/spacer_ring.LIP_WALL_MIN: thinnest rehaut lip that can be made
WHATIF_RING = {"dial_d": 25.0, "dial_aperture_d": 24.6}   # params.py change the lip row recommends; tried informationally only
PLACEMENT_TOL = 0.02                # mm, bounding box of every solid against its SPEC position
BRASS_DENSITY = 8.5                 # g/cm3, informational only: params.py carries no brass density (SPEC 8.6 uses density_*)

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


def _ivol(a: cq.Workplane, b: cq.Workplane) -> float:
    """Volume of a INTERSECT b (0 when disjoint)."""
    r = a.intersect(b)
    return sum(s.Volume() for s in r.solids().vals())


def _cylinder_z(r: float, z0: float, z1: float) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).circle(r).extrude(z1 - z0)


def _cylinder_x(r: float, y: float, z: float, x0: float, x1: float) -> cq.Workplane:
    return cq.Workplane("YZ", origin=(x0, 0, 0)).center(y, z).circle(r).extrude(x1 - x0)


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
        self.t0 = time.perf_counter()

    # -- helpers
    def add(self, group: str, name: str, ok, value, limit: str = "", note: str = "", blame: str = "") -> Check:
        c = Check(group, name, ok, value if isinstance(value, str) else _fmt(value), limit, note, blame)
        self.checks.append(c)
        tag = c.result
        print(f"  [{tag:4s}] {name}: {c.value}" + (f"  (limit {limit})" if limit else "") + (f"  -- {note}" if note else ""))
        return c

    def error(self, group: str, name: str, e: BaseException) -> Check:
        tb = traceback.format_exception(type(e), e, e.__traceback__, limit=-3)
        print("".join(tb), file=sys.stderr)
        return self.add(group, name, False, "error", "", _exc(e), blame=f"parts / verify.py ({group})")

    def wp(self, short: str) -> cq.Workplane:
        return self.built[ALIAS.get(short, short)].wp

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
                self.error(g, f"{stem}: import", e)
                continue
            missing = [a for a in ("PART", "MATERIAL", "build", "bom") if not hasattr(mod, a)]
            if missing:
                self.add(g, f"{stem}: module contract", False, f"missing {missing}", "PART, MATERIAL, build, bom")
                continue
            name = str(mod.PART)
            if name != stem:
                self.add(g, f"{stem}: PART name", False, name, stem)
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
            self.error(g, "purchased: import", e)
            return
        parts_dict = getattr(pmod, "PARTS", None)
        if not isinstance(parts_dict, dict) or not hasattr(pmod, "bom"):
            self.add(g, "purchased: module contract", False, "no PARTS dict / bom()", "PARTS, bom")
            return
        missing = [n for n in PURCHASED_NAMES if n not in parts_dict]
        self.add(g, "purchased: PARTS covers SPEC section 6", not missing,
                 "all present" if not missing else f"missing {missing}", ", ".join(PURCHASED_NAMES))
        for name, fn in parts_dict.items():
            material = pmod.MATERIAL.get(name, "purchased") if isinstance(getattr(pmod, "MATERIAL", None), dict) else "purchased"
            self._build_one(str(name), fn, material, "purchased")
        self._check_bom("purchased", pmod)

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
            self.error("build", f"{name}: one valid solid", e)
            self.checks[-1].blame = f"parts/{'purchased' if kind == 'purchased' else name}.py"
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
            self.error("build", f"{name}: bom()", e)
            return
        ok = isinstance(rows, list) and all(isinstance(r, dict) and all(k in r for k in BOM_KEYS) for r in rows)
        self.add("build", f"{name}: bom() rows", ok, f"{len(rows) if isinstance(rows, list) else '?'} rows",
                 "list of dicts with item/spec/qty/source")

    # ------------------------------------------------------------------ 1b. placement
    def run_placement(self) -> None:
        """Bounding box of every built solid against the position SPEC sections 3-7 give it (params.py values)."""
        print(f"\n== placement in assembly coordinates ({self.profile})")
        p = self.p
        g = "placement"
        r_ir = p.crystal_d / 2 + p.iring_wall
        r_or = (p.oring_groove_mean_d + p.oring_cs) / 2
        z_or = -p.oring_groove_depth / 2
        expected = {
            "case_body": ("SPEC 3.1", dict(x=(-p.W / 2, p.W / 2), y=(-p.L / 2, p.L / 2), z=(0.0, p.H)), "parts/case_body.py"),
            "caseback": ("SPEC 4", dict(x=(-p.cb_flange_d / 2, p.cb_flange_d / 2), z=(-p.cb_flange_t, p.cb_thread_len)),
                         "parts/caseback.py"),
            "spacer_ring": ("SPEC 5: z_cb_inner .. z_ledge_bottom (ring_h)", dict(y=(-p.ring_od / 2, p.ring_od / 2),
                            z=(p.z_cb_inner, p.z_ledge_bottom)),
                            "params.py (dial_d vs mvt_bore_d): the lip fallback in parts/spacer_ring.py builds the short ring"),
            "dial_blank": ("SPEC 6", dict(x=(-p.dial_d / 2, p.dial_d / 2), z=(p.z_dial_seat, p.z_dial_face)), "parts/dial_blank.py"),
            "crystal": ("SPEC 6", dict(x=(-p.crystal_d / 2, p.crystal_d / 2), z=(p.z_crystal_seat, p.z_crystal_top)), "parts/purchased.py"),
            "iring": ("SPEC 6", dict(x=(-r_ir, r_ir), z=(p.z_crystal_seat, p.z_crystal_seat + p.iring_h)), "parts/purchased.py"),
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
        lip = (p.ring_od - p.ring_dial_recess_d) / 2
        ring_top = self.built["spacer_ring"].solid.BoundingBox().zmax if "spacer_ring" in self.built else None
        ring_top_note = "" if ring_top is None else f"; built ring top z = {ring_top:.2f} (SPEC 5: {p.z_ledge_bottom:.2f})"
        rows = [
            ("hands_clearance >= hands_clearance_min", p.hands_clearance, p.hands_clearance_min, ""),
            ("tube_to_thread_margin >= 0.1", p.tube_to_thread_margin, 0.1, "driven by UNVERIFIED stem_below_dial_seat"),
            ("cb_thread_minor >= ring_od + 0.1", p.cb_thread_minor, p.ring_od + 0.1, "ring passes the thread from the back"),
            ("cb_thread_minor >= mvt_bore_d - 0.05 (SPEC 3.6)", p.cb_thread_minor, p.mvt_bore_d - 0.05, ""),
            ("strap_front_wall >= 2.0", p.strap_front_wall, 2.0, "solid above the end-face exit"),
            ("strap_bar_end_h >= 1.5", p.strap_bar_end_h, 1.5, "bar height on the end face"),
            ("end_face_flat_half_w >= strap_slot_w/2 + 0.3", p.end_face_flat_half_w, p.strap_slot_w / 2 + 0.3, ""),
            ("crown-side wall at the crystal bore inside the pocket >= 1.0", wall_crystal, 1.0,
             f"x_pocket_floor {p.x_pocket_floor:.2f} - crystal_bore_d/2 {p.crystal_bore_d / 2:.3f}"),
            ("crown-side wall at the thread inside the pocket >= 1.0", wall_thread, 1.0,
             f"x_pocket_floor {p.x_pocket_floor:.2f} - thread outer radius {r_thread_out:.3f}"),
            ("ring pocket wall >= 2.5", ring_wall, 2.5, "(ring_od - ring_pocket_l)/2, thinnest at 12/6"),
            ("tube engagement >= 1.5", p.tube_engagement, 1.5, ""),
            (f"spacer ring rehaut lip wall >= {RING_LIP_MIN} (SPEC 5: ring full height, clamped by the caseback)",
             lip, RING_LIP_MIN, f"(ring_od {p.ring_od:.2f} - ring_dial_recess_d {p.ring_dial_recess_d:.2f})/2; "
                                f"below {RING_LIP_MIN} spacer_ring.py builds the short ring (top at the dial seat)"
                                f"{ring_top_note}"),
        ]
        blame = {
            rows[-1][0]: (f"params.py (dial_d {p.dial_d} vs mvt_bore_d {p.mvt_bore_d} / ring_od_clearance): a {p.dial_d} dial "
                          f"cannot sit inside a ring that itself fits the {p.mvt_bore_d} bore; dial_d <= {p.ring_od - 2 * 0.5 - 0.2 - p.fit_extra:.1f} "
                          f"(with dial_aperture_d ~ dial_d - 0.4) or a larger bore / thread"),
            "tube_to_thread_margin >= 0.1": "params.py (stem_below_dial_seat, UNVERIFIED)",
            "hands_clearance >= hands_clearance_min": "params.py (hands_top_above_dial_face / crystal_engagement)",
        }
        for name, val, lim, note in rows:
            self.add(g, name, val >= lim - 1e-9, val, f">= {lim:.3f}", note, blame=blame.get(name, "params.py"))
        if ring_top is not None and ring_top < p.z_ledge_bottom - PLACEMENT_TOL:
            self.add(g, "short spacer ring: axial float under the ledge (nothing clamps the ring)", None,
                     f"{p.z_ledge_bottom - ring_top:.2f} mm", "", f"ring top {ring_top:.2f} -> ledge underside {p.z_ledge_bottom:.2f}; "
                     f"dial face {p.z_dial_face:.2f} -> ledge {p.z_ledge_bottom - p.z_dial_face:.2f}")
        if lip < RING_LIP_MIN - 1e-9:
            self._whatif_ring()
        # stack-up sanity (informational)
        self.add(g, "stack-up: crystal top / total height", None,
                 f"crystal {p.crystal_proud:+.2f} proud, total {p.total_height:.2f}", "", "")

    def _wp_or_none(self, short: str):
        return self.built[ALIAS.get(short, short)].wp if ALIAS.get(short, short) in self.built else None

    def _whatif_ring(self) -> None:
        """Informational only (never scored): rebuild the spacer ring and the dial with the params.py change the lip
        row recommends (WHATIF_RING, applied through params.get overrides -- params.py is untouched) and show that the
        SPEC 5 ring (full height, real rehaut lip, clamped by the caseback) then exists and clears the as-verified case,
        caseback, movement, tube and crown.  This is the evidence behind the recommendation, nothing more."""
        g = "parametric"
        from parts import dial_blank, spacer_ring
        q = params.get(self.profile, **WHATIF_RING)
        tag = ", ".join(f"{k} {v}" for k, v in WHATIF_RING.items())
        lip = (q.ring_od - q.ring_dial_recess_d) / 2
        saved = list(spacer_ring.WARNINGS)             # the real build's warning stays in the module list
        try:
            ring = spacer_ring.build(q)
            dial = dial_blank.build(q)
            for name, wp in (("ring", ring), ("dial", dial)):
                if wp.solids().size() != 1 or not wp.val().isValid():
                    raise RuntimeError(f"what-if {name} is not one valid solid")
            bb = ring.solids().val().BoundingBox()
            self.add(g, f"what-if {tag}: rehaut lip and ring height (not scored)", None,
                     f"lip {lip:.2f} mm, ring z {bb.zmin:.2f}..{bb.zmax:.2f} (SPEC 5: {q.z_cb_inner:.2f}..{q.z_ledge_bottom:.2f})",
                     "", "params.get overrides only; the full-height SPEC 5 ring builds and reaches the ledge underside")
            pairs = [("case/ring", self._wp_or_none("case"), ring), ("caseback/ring", self._wp_or_none("caseback"), ring),
                     ("ring/movement", ring, self._wp_or_none("movement")), ("ring/tube", ring, self._wp_or_none("tube")),
                     ("ring/crown", ring, self._wp_or_none("crown")), ("ring/dial", ring, dial),
                     ("dial/case", dial, self._wp_or_none("case")), ("dial/movement", dial, self._wp_or_none("movement")),
                     ("dial/hands_envelope", dial, self._wp_or_none("hands_envelope"))]
            vols = [(name, _ivol(a, b)) for name, a, b in pairs if a is not None and b is not None]
            worst = max((v for _, v in vols), default=0.0)
            self.add(g, f"what-if {tag}: full-height ring and {q.dial_d} dial against the as-verified parts (not scored)", None,
                     "; ".join(f"{n} {v:.4f}" for n, v in vols) + " mm^3", "",
                     f"worst {worst:.4f} mm^3 (SPEC 8.3 limit {INTERFERENCE_LIMIT} once params.py is changed); the case body is "
                     f"not rebuilt, its dial aperture does not touch these pairs")
        except Exception as e:  # noqa: BLE001
            self.add(g, f"what-if {tag} (not scored)", None, "error", "", _exc(e))
        finally:
            spacer_ring.WARNINGS[:] = saved

    # ------------------------------------------------------------------ 3. interference
    def run_interference(self) -> None:
        print(f"\n== assembly interference ({self.profile})")
        p = self.p
        g = "interference"
        lim = f"< {INTERFERENCE_LIMIT} mm^3"
        vols: dict[tuple, float] = {}
        for pairs, label in ((REQUIRED_PAIRS, "SPEC 8.3"), (EXTRA_PAIRS, "extra")):
            for a, b in pairs:
                name = f"{a}/{b}" + ("" if label == "SPEC 8.3" else " (extra pair)")
                if ALIAS.get(a, a) not in self.built or ALIAS.get(b, b) not in self.built:
                    self.add(g, name, False, "not built", lim)
                    continue
                try:
                    v = _ivol(self.wp(a), self.wp(b))
                except Exception as e:  # noqa: BLE001
                    self.error(g, name, e)
                    continue
                vols[(a, b)] = v
                note = ""
                blame = f"parts/{ALIAS.get(a, a)}.py or parts/{ALIAS.get(b, b)}.py"
                if (a, b) == ("case", "caseback") and v >= INTERFERENCE_LIMIT and p.thread_model != "helix":
                    note = ("SPEC contradiction for the plain-thread profile: the cosmetic boss (SPEC 4, plain "
                            "cylinder at the MAJOR dia) sits in the cosmetic bore (SPEC 3.6, MINOR dia); the overlap "
                            "is the thread tooth material itself, see the cosmetic-annulus rows below")
                    blame = ("SPEC.md: 8.3 vs 4 / 3.6 -- either 8.3 excludes the cosmetic tooth annulus for thread_model "
                             "'plain', or SPEC 4 draws the cosmetic boss at the MINOR dia")
                if (a, b) == ("case", "tube") and v >= INTERFERENCE_LIMIT and p.tube_hole_d < p.tube_od:
                    note = (f"SPEC contradiction for this profile: the tube hole is printed at {p.tube_hole_d} and "
                            f"reamed to {p.tube_od} (SPEC 1); the as-printed overlap is the ream allowance, see the "
                            "post-ream row below")
                    blame = ("SPEC.md 8.3 / params.resin(): either 8.3 checks the resin case/tube pair after reaming, "
                             f"or the resin tube hole is printed at size (tube_hole_d {p.tube_od})")
                self.add(g, name, v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim, note, blame=blame)

        # -- design-inherent overlaps, measured against their expected values (never replaces the rows above)
        if "case_body" in self.built and "caseback" in self.built and p.thread_model != "helix":
            r_minor = p.cb_thread_minor / 2
            r_major = p.cb_thread_major / 2
            lc = CB_LEAD_CHAMFER
            annulus = math.pi * (r_major ** 2 - r_minor ** 2) * p.cb_thread_len
            chamfer = math.pi * (r_minor * lc ** 2 + lc ** 3 / 3) if r_minor + lc <= r_major else 0.0
            expected = annulus - chamfer
            try:
                inter = self.wp("case").intersect(self.wp("caseback"))
                raw = sum(s.Volume() for s in inter.solids().vals())
                env = _cylinder_z(r_major + 0.3, -0.3, p.cb_thread_len + 0.3)
                inside = _ivol(inter, env) if raw > 0 else 0.0
                outside = raw - inside
                self.add(g, "case/caseback outside the cosmetic thread zone (r <= major/2+0.3, z -0.3..len+0.3)",
                         outside < INTERFERENCE_LIMIT, f"{outside:.4f} mm^3", lim,
                         "any overlap must be the cosmetic boss inside the thread zone")
                dev = abs(raw - expected) / expected if expected else 0.0
                self.add(g, "case/caseback equals the cosmetic tooth annulus pi/4(D^2-D1^2)L minus the lead chamfer",
                         dev < 0.01, f"{raw:.3f} mm^3 vs expected {expected:.3f} ({100 * dev:.2f} %)", "within 1 %",
                         f"annulus {annulus:.3f} - chamfer {chamfer:.3f}; the machinist cuts M{p.cb_thread_major} x "
                         f"{p.cb_thread_pitch} in this material")
            except Exception as e:  # noqa: BLE001
                self.error(g, "case/caseback cosmetic annulus", e)
        if "case_body" in self.built and "tube" in self.built and p.tube_hole_d < p.tube_od:
            ream_allow = math.pi / 4 * (p.tube_od ** 2 - p.tube_hole_d ** 2) * p.tube_engagement
            try:
                tool = _cylinder_x(p.tube_od / 2, 0.0, p.z_stem, p.x_bore_wall - 1.0, p.x_pocket_floor + 1.0)
                reamed = self.wp("case").cut(tool)
                if reamed.solids().size() != 1 or not reamed.val().isValid():
                    raise RuntimeError("reamed case is not one valid solid")
                v = _ivol(reamed, self.wp("tube"))
                self.add(g, f"case (tube hole reamed {p.tube_hole_d} -> {p.tube_od}) / tube", v < INTERFERENCE_LIMIT,
                         f"{v:.4f} mm^3", lim, f"as-printed overlap expected = ream allowance {ream_allow:.4f} mm^3")
            except Exception as e:  # noqa: BLE001
                self.error(g, "case (reamed) / tube", e)
        if "case_body" in self.built and "iring" in self.built:
            od = p.crystal_d + 2 * p.iring_wall
            crush = math.pi / 4 * (od ** 2 - p.crystal_bore_d ** 2) * p.iring_h if od > p.crystal_bore_d else 0.0
            try:
                v = _ivol(self.wp("case"), self.wp("iring"))
                dev = abs(v - crush) / crush if crush else v
                self.add(g, "case/iring equals the designed I-ring crush (uncompressed OD vs bore)", dev < 0.02,
                         f"{v:.4f} mm^3 vs expected {crush:.4f}", "within 2 %",
                         f"I-ring OD {od:.2f} in the {p.crystal_bore_d:.2f} bore: {od - p.crystal_bore_d:.2f} diametral crush")
            except Exception as e:  # noqa: BLE001
                self.error(g, "case/iring", e)
        if "oring" in self.built:
            for other in ("caseback", "case"):
                if ALIAS.get(other, other) in self.built:
                    try:
                        v = _ivol(self.wp(other), self.wp("oring"))
                        self.add(g, f"{other}/oring (uncompressed O-ring, squeeze)", None, f"{v:.4f} mm^3", "",
                                 f"CS {p.oring_cs} in a {p.oring_groove_depth} deep groove: stands "
                                 f"{(p.oring_cs - p.oring_groove_depth) / 2:.3f} proud each side by design")
                    except Exception as e:  # noqa: BLE001
                        self.error(g, f"{other}/oring", e)

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
        if "case_body" not in self.built or "caseback" not in self.built:
            self.add(g, "screw-in test", False, "case or caseback not built", lim)
            return
        P = p.cb_thread_pitch
        case = self.wp("case")
        cb = self.wp("caseback")
        try:
            boss = threads.boss(p.cb_thread_major, p.cb_thread_pitch, p.cb_thread_len,
                                clearance=p.cb_thread_clearance, z0=0.0)
            zone = case.intersect(cq.Workplane("XY").box(p.W + 2, p.L + 2, p.cb_thread_len, centered=(True, True, False)))
            if zone.solids().size() != 1 or not zone.val().isValid():
                raise RuntimeError("threaded zone slice is not one valid solid")
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
                         "seated" if theta == 0 else f"{theta / 360:.2f} turn before seating")
            except Exception as e:  # noqa: BLE001
                self.error(g, f"screw-in {theta:.0f} deg", e)
        # the same alignment on the advancing side, judged against the threaded zone only (the seat excluded)
        for theta in (120.0, 240.0):
            dz = theta / 360.0 * P
            try:
                moved = boss.rotate((0, 0, 0), (0, 0, 1), theta).translate((0, 0, dz))
                v = _ivol(moved, zone)
                self.add(g, f"bare boss rotated +{theta:.0f} deg, advanced +{dz:.3f} mm / threaded zone z 0..{p.cb_thread_len}",
                         v < INTERFERENCE_LIMIT, f"{v:.4f} mm^3", lim, "thread flanks only, seat excluded")
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
                     "> 1 mm^3", "proves the intersection test can see a clash")
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
            solid.append((f"wall between the thread zone and the channel {tag} o'clock", (0.0, sy * 15.5, 0.75)))
        empty.append(("crown pocket, centre", ((p.x_pocket_floor + p.x_flank) / 2, 0.0, p.H / 2)))
        for sy in (1, -1):
            empty.append((f"crown pocket, y = {sy * (p.crown_pocket_w / 2 - 0.8):+.1f}",
                          (p.x_pocket_floor + 0.3, sy * (p.crown_pocket_w / 2 - 0.8), p.H / 2)))
        x_tube = (r_mvt + p.x_pocket_floor) / 2
        empty.append(("tube hole, on the axis", (x_tube, 0.0, p.z_stem)))
        empty.append(("tube hole, 0.1 inside the wall", (x_tube, 0.0, p.z_stem + p.tube_hole_d / 2 - 0.1)))
        empty.append(("ring keyway at 9 o'clock", (-(r_mvt + (p.ring_key_h + 0.1) / 2), 0.0, p.z_cb_inner + p.ring_key_len / 2)))
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
        solid.append(("wall beside the ring keyway", (-(r_mvt + p.ring_key_h + 0.1 + 0.4), 0.0, p.z_cb_inner + p.ring_key_len / 2)))
        solid.append(("crystal bore wall below the lead chamfer", (r_crystal + 0.08, 0.0, p.H - 0.6)))
        solid.append(("front face at the flank", (p.x_flank - 1.0, -8.0, p.H - 0.2)))
        return empty, solid

    def run_probes(self) -> None:
        print(f"\n== probe points ({self.profile})")
        p = self.p
        g = "probes"
        if "case_body" not in self.built:
            self.add(g, "case body probes", False, "case not built")
        else:
            s = self.built["case_body"].solid
            empty, solid = self._case_probes()
            for name, pt in empty:
                ins = s.isInside(cq.Vector(*pt))
                self.add(g, f"case: {name} is void", not ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "void")
            for name, pt in solid:
                ins = s.isInside(cq.Vector(*pt))
                self.add(g, f"case: {name} is solid", ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "solid")
            # thread lead chamfer: a ring of points just outside the minor radius at the mouth must all be void
            r = p.cb_thread_minor / 2 + 0.08
            hits = sum(s.isInside(cq.Vector(r * math.cos(t), r * math.sin(t), 0.05)) for t in [i * math.pi / 18 for i in range(36)])
            self.add(g, "case: thread lead chamfer (36 points at r_minor + 0.08, z = 0.05) all void", hits == 0,
                     f"{hits} solid hits / 36", "0", "SPEC 3.6 lead chamfer 0.2")
        if "spacer_ring" in self.built:
            try:
                from parts import spacer_ring
                empty, solid = spacer_ring.probe_points(p)
                s = self.built["spacer_ring"].solid
                for name, pt in empty:
                    ins = s.isInside(cq.Vector(*pt))
                    self.add(g, f"ring: {name} is void", not ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "void")
                for name, pt in solid:
                    ins = s.isInside(cq.Vector(*pt))
                    self.add(g, f"ring: {name} is solid", ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "solid")
            except Exception as e:  # noqa: BLE001
                self.error(g, "ring probes", e)
        if "caseback" in self.built:
            s = self.built["caseback"].solid
            z_out = -p.cb_flange_t
            rr = p.cb_tool_pcd / 2
            pts_empty = [("tool hole at 12 o'clock", (0.0, rr, z_out + p.cb_tool_hole_depth - 0.05)),
                         ("O-ring groove", (p.oring_groove_mean_d / 2, 0.0, -p.oring_groove_depth / 2)),
                         ("above the boss", (0.0, 0.0, p.cb_thread_len + 0.1))]
            pts_solid = [("boss, just under the inner face", (0.0, 5.0, p.cb_thread_len - 0.1)),
                         ("flange between groove and edge", ((p.oring_groove_mean_d + p.oring_groove_w) / 2 + 0.2, 0.0, -p.cb_flange_t / 2)),
                         ("floor under the tool hole", (0.0, rr, z_out + p.cb_tool_hole_depth + 0.1))]
            if p.cb_center_hole:
                pts_empty.append(("centre dimple", (0.0, 0.0, z_out + p.cb_center_hole_depth / 2)))
            for name, pt in pts_empty:
                ins = s.isInside(cq.Vector(*pt))
                self.add(g, f"caseback: {name} is void", not ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "void")
            for name, pt in pts_solid:
                ins = s.isInside(cq.Vector(*pt))
                self.add(g, f"caseback: {name} is solid", ins, f"({pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}) {'solid' if ins else 'void'}", "solid")
        if "dial_blank" in self.built:
            s = self.built["dial_blank"].solid
            zc = (p.z_dial_seat + p.z_dial_face) / 2
            ins = s.isInside(cq.Vector(0, 0, zc))
            self.add(g, "dial: centre hole is void", not ins, f"(0, 0, {zc:.2f}) {'solid' if ins else 'void'}", "void")
            ins = s.isInside(cq.Vector(p.dial_d / 4, 0, zc))
            self.add(g, "dial: disc is solid", ins, f"({p.dial_d / 4:.2f}, 0, {zc:.2f}) {'solid' if ins else 'void'}", "solid")

    # ------------------------------------------------------------------ 6. STL
    def run_stl(self) -> None:
        print(f"\n== STL export and mesh checks ({self.profile}) -> {self.scratch}")
        g = "stl"
        os.makedirs(self.scratch, exist_ok=True)
        for name, b in self.built.items():
            path = os.path.join(self.scratch, f"{name}.stl")
            try:
                cq.exporters.export(b.wp, path, exportType="STL", tolerance=STL_TOLERANCE,
                                    angularTolerance=STL_ANGULAR_TOLERANCE)
                m = trimesh.load(path, force="mesh")
                wt = bool(m.is_watertight)
                mv = float(m.volume) if wt else float("nan")
                dev = abs(mv - b.volume) / b.volume if wt else float("nan")
                self.add(g, f"{name}: STL watertight", wt, f"{len(m.faces)} faces, watertight={wt}", "watertight")
                self.add(g, f"{name}: mesh volume within 1 % of the B-rep volume", wt and dev < STL_VOLUME_TOL,
                         f"mesh {mv:.2f} vs B-rep {b.volume:.2f} mm^3 ({100 * dev:.3f} %)" if wt else "n/a", "< 1 %")
            except Exception as e:  # noqa: BLE001
                self.error(g, f"{name}: STL export / mesh check", e)

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
                note = f"{attr}" + (" (no brass density in params.py; build.py uses the same)" if name == "dial_blank" else "")
                self.add("mass", f"{name} mass", None, f"{mass:.2f} g ({b.volume:.1f} mm^3 x {dens:.2f} g/cm^3)", "", note)
        self.add("mass", "made parts total", None, f"{total:.2f} g", "", "case body + caseback + spacer ring + dial blank")
        self.total_mass = total
        if "dial_blank" in self.built and self.profile == "titanium":
            vb = self.built["dial_blank"].volume
            self.add("mass", "dial_blank mass at brass density (informational, not in the total)", None,
                     f"{vb / 1000.0 * BRASS_DENSITY:.2f} g ({vb:.1f} mm^3 x {BRASS_DENSITY:.2f} g/cm^3)", "",
                     "the scored table follows SPEC 8.6 (density_*); params.py has no brass density")

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
                     f"Run {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')} in {elapsed:.0f} s. SPEC.md section 8.")
        lines.append("")
        lines.append(f"Profile: thread_model `{p.thread_model}`, M{p.cb_thread_major} x {p.cb_thread_pitch} "
                     f"(minor {p.cb_thread_minor:.3f}), tube hole {p.tube_hole_d}, hole_comp {p.hole_comp}, fit_extra {p.fit_extra}; "
                     f"densities case {p.density_case} / caseback {p.density_caseback} / ring {p.density_ring} g/cm^3.")
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
        lines.append("## Module warnings (skipped edge breaks, fallbacks)")
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
        print(f"\n===== profile {self.profile} (thread_model {self.p.thread_model}), report -> {self.out_dir}")
        for step in (self.run_build, self.run_placement, self.run_parametric, self.run_interference, self.run_thread,
                     self.run_probes, self.run_stl, self.run_masses):
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
