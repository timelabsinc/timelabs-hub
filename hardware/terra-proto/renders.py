#!/usr/bin/env python3
"""
Shaded renders and section images of the exported STL meshes.

    python3 renders.py [--profile titanium|resin|all] [--out out] [--dpi 150]

Reads out/<profile>/<part>.stl (runs `build.py --profile all` first when they are missing) and writes

    out/renders/<profile>_<part>_iso.png       one shaded isometric view per part (from the front / +Z side)
    out/renders/<profile>_<part>_iso_back.png  the same part seen from the back (-Z side) for the parts whose
                                               v0.2 features live there: case body (strap openings, back
                                               grooves, chamfer notches, thread bore, keyway open to the back
                                               face), caseback (spanner holes, dimple, outer chamfer) and
                                               spacer ring (GL32 movement pocket, stem slot, key)
    out/renders/<profile>_exploded_iso.png     the assembly exploded along Z (tube and crown along X)
    out/renders/<profile>_section_yz.png       filled cross-section of the assembly at x = 0 (YZ plane)
    out/renders/<profile>_section_xz.png       filled cross-section of the assembly at y = 0 (XZ plane):
                                               keyway at 9, stem slot / tube / crown at 3

The caseback seal is whichever one params.cb_seal selects (parts.purchased.seal_part_name: the flat
gasket by default, the O-ring option otherwise); build.py exports only that one, so only that one is
rendered (SPEC 4 / 6).  Nothing here is drawn from params.py directly: every picture is the exported v0.2
STL, so the renders show exactly what build.py wrote (flat-gasket recess, open keyway, 4.0 strap bar,
0.3 front chamfer round the crown pocket, full-height ring with its rehaut lip).

Rendering is plain matplotlib: every mesh goes into a Poly3DCollection with per-face Lambert shading
(ambient + diffuse from a fixed light), equal box aspect and no axes.  The sections cut every part mesh with
trimesh.intersections.slice_mesh_plane, collect the boundary edges that lie in the cutting plane, chain them into
closed loops and fill them (even-odd nesting, so bores and grooves stay open) in the part's colour.  No shapely
is needed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import numpy as np
import trimesh

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch, Patch  # noqa: E402
from matplotlib.path import Path  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import params  # noqa: E402
from parts import purchased  # noqa: E402

PROFILES = ("titanium", "resin")
PARTS_BASE = ("case_body", "caseback", "spacer_ring", "dial_blank",
              "crystal", "iring", "tube", "crown", "movement", "hands_envelope", "battery")


def parts_for(profile: str) -> tuple[str, ...]:
    """Every part plus the ONE caseback seal params.cb_seal selects for this profile (SPEC 4 / 6)."""
    return PARTS_BASE + (purchased.seal_part_name(params.get(profile)),)


# The POM ring and the rubber seals are black parts, but near-black Lambert shading hides their features
# (lip, key, pocket step), so they are drawn as dark greys that still read as "the black parts".
COLOURS = {
    "case_body": (0.56, 0.57, 0.60), "caseback": (0.76, 0.77, 0.80), "spacer_ring": (0.34, 0.34, 0.37),
    "dial_blank": (0.92, 0.90, 0.80), "crystal": (0.55, 0.75, 1.00), "iring": (0.42, 0.42, 0.45),
    "gasket": (0.24, 0.24, 0.26), "oring": (0.22, 0.22, 0.24), "tube": (0.70, 0.70, 0.74), "crown": (0.50, 0.52, 0.58),
    "movement": (0.85, 0.72, 0.30), "hands_envelope": (1.00, 0.55, 0.10), "battery": (0.80, 0.80, 0.82),
}
ALPHA = {"crystal": 0.45, "hands_envelope": 0.35}
LABEL = {
    "case_body": "case body", "caseback": "caseback", "spacer_ring": "spacer ring", "dial_blank": "dial blank",
    "crystal": "crystal", "iring": "I-ring", "gasket": "flat gasket", "oring": "O-ring", "tube": "tube", "crown": "crown",
    "movement": "movement (GL32 envelope)", "hands_envelope": "hands envelope", "battery": "battery",
}
# parts that get a second view from the back (-Z side, negative elevation) and what that view is for
BACK_VIEWS = {
    "case_body": "back face: strap openings and 4.0 bars, back grooves, chamfer notches, thread bore, keyway open at 9",
    "caseback": "outer face: spanner holes on the PCD, centre dimple, outer edge chamfer",
    "spacer_ring": "bottom: GL32 movement pocket (circle cut by two flats), stem slot at 3, key at 9",
}
# exploded-view offsets: (dx, dy, dz) in mm
EXPLODE = {
    "caseback": (0, 0, -20), "gasket": (0, 0, -14), "oring": (0, 0, -14), "case_body": (0, 0, 0), "spacer_ring": (0, 0, 16),
    "movement": (0, 0, 26), "battery": (0, 0, 26), "dial_blank": (0, 0, 35), "hands_envelope": (0, 0, 42),
    "iring": (0, 0, 50), "crystal": (0, 0, 58), "tube": (8, 0, 0), "crown": (16, 0, 0),
}
MAX_EDGE = 1.0   # mm: long sliver triangles are subdivided so that per-face depth sorting works
LIGHT_ELEV_OFFSET, LIGHT_AZIM_OFFSET = 25.0, 30.0   # the key light sits above-right of the camera (below-right for back views)
AMBIENT, DIFFUSE = 0.32, 0.68


# --------------------------------------------------------------------------- loading
def load_meshes(out_root: str, profile: str) -> dict[str, trimesh.Trimesh]:
    d = os.path.join(out_root, profile)
    parts = parts_for(profile)
    missing = [n for n in parts if not os.path.exists(os.path.join(d, n + ".stl"))]
    if missing:
        print(f"  {profile}: {len(missing)} STL(s) missing ({', '.join(missing[:3])}...), running build.py --profile all")
        subprocess.run([sys.executable, os.path.join(HERE, "build.py"), "--profile", "all", "--out", out_root], check=True)
    meshes = {}
    for n in parts:
        path = os.path.join(d, n + ".stl")
        if os.path.exists(path):
            m = trimesh.load(path, force="mesh")
            m.merge_vertices()
            meshes[n] = m
        else:
            print(f"  WARNING {profile}: {n}.stl still missing, skipped")
    return meshes


# --------------------------------------------------------------------------- shaded views
def refine(mesh: trimesh.Trimesh, max_edge: float = MAX_EDGE) -> trimesh.Trimesh:
    """Subdivide edges longer than max_edge (STL flat faces are fans of long slivers, which the painter's
    depth sort of Poly3DCollection cannot order correctly)."""
    v, f = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, max_edge=max_edge, max_iter=8)
    return trimesh.Trimesh(v, f, process=False)


def view_vector(elev: float, azim: float) -> np.ndarray:
    e, a = np.radians(elev), np.radians(azim)
    return np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])


def cull_backfaces(mesh: trimesh.Trimesh, view: np.ndarray) -> trimesh.Trimesh:
    keep = mesh.face_normals @ view > 1e-6
    return trimesh.Trimesh(mesh.vertices, mesh.faces[keep], process=False)


def light_vector(elev: float, azim: float) -> np.ndarray:
    """Key light that follows the camera: offset in elevation away from the horizon (so a view from below
    the back face is lit from below, not left in shadow by a light fixed above the part) and in azimuth."""
    sgn = -1.0 if elev < 0 else 1.0
    return view_vector(elev + sgn * LIGHT_ELEV_OFFSET, azim + LIGHT_AZIM_OFFSET)


def _shade(mesh: trimesh.Trimesh, base, light: np.ndarray) -> np.ndarray:
    n = mesh.face_normals
    inten = AMBIENT + DIFFUSE * np.clip(n @ light, 0.0, 1.0)
    return np.clip(np.asarray(base)[None, :] * inten[:, None], 0.0, 1.0)


def render_iso(items: list[tuple[trimesh.Trimesh, str, tuple]], path: str, title: str, dpi: int,
               elev: float = 30.0, azim: float = -60.0, size: float = 7.0):
    """items: [(mesh, part_name, (dx, dy, dz))]. One Poly3DCollection per mesh, Lambert shaded."""
    fig = plt.figure(figsize=(size, size), dpi=dpi)
    ax = fig.add_subplot(111, projection="3d")
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    view = view_vector(elev, azim)
    light = light_vector(elev, azim)
    for mesh, name, off in items:
        if name not in ALPHA:                     # opaque: draw only the faces that face the camera
            mesh = cull_backfaces(mesh, view)
        v = mesh.vertices + np.asarray(off, dtype=float)
        tri = v[mesh.faces]
        cols = _shade(mesh, COLOURS.get(name, (0.7, 0.5, 0.2)), light)
        a = ALPHA.get(name, 1.0)
        rgba = np.column_stack([cols, np.full(len(cols), a)])
        # opaque faces get a hairline edge in their own colour: it fills the anti-aliasing seams between
        # neighbouring triangles that otherwise show as a white wireframe
        pc = Poly3DCollection(tri, facecolors=rgba, edgecolors="none" if a < 1 else rgba,
                              linewidths=0.0 if a < 1 else 0.25, shade=False)
        ax.add_collection3d(pc)
        lo = np.minimum(lo, v.min(axis=0))
        hi = np.maximum(hi, v.max(axis=0))
    span = hi - lo
    pad = 0.03 * span.max()
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_zlim(lo[2] - pad, hi[2] + pad)
    ax.set_box_aspect(span + 2 * pad)          # equal aspect
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.set_title(title, fontsize=9 if len(title) < 80 else 7.5, pad=2, wrap=True)
    fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=0.96)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)


# --------------------------------------------------------------------------- sections
def section_loops(mesh: trimesh.Trimesh, normal, origin, tol: float = 1e-5) -> list[np.ndarray]:
    """Closed loops (n, 3) of the section of `mesh` by the plane, from the boundary of the sliced half mesh."""
    normal = np.asarray(normal, dtype=float)
    origin = np.asarray(origin, dtype=float)
    try:
        half = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=normal, plane_origin=origin, cap=False)
    except ModuleNotFoundError:
        # slice_mesh_plane imports shapely-backed helpers even for cap=False; slice_faces_plane is the
        # same cut on the raw arrays and needs only numpy
        v, f, _ = trimesh.intersections.slice_faces_plane(mesh.vertices, mesh.faces, normal, origin)
        half = trimesh.Trimesh(v, f, process=False)
    if half is None or len(half.faces) == 0:
        return []
    half.merge_vertices()
    edges = half.edges_sorted
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = uniq[counts == 1]
    if len(boundary) == 0:
        return []
    dist = np.abs((half.vertices - origin) @ normal)
    on_plane = dist < tol
    keep = boundary[on_plane[boundary[:, 0]] & on_plane[boundary[:, 1]]]
    if len(keep) == 0:
        return []
    # chain by vertex index (merge_vertices made shared endpoints identical); fall back to nearest end
    adj: dict[int, list[int]] = {}
    for a, b in keep:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    unused = {tuple(sorted(e)) for e in keep.tolist()}
    loops = []
    while unused:
        a, b = next(iter(unused))
        unused.discard((a, b))
        loop = [a, b]
        while True:
            cur = loop[-1]
            nxt = None
            for c in adj.get(cur, []):
                key = tuple(sorted((cur, c)))
                if key in unused:
                    nxt = c
                    unused.discard(key)
                    break
            if nxt is None or nxt == loop[0]:
                break
            loop.append(nxt)
        if len(loop) >= 3:
            loops.append(half.vertices[loop])
    return loops


def _signed_area(poly2: np.ndarray) -> float:
    x, y = poly2[:, 0], poly2[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def fill_loops(ax, loops2: list[np.ndarray], colour, alpha: float, label: str | None):
    """Even-odd nesting: loops at even depth are outer boundaries, odd-depth loops are holes."""
    loops2 = [l for l in loops2 if abs(_signed_area(l)) > 1e-6]
    if not loops2:
        return
    paths = [Path(l) for l in loops2]
    depth = []
    for i, l in enumerate(loops2):
        d = 0
        for j, pth in enumerate(paths):
            if i != j and pth.contains_point(l[0]) and abs(_signed_area(loops2[j])) > abs(_signed_area(l)):
                d += 1
        depth.append(d)
    first = True
    for i, l in enumerate(loops2):
        if depth[i] % 2:
            continue
        outer = l if _signed_area(l) > 0 else l[::-1]
        verts = [outer]
        for j, h in enumerate(loops2):
            if depth[j] == depth[i] + 1 and paths[i].contains_point(h[0]):
                verts.append(h if _signed_area(h) < 0 else h[::-1])
        v, codes = [], []
        for poly in verts:
            v.extend(poly.tolist())
            codes.extend([Path.MOVETO] + [Path.LINETO] * (len(poly) - 1))
            v.append(poly[0].tolist())
            codes.append(Path.CLOSEPOLY)
        patch = PathPatch(Path(v, codes), facecolor=colour, edgecolor="black", lw=0.5, alpha=alpha,
                          label=label if first else None)
        ax.add_patch(patch)
        first = False


def render_section(meshes: dict, plane: str, path: str, profile: str, dpi: int):
    """plane 'yz' (x = 0, drawn as Y across / Z up) or 'xz' (y = 0, X across / Z up)."""
    if plane == "yz":
        normal, origin, cols, xl = (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1, 2), "Y  (12 o'clock ->)  mm"
        ttl = f"{profile}: assembly section at x = 0 (YZ plane), viewed from 3 o'clock"
    else:
        normal, origin, cols, xl = (0.0, 1.0, 0.0), (0.0, 0.0, 0.0), (0, 2), "X  (crown ->)  mm"
        ttl = f"{profile}: assembly section at y = 0 (XZ plane), viewed from 6 o'clock"
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=dpi)
    order = ["case_body", "caseback", "spacer_ring", "movement", "battery", "dial_blank", "hands_envelope",
             "iring", "crystal", "gasket", "oring", "tube", "crown"]
    n_drawn = 0
    for name in order:
        if name not in meshes:
            continue
        loops = section_loops(meshes[name], normal, origin)
        if not loops:
            continue
        loops2 = [l[:, cols] for l in loops]
        fill_loops(ax, loops2, COLOURS[name], 0.9 if name not in ALPHA else 0.6, LABEL[name])
        n_drawn += 1
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_xlabel(xl, fontsize=8)
    ax.set_ylabel("Z  (front ->)  mm", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(True, lw=0.3, alpha=0.4)
    ax.set_title(ttl, fontsize=9)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=6, fontsize=7, frameon=False)
    fig.subplots_adjust(left=0.06, right=0.99, bottom=0.26, top=0.93)
    fig.savefig(path, dpi=dpi, facecolor="white", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return n_drawn


# --------------------------------------------------------------------------- main
def remove_stale_seal_renders(profile: str, ren_dir: str) -> list[str]:
    """Drop renders of the caseback seal that params.cb_seal does NOT select (left over from a run with the
    other seal, or from the v0.1 two-seal export), so out/renders shows exactly the assembled set."""
    keep = purchased.seal_part_name(params.get(profile))
    removed = []
    for seal in purchased.SEAL_NAMES:
        if seal == keep:
            continue
        for suffix in ("_iso.png", "_iso_back.png"):
            path = os.path.join(ren_dir, f"{profile}_{seal}{suffix}")
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)
    return removed


def run_profile(profile: str, out_root: str, ren_dir: str, dpi: int) -> list[str]:
    written = []
    for path in remove_stale_seal_renders(profile, ren_dir):
        print(f"  {profile}: removed stale {os.path.basename(path)} (seal not selected by params.cb_seal)")
    meshes = load_meshes(out_root, profile)
    t0 = time.time()
    fine = {n: refine(m) for n, m in meshes.items()}
    print(f"  {profile}: meshes refined for rendering in {time.time() - t0:.1f} s "
          f"({sum(len(m.faces) for m in meshes.values())} -> {sum(len(m.faces) for m in fine.values())} faces)")
    t0 = time.time()
    n_views = 0
    for name, m in meshes.items():
        path = os.path.join(ren_dir, f"{profile}_{name}_iso.png")
        render_iso([(fine[name], name, (0, 0, 0))], path, f"{profile}: {LABEL[name]}  ({len(m.faces)} STL faces)", dpi)
        written.append(path)
        n_views += 1
        if name in BACK_VIEWS:
            # seen from below the back face (-Z): elev < 0 puts the camera under the XY plane
            path = os.path.join(ren_dir, f"{profile}_{name}_iso_back.png")
            render_iso([(fine[name], name, (0, 0, 0))], path,
                       f"{profile}: {LABEL[name]} from the back -- {BACK_VIEWS[name]}", dpi, elev=-32, azim=-60)
            written.append(path)
            n_views += 1
    print(f"  {profile}: {n_views} part renders ({len(meshes)} parts, {len(BACK_VIEWS)} back views) in {time.time() - t0:.1f} s")
    t0 = time.time()
    items = [(fine[n], n, EXPLODE.get(n, (0, 0, 0))) for n in meshes]
    path = os.path.join(ren_dir, f"{profile}_exploded_iso.png")
    render_iso(items, path, f"{profile}: exploded assembly (parts offset along Z; tube and crown along X)", dpi,
               elev=32, azim=-55, size=8.0)
    written.append(path)
    print(f"  {profile}: exploded view in {time.time() - t0:.1f} s")
    for plane in ("yz", "xz"):
        t0 = time.time()
        path = os.path.join(ren_dir, f"{profile}_section_{plane}.png")
        n = render_section(meshes, plane, path, profile, dpi)
        written.append(path)
        print(f"  {profile}: section {plane} ({n} parts cut) in {time.time() - t0:.1f} s")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", choices=(*PROFILES, "all"), default="all")
    ap.add_argument("--out", default="out", help="output root; STLs are read from out/<profile>/, PNGs go to out/renders/")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args(argv)
    out_root = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
    ren_dir = os.path.join(out_root, "renders")
    os.makedirs(ren_dir, exist_ok=True)
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    t0 = time.time()
    written = []
    for prof in profiles:
        print(f"renders: profile {prof}")
        written += run_profile(prof, out_root, ren_dir, args.dpi)
    print(f"wrote {len(written)} PNG(s) to {ren_dir} in {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
