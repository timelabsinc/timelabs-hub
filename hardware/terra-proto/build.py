#!/usr/bin/env python3
"""
Export every part of the Terra-style GL32 case prototype (SPEC.md section 10, v0.2).

    python3 build.py --profile resin|titanium|all [--out out] [--parts case_body caseback ...]

For each requested profile (p = params.get(profile)) this script

  * imports parts.case_body, parts.caseback, parts.spacer_ring, parts.dial_blank
    (contract: PART, MATERIAL, build(p), bom(p)) and parts.purchased (PARTS dict + bom(p),
    plus MATERIAL / COLORS per placeholder and the seal helpers described below),
  * builds every part, checks it is exactly ONE valid solid,
  * writes out/<profile>/<PART>.stl (binary, tol 0.01 / 0.1 rad) and out/<profile>/<PART>.step,
    and the same for every purchased placeholder (crystal, iring, tube, crown, movement, ...),
  * exports ONE caseback seal placeholder, the one p.cb_seal selects (SPEC 4 / 6): "gasket" for
    cb_seal "flat_gasket" (the default), "oring" for cb_seal "oring". The choice comes from
    parts.purchased.seal_part_name(p) when the module has it and from the same mapping here
    otherwise. The other seal is not built, not exported and not in the assembly; a stale
    <seal>.stl / .step of it left in out/<profile>/ by an earlier run is removed. Naming the
    unselected seal in --parts builds and exports it anyway (you asked for it), but it still
    stays out of assembly.step. parts.purchased.seal_fit_problems(p), when present, is printed
    as warnings: the placeholder is built exactly as params.py says, never corrected.
  * writes out/<profile>/assembly.step from a colour-coded cq.Assembly 'terra_proto_<profile>'
    (made parts + every placeholder + the selected seal),
  * writes out/<profile>/params.json (Params.as_dict()) and out/<profile>/bom.csv, the bom() rows
    of every module merged into one table (columns part, item, spec, qty, source),
  * prints a table of volumes and mass estimates (density_case for the case body,
    density_caseback for the caseback, density_ring for the spacer ring, density_dial for the
    dial blank; purchased placeholders get no mass).

A missing or broken part module is reported and skipped; the remaining parts are still exported
and the assembly is written with whatever was built. The exit code is 1 if anything failed.

Relative --out paths are resolved against this file's directory, so the outputs always land in
hardware/terra-proto/out/<profile>/ no matter where the script is launched from.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:                       # lets 'import params' / 'import parts.x' work from any cwd
    sys.path.insert(0, HERE)

import cadquery as cq  # noqa: E402

import params  # noqa: E402

# --------------------------------------------------------------------------- configuration
PROFILES = ("titanium", "resin")

PART_MODULES = ("parts.case_body", "parts.caseback", "parts.spacer_ring", "parts.dial_blank")
PURCHASED_MODULE = "parts.purchased"
PURCHASED_NAMES = ("crystal", "iring", "gasket", "oring", "tube", "crown", "movement", "hands_envelope", "battery")

# The two caseback seal placeholders; exactly one of them (per params.cb_seal) goes into the assembly.
# parts.purchased.SEAL_NAMES / SEAL_BY_CB_SEAL / seal_part_name(p) override these when the module has them.
SEAL_NAMES = ("gasket", "oring")
SEAL_BY_CB_SEAL = {"flat_gasket": "gasket", "oring": "oring"}

# Which Params density applies to each made part (g/cm3). Purchased placeholders get no mass estimate.
DENSITY_ATTR = {
    "case_body": "density_case",
    "caseback": "density_caseback",
    "spacer_ring": "density_ring",
    "dial_blank": "density_dial",
}

# Assembly colours (r, g, b, alpha) for the made parts; purchased placeholders take parts.purchased.COLORS
# when the module has it, else COLOUR_OTHER.
COLOURS = {
    "case_body": (0.55, 0.56, 0.58, 1.0),      # grey (titanium)
    "caseback": (0.78, 0.79, 0.81, 1.0),       # steel
    "spacer_ring": (0.08, 0.08, 0.08, 1.0),    # black (POM)
    "dial_blank": (0.96, 0.96, 0.94, 1.0),     # white
    "crystal": (0.55, 0.75, 1.00, 0.35),       # translucent blue
}
COLOUR_OTHER = (1.0, 0.55, 0.10, 1.0)          # orange: every other purchased placeholder

STL_TOLERANCE = 0.01
STL_ANGULAR_TOLERANCE = 0.1


# --------------------------------------------------------------------------- reporting helpers
class Report:
    """Collects failures / warnings across parts and profiles; decides the exit code."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, where: str, msg: str, exc: BaseException | None = None) -> None:
        line = f"{where}: {msg}"
        if exc is not None:
            line += f" ({type(exc).__name__}: {exc})"
        self.failures.append(line)
        print(f"ERROR   {line}", file=sys.stderr)
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=-4))
            print("".join("        " + ln + "\n" for ln in tb.rstrip().splitlines()), end="", file=sys.stderr)

    def warn(self, where: str, msg: str) -> None:
        line = f"{where}: {msg}"
        self.warnings.append(line)
        print(f"WARNING {line}", file=sys.stderr)

    @property
    def ok(self) -> bool:
        return not self.failures


def _import(name: str, report: Report, where: str):
    """Import a part module; returns None (and records a failure) if it is missing or broken."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name == name:
            report.fail(where, f"module {name} not found (not written yet?)")
        else:
            report.fail(where, f"import of {name} failed", e)
    except Exception as e:  # noqa: BLE001 - a broken module must not kill the build
        report.fail(where, f"import of {name} raised", e)
    return None


def _as_workplane(obj) -> cq.Workplane:
    """Accept a Workplane or a bare Shape from build(); anything else is an error."""
    if isinstance(obj, cq.Workplane):
        return obj
    if isinstance(obj, cq.Shape):
        return cq.Workplane("XY").newObject([obj])
    raise TypeError(f"build() returned {type(obj).__name__}, expected cq.Workplane")


def _one_valid_solid(wp: cq.Workplane) -> cq.Solid:
    n = wp.solids().size()
    if n != 1:
        raise ValueError(f"expected exactly 1 solid, got {n}")
    solid = wp.solids().val()
    if not solid.isValid():
        raise ValueError("solid is not valid (BRepCheck failed)")
    return solid


def _to_jsonable(v):
    if isinstance(v, (tuple, list)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _stl_watertight(path: str) -> str:
    """'yes' / 'no' / '?' -- informational only; verify.py owns the hard check."""
    try:
        import trimesh  # local import: optional for a plain export run
        m = trimesh.load(path, force="mesh")
        return "yes" if m.is_watertight else "no"
    except Exception:  # noqa: BLE001
        return "?"


# --------------------------------------------------------------------------- seal selection (SPEC 4 / 6)
def _seal_names(mod) -> tuple[str, ...]:
    names = getattr(mod, "SEAL_NAMES", None)
    if isinstance(names, (tuple, list)) and names:
        return tuple(str(n) for n in names)
    return SEAL_NAMES


def _selected_seal(mod, p: params.Params, report: Report, where: str) -> str | None:
    """The PARTS name of the seal that belongs in the assembly: parts.purchased.seal_part_name(p) when the
    module has it, else the same cb_seal mapping here ("flat_gasket" -> "gasket", anything else -> "oring",
    with a warning when cb_seal is not one of the two documented values). None if the module's helper raised."""
    fn = getattr(mod, "seal_part_name", None)
    if callable(fn):
        try:
            return str(fn(p))
        except Exception as e:  # noqa: BLE001
            report.fail(where, f"seal_part_name(p) raised for cb_seal = {p.cb_seal!r}", e)
            return None
    if p.cb_seal not in SEAL_BY_CB_SEAL:
        report.warn(where, f"params.cb_seal = {p.cb_seal!r} is not one of {sorted(SEAL_BY_CB_SEAL)}; "
                           f"taking the 'oring' placeholder")
    return "gasket" if p.cb_seal == "flat_gasket" else "oring"


def _seal_fit_warnings(mod, p: params.Params, seal: str, report: Report, where: str) -> None:
    """parts.purchased.seal_fit_problems(p): what params.py makes impossible for the selected seal."""
    fn = getattr(mod, "seal_fit_problems", None)
    if not callable(fn):
        return
    try:
        problems = fn(p)
    except Exception as e:  # noqa: BLE001
        report.warn(where, f"seal_fit_problems(p) raised ({type(e).__name__}: {e})")
        return
    for msg in problems or ():
        report.warn(where, f"{seal} does not fit its seat as params.py describes it: {msg} "
                           f"(placeholder built as specified, fix params.py)")


def _remove_stale(out_dir: str, stem: str, report: Report, where: str) -> None:
    """Delete <stem>.stl / .step left behind by an earlier run with the other cb_seal."""
    for ext in (".stl", ".step"):
        path = os.path.join(out_dir, stem + ext)
        if os.path.isfile(path):
            try:
                os.remove(path)
                print(f"    removed stale {os.path.basename(path)}")
            except OSError as e:
                report.warn(where, f"could not remove stale {path} ({e})")


# --------------------------------------------------------------------------- building
class Built:
    """One exported solid and its bookkeeping row."""

    def __init__(self, name: str, material: str, solid: cq.Solid, kind: str) -> None:
        self.name = name
        self.material = material
        self.solid = solid
        self.kind = kind                      # "part" | "purchased"
        self.volume = solid.Volume()          # mm3
        self.density: float | None = None     # g/cm3
        self.mass: float | None = None        # g
        self.watertight = "?"
        self.seconds = 0.0
        self.in_assembly = True               # False only for a seal exported by name but not selected by cb_seal
        self.colour: tuple = COLOURS.get(name, COLOUR_OTHER)


def _export_solid(solid: cq.Solid, out_dir: str, stem: str, report: Report, where: str) -> tuple[bool, bool]:
    stl_ok = step_ok = False
    stl_path = os.path.join(out_dir, f"{stem}.stl")
    step_path = os.path.join(out_dir, f"{stem}.step")
    try:
        cq.exporters.export(solid, stl_path, exportType="STL",
                            tolerance=STL_TOLERANCE, angularTolerance=STL_ANGULAR_TOLERANCE)
        stl_ok = True
    except Exception as e:  # noqa: BLE001
        report.fail(where, f"STL export failed ({stl_path})", e)
    try:
        cq.exporters.export(solid, step_path, exportType="STEP")
        step_ok = True
    except Exception as e:  # noqa: BLE001
        report.fail(where, f"STEP export failed ({step_path})", e)
    return stl_ok, step_ok


def _build_one(fn, p: params.Params, name: str, material: str, kind: str,
               out_dir: str, report: Report, where: str) -> Built | None:
    t0 = time.perf_counter()
    try:
        wp = _as_workplane(fn(p))
        solid = _one_valid_solid(wp)
    except Exception as e:  # noqa: BLE001 - includes OCC Standard_Failure
        report.fail(where, "build failed", e)
        return None
    b = Built(name, material, solid, kind)
    b.seconds = time.perf_counter() - t0
    stl_ok, _ = _export_solid(solid, out_dir, name, report, where)
    if stl_ok:
        b.watertight = _stl_watertight(os.path.join(out_dir, f"{name}.stl"))
        if b.watertight == "no":
            report.warn(where, "exported STL is not watertight")
    return b


def _bom_rows(mod, p: params.Params, part_label: str, report: Report, where: str) -> list[dict]:
    """Normalise one module's bom() output into rows with fixed columns."""
    rows: list[dict] = []
    try:
        raw = mod.bom(p)
    except Exception as e:  # noqa: BLE001
        report.fail(where, "bom() raised", e)
        return rows
    if raw is None:
        return rows
    if not isinstance(raw, (list, tuple)):
        report.fail(where, f"bom() returned {type(raw).__name__}, expected a list of dicts")
        return rows
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            report.fail(where, f"bom() row {i} is {type(r).__name__}, expected dict")
            continue
        rows.append({
            "part": part_label,
            "item": str(r.get("item", "")),
            "spec": str(r.get("spec", "")),
            "qty": r.get("qty", ""),
            "source": str(r.get("source", "")),
        })
    return rows


def _build_made_parts(p: params.Params, profile: str, out_dir: str, wanted: set[str] | None,
                      report: Report, built: list[Built], bom_rows: list[dict]) -> None:
    where0 = f"[{profile}]"
    for modname in PART_MODULES:
        stem = modname.rsplit(".", 1)[-1]
        where = f"{where0} {stem}"
        if wanted is not None and stem not in wanted:
            continue
        mod = _import(modname, report, where)
        if mod is None:
            continue
        missing = [a for a in ("PART", "MATERIAL", "build", "bom") if not hasattr(mod, a)]
        if missing:
            report.fail(where, f"module does not follow the contract, missing {missing}")
            continue
        part = str(mod.PART)
        if part != stem:
            report.warn(where, f"PART = {part!r} differs from the module name; files use {part!r}")
        material = mod.MATERIAL.get(profile, "?") if isinstance(mod.MATERIAL, dict) else str(mod.MATERIAL)
        print(f"  building {part} ({material})", flush=True)
        b = _build_one(mod.build, p, part, material, "part", out_dir, report, where)
        if b is None:
            print(f"    {part}: FAILED")
        else:
            dens_attr = DENSITY_ATTR.get(part)
            if dens_attr is not None:
                b.density = float(getattr(p, dens_attr))
                b.mass = b.volume / 1000.0 * b.density
            built.append(b)
            print(f"    {part}: ok  {b.volume:.1f} mm3  {b.seconds:.1f} s")
            skipped = getattr(mod, "SKIPPED", None)
            if isinstance(skipped, list):
                mine = [s for s in skipped if s.startswith(profile + ":")]
                if mine:
                    report.warn(where, f"{len(mine)} edge break(s) skipped (see the module's warnings)")
        bom_rows += _bom_rows(mod, p, part, report, where)


def _build_purchased(p: params.Params, profile: str, out_dir: str, wanted: set[str] | None,
                     report: Report, built: list[Built], bom_rows: list[dict]) -> None:
    where = f"[{profile}] purchased"
    mod = _import(PURCHASED_MODULE, report, where)
    if mod is None:
        return
    parts_dict = getattr(mod, "PARTS", None)
    if not isinstance(parts_dict, dict) or not hasattr(mod, "bom"):
        report.fail(where, "module does not follow the contract (needs PARTS dict and bom(p))")
        return
    materials = getattr(mod, "MATERIAL", None)
    if not isinstance(materials, dict):
        materials = {}
    colours = getattr(mod, "COLORS", None)
    if not isinstance(colours, dict):
        colours = {}

    seal_names = _seal_names(mod)
    seal = _selected_seal(mod, p, report, where)
    if seal is not None:
        print(f"  seal: cb_seal = {p.cb_seal!r} -> {seal!r} placeholder in the assembly"
              + "".join(f", {n!r} left out" for n in seal_names if n != seal))
        if seal not in parts_dict:
            report.fail(where, f"PARTS has no entry for the selected seal {seal!r}")
        else:
            _seal_fit_warnings(mod, p, seal, report, where)

    for name, fn in parts_dict.items():
        name = str(name)
        explicit = wanted is not None and name in wanted
        if wanted is not None and "purchased" not in wanted and not explicit:
            continue
        w = f"{where}/{name}"
        in_assembly = True
        if name in seal_names and name != seal:
            if not explicit:
                print(f"  skipping {name} (not the seal cb_seal = {p.cb_seal!r} selects)")
                _remove_stale(out_dir, name, report, w)
                continue
            report.warn(w, f"not the seal cb_seal = {p.cb_seal!r} selects; built and exported because it was "
                           f"named in --parts, but kept out of assembly.step")
            in_assembly = False
        if not callable(fn):
            report.fail(w, "PARTS entry is not callable")
            continue
        material = str(materials.get(name, "purchased"))
        print(f"  building {name} (purchased placeholder: {material})", flush=True)
        b = _build_one(fn, p, name, material, "purchased", out_dir, report, w)
        if b is None:
            print(f"    {name}: FAILED")
            continue
        b.in_assembly = in_assembly
        col = colours.get(name)
        if isinstance(col, (tuple, list)) and len(col) in (3, 4):
            b.colour = tuple(float(c) for c in col)
        built.append(b)
        print(f"    {name}: ok  {b.volume:.1f} mm3  {b.seconds:.1f} s")

    unknown = [n for n in PURCHASED_NAMES if n not in parts_dict]
    if unknown:
        report.warn(where, f"PARTS has no entry for {unknown}")
    bom_rows += _bom_rows(mod, p, "purchased", report, where)


def build_profile(profile: str, out_root: str, wanted: set[str] | None, report: Report) -> list[Built]:
    """Build, export and document one profile. Returns the list of built solids."""
    where0 = f"[{profile}]"
    p = params.get(profile)
    out_dir = os.path.join(out_root, profile)
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n=== profile {profile}  ->  {out_dir}")

    built: list[Built] = []
    bom_rows: list[dict] = []

    # -- made parts
    _build_made_parts(p, profile, out_dir, wanted, report, built, bom_rows)

    # -- purchased placeholders (every one plus the seal cb_seal selects)
    want_purchased = wanted is None or "purchased" in wanted or any(n in wanted for n in PURCHASED_NAMES)
    if want_purchased:
        _build_purchased(p, profile, out_dir, wanted, report, built, bom_rows)

    # -- assembly
    in_assy = [b for b in built if b.in_assembly]
    if in_assy:
        where = f"{where0} assembly"
        try:
            assy = cq.Assembly(name=f"terra_proto_{profile}")
            for b in in_assy:
                assy.add(b.solid, name=b.name, color=cq.Color(*b.colour))
            assy_path = os.path.join(out_dir, "assembly.step")
            assy.export(assy_path, exportType="STEP")
            print(f"  assembly.step written with {len(in_assy)} solid(s): " + ", ".join(b.name for b in in_assy))
        except Exception as e:  # noqa: BLE001
            report.fail(where, "assembly export failed", e)
    elif built:
        report.warn(f"{where0} assembly", "assembly.step not written: nothing built belongs in the assembly "
                                          "(only the unselected seal was asked for)")
    else:
        report.fail(f"{where0} assembly", "nothing was built, assembly.step not written")

    # -- params.json
    try:
        with open(os.path.join(out_dir, "params.json"), "w", encoding="utf-8") as f:
            json.dump(_to_jsonable(p.as_dict()), f, indent=2)
            f.write("\n")
    except Exception as e:  # noqa: BLE001
        report.fail(f"{where0} params.json", "could not write", e)

    # -- bom.csv: every module's bom() rows in one table
    try:
        with open(os.path.join(out_dir, "bom.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["part", "item", "spec", "qty", "source"])
            w.writeheader()
            w.writerows(bom_rows)
        print(f"  bom.csv written with {len(bom_rows)} row(s) from "
              f"{len({r['part'] for r in bom_rows})} module(s)")
    except Exception as e:  # noqa: BLE001
        report.fail(f"{where0} bom.csv", "could not write", e)

    _print_table(profile, built)
    return built


def _print_table(profile: str, built: list[Built]) -> None:
    print(f"\n  volumes and mass estimates ({profile}):")
    mw = max(18, min(40, max((len(b.material) for b in built), default=18)))
    hdr = f"  {'part':<16s} {'material':<{mw}s} {'volume mm3':>11s} {'density':>8s} {'mass g':>8s}  {'watertight':<10s}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    total = 0.0
    for b in built:
        dens = f"{b.density:8.2f}" if b.density is not None else f"{'-':>8s}"
        mass = f"{b.mass:8.2f}" if b.mass is not None else f"{'-':>8s}"
        if b.mass is not None:
            total += b.mass
        note = "" if b.in_assembly else "  (not in assembly)"
        print(f"  {b.name:<16s} {b.material[:mw]:<{mw}s} {b.volume:11.1f} {dens} {mass}  {b.watertight:<10s}{note}")
    print("  " + "-" * (len(hdr) - 2))
    print(f"  {'made parts total':<16s} {'':<{mw}s} {'':>11s} {'':>8s} {total:8.2f}   (density_* from params.py)")


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=(*PROFILES, "all"), default="all",
                    help="build profile: titanium (machined, cosmetic threads), resin (helix threads, "
                         "opened fits) or all (default)")
    ap.add_argument("--out", default="out",
                    help="output root; out/<profile>/... is written below it. A relative path is taken "
                         "relative to this script's directory (default: out)")
    ap.add_argument("--parts", nargs="+", metavar="NAME",
                    help="only build this subset: case_body caseback spacer_ring dial_blank purchased, "
                         "or single purchased placeholders (" + " ".join(PURCHASED_NAMES) + "); "
                         "'purchased' means every placeholder plus the seal params.cb_seal selects")
    args = ap.parse_args(argv)

    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    out_root = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)

    wanted: set[str] | None = None
    if args.parts:
        known = {m.rsplit(".", 1)[-1] for m in PART_MODULES} | {"purchased"} | set(PURCHASED_NAMES)
        bad = [n for n in args.parts if n not in known]
        if bad:
            ap.error(f"unknown part name(s) {bad}; choose from {sorted(known)}")
        wanted = set(args.parts)

    report = Report()
    t0 = time.perf_counter()
    print(f"terra-proto build: profiles={profiles} out={out_root}"
          + (f" parts={sorted(wanted)}" if wanted else ""))
    for profile in profiles:
        try:
            build_profile(profile, out_root, wanted, report)
        except Exception as e:  # noqa: BLE001 - never let one profile abort the other
            report.fail(f"[{profile}]", "profile build aborted", e)

    print(f"\nfinished in {time.perf_counter() - t0:.1f} s")
    if report.warnings:
        print(f"{len(report.warnings)} warning(s)")
    if report.failures:
        print(f"{len(report.failures)} FAILURE(S):", file=sys.stderr)
        for f in report.failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("all parts built and exported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
