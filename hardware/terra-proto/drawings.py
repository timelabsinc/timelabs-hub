#!/usr/bin/env python3
"""
Blueprints for the Terra-style GL32 case prototype (SPEC.md section 9).

    python3 drawings.py [--out out] [--date 2026-09-29] [--png DIR] [--sheets 1 3]

Writes out/drawings/blueprints.pdf (A4 landscape, PdfPages) and one out/drawings/sheet_NN_<name>.svg
per sheet.  --png DIR additionally writes a 150 dpi PNG preview of every sheet into DIR (review aid).
--sheets N [N ...] renders only those sheets (the PDF then holds only them: run without it for the deliverable).

Every outline on every sheet is taken from the CadQuery solids of the part modules, never drawn from
hand primitives:

  * plan views  -- OCC hidden-line removal (HLRBRep_Algo) of the solid along the viewing direction;
                   visible edges solid, hidden edges dashed
  * sections    -- the true planar section faces (Workplane.section) of the solid, hatched
  * detail views -- the same section faces at a larger scale inside a clipping circle

The dimensions, notes and tables are overlaid with matplotlib from `params.py` (titanium profile: the
resin differences are tabulated on sheets 8 and 9).  Values that SPEC.md section 11 lists as
UNVERIFIED carry an asterisk and a footnote.

Sheets
  1 case body, front view              6 spacer ring, plan + sections
  2 case body, back view               7 dial blank
  3 case body, section A-A (YZ, x=0)   8 assembly section A-A, stack-up, purchased parts
  4 case body, section B-B (XZ, y=0)   9 resin vs titanium, print orientation
  5 caseback, plan + section
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import textwrap
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cadquery as cq  # noqa: E402
from OCP.BRepLib import BRepLib  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402
from OCP.HLRAlgo import HLRAlgo_Projector  # noqa: E402
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

import params  # noqa: E402
from parts import case_body, caseback, dial_blank, purchased, spacer_ring  # noqa: E402

# --------------------------------------------------------------------------- style
PAPER_W, PAPER_H = 297.0, 210.0          # A4 landscape, mm
MARGIN = 8.0
TB_W, TB_H = 118.0, 34.0                 # title block
FS_DIM = 6.3                             # dimension text, pt
FS_NOTE = 6.0
FS_SMALL = 5.4
FS_TITLE = 10.5
LW_VIS = 0.85
LW_HID = 0.45
LW_THIN = 0.4
ARROW = "-|>,head_length=0.62,head_width=0.2"
ARROW2 = "<|-|>,head_length=0.62,head_width=0.2"
MUT = 10                                 # mutation_scale for the arrow heads (points)
HATCH_LW = 0.35
GAP = 0.8                                # extension-line gap from the outline, paper mm
EXT = 1.5                                # extension-line overshoot past the dimension line, paper mm

matplotlib.rcParams["hatch.linewidth"] = HATCH_LW
matplotlib.rcParams["font.family"] = "DejaVu Sans"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["svg.fonttype"] = "none"

PART_COLOURS = {
    "case_body": "#8f9297", "caseback": "#b9bcc3", "spacer_ring": "#3a3a3a", "dial_blank": "#d9d2b0",
    "crystal": "#7fb6e8", "iring": "#5a5a5a", "oring": "#202020", "tube": "#a3a6ae", "crown": "#7b7f88",
    "movement": "#d6b64c", "hands_envelope": "#ff8a1e", "battery": "#c9c9cd",
}
PART_HATCH = {
    "case_body": "////", "caseback": "\\\\\\\\", "spacer_ring": "xx", "dial_blank": "||||",
    "crystal": "...", "iring": "++", "oring": "xx", "tube": "\\\\\\\\", "crown": "////",
    "movement": "++", "hands_envelope": "..", "battery": "--",
}
PART_LABEL = {
    "case_body": "case body", "caseback": "caseback", "spacer_ring": "spacer ring", "dial_blank": "dial",
    "crystal": "crystal", "iring": "I-ring", "oring": "O-ring", "tube": "tube", "crown": "crown",
    "movement": "movement (GL32 envelope)", "hands_envelope": "hands envelope", "battery": "battery",
}

UNVERIFIED_NOTE = ("* UNVERIFIED value (SPEC.md section 11): catalogue figure or photo estimate, "
                   "measure on the real part before titanium is cut.")


# =========================================================================== geometry extraction
def _edge_points(e: cq.Edge, tol: float = 0.01) -> np.ndarray:
    """Polyline (n, 3) along an edge; chord error <= tol on circles, dense sampling elsewhere."""
    gt = e.geomType()
    if gt == "LINE":
        a, b = e.startPoint(), e.endPoint()
        return np.array([[a.x, a.y, a.z], [b.x, b.y, b.z]])
    length = e.Length()
    if length < 1e-9:
        a = e.startPoint()
        return np.array([[a.x, a.y, a.z]])
    if gt == "CIRCLE":
        r = e.radius()
        theta = 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 8
        n = max(6, int(math.ceil((length / r) / theta)) + 1)
    else:
        n = max(12, min(400, int(length / 0.1) + 1))
    pts = [e.positionAt(t) for t in np.linspace(0.0, 1.0, n)]
    return np.array([[v.x, v.y, v.z] for v in pts])


def _chain(segments: list[np.ndarray]) -> np.ndarray:
    """Greedy nearest-end chaining of edge polylines into one closed polyline."""
    segs = [s for s in segments if len(s) >= 2]
    if not segs:
        return np.zeros((0, 3))
    out = [segs.pop(0)]
    cur = out[0][-1]
    while segs:
        best, best_d, rev = None, None, False
        for i, s in enumerate(segs):
            d0 = np.linalg.norm(s[0] - cur)
            d1 = np.linalg.norm(s[-1] - cur)
            d, r = (d0, False) if d0 <= d1 else (d1, True)
            if best_d is None or d < best_d:
                best, best_d, rev = i, d, r
        s = segs.pop(best)
        if rev:
            s = s[::-1]
        out.append(s[1:] if best_d < 1e-6 else s)
        cur = s[-1]
    pts = np.vstack(out)
    if np.linalg.norm(pts[0] - pts[-1]) > 1e-6:
        pts = np.vstack([pts, pts[:1]])
    return pts


def wire_polyline(w: cq.Wire) -> np.ndarray:
    return _chain([_edge_points(e) for e in w.Edges()])


PLANE_UV = {"YZ": (1, 2), "XZ": (0, 2), "XY": (0, 1)}


def section_faces(solid: cq.Shape, plane: str, offset: float = 0.0) -> list[tuple[np.ndarray, list[np.ndarray]]]:
    """True planar section of `solid` (YZ at x=offset, XZ at y=offset, XY at z=offset).
    Returns [(outer_uv, [inner_uv, ...]), ...] as (n, 2) arrays in the plane's (u, v) coordinates."""
    origin = {"YZ": (offset, 0, 0), "XZ": (0, offset, 0), "XY": (0, 0, offset)}[plane]
    iu, iv = PLANE_UV[plane]
    wp = cq.Workplane(plane, origin=origin).add(solid).section()
    faces = []
    for f in wp.faces().vals():
        if f.Area() < 1e-6:
            continue
        outer = wire_polyline(f.outerWire())[:, [iu, iv]]
        inners = [wire_polyline(w)[:, [iu, iv]] for w in f.innerWires()]
        faces.append((outer, inners))
    return faces


def hlr_view(shape: cq.Shape, view_dir: tuple, x_dir: tuple) -> dict:
    """Hidden-line-removed projection. view_dir points from the part toward the viewer; x_dir is the
    model direction drawn along +u.  Returns {'visible': [(n,2)...], 'hidden': [(n,2)...]}."""
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*view_dir), gp_Dir(*x_dir)))
    algo.Projector(projector)
    algo.Update()
    algo.Hide()
    hs = HLRBRep_HLRToShape(algo)
    out = {"visible": [], "hidden": []}
    groups = (("visible", hs.VCompound), ("visible", hs.OutLineVCompound),
              ("hidden", hs.HCompound), ("hidden", hs.OutLineHCompound))
    for key, fn in groups:
        comp = fn()
        if comp.IsNull():
            continue
        BRepLib.BuildCurves3d_s(comp, 1e-4)
        ex = TopExp_Explorer(comp, TopAbs_EDGE)
        while ex.More():
            pts = _edge_points(cq.Edge(ex.Current()))
            if len(pts) >= 2:
                out[key].append(pts[:, :2])
            ex.Next()
    return out


def _signed_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _oriented(poly: np.ndarray, ccw: bool) -> np.ndarray:
    return poly if (_signed_area(poly) > 0) == ccw else poly[::-1]


# =========================================================================== sheet + view framework
class Sheet:
    """One A4-landscape figure; all coordinates are paper millimetres (origin bottom-left)."""

    def __init__(self, n: int, total: int, title: str, part: str, material: str, scale_text: str,
                 date: str, profile: str = "titanium", file_stem: str = ""):
        self.n, self.total, self.title, self.file_stem = n, total, title, file_stem
        self.fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, PAPER_W)
        self.ax.set_ylim(0, PAPER_H)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self.footnote_needed = False
        self._frame()
        self._title_block(part, material, scale_text, date, profile)

    # -- frame and title block
    def _frame(self):
        self.ax.add_patch(Rectangle((MARGIN, MARGIN), PAPER_W - 2 * MARGIN, PAPER_H - 2 * MARGIN,
                                    fill=False, lw=1.0, ec="black"))

    def _title_block(self, part, material, scale_text, date, profile):
        x0 = PAPER_W - MARGIN - TB_W
        y0 = MARGIN
        ax = self.ax
        ax.add_patch(Rectangle((x0, y0), TB_W, TB_H, fill=True, fc="white", ec="black", lw=0.8))
        rows = [
            ("PROJECT", "Terra-style GL32 case prototype  (v0.1 design intent)"),
            ("TITLE", self.title),
            ("PART / MATERIAL", f"{part}  --  {material}"),
            ("PROFILE", profile),
            ("SCALE", scale_text),
            ("DATE", date),
        ]
        rh = TB_H / (len(rows) + 1)
        for i, (k, v) in enumerate(rows):
            y = y0 + TB_H - (i + 1) * rh
            ax.plot([x0, x0 + TB_W], [y, y], color="black", lw=0.4)
            ax.text(x0 + 1.5, y + rh / 2, k, fontsize=FS_SMALL, va="center", ha="left", color="#444444")
            ax.text(x0 + 30, y + rh / 2, v, fontsize=FS_NOTE if k != "TITLE" else 7.2, va="center", ha="left",
                    fontweight="bold" if k == "TITLE" else "normal")
        y = y0
        ax.text(x0 + 1.5, y + rh / 2, "SHEET", fontsize=FS_SMALL, va="center", ha="left", color="#444444")
        ax.text(x0 + 30, y + rh / 2, f"{self.n} / {self.total}", fontsize=FS_NOTE, va="center", ha="left",
                fontweight="bold")
        ax.text(x0 + 60, y + rh / 2, "units mm  |  drawn from params.py / CadQuery sections",
                fontsize=FS_SMALL, va="center", ha="left", color="#444444")
        ax.plot([x0 + 28, x0 + 28], [y0, y0 + TB_H], color="black", lw=0.4)

    # -- text helpers
    def text(self, x, y, s, size=FS_NOTE, **kw):
        kw.setdefault("va", "top")
        kw.setdefault("ha", "left")
        return self.ax.text(x, y, s, fontsize=size, **kw)

    def notes(self, x, y, lines, width_chars=62, size=FS_NOTE, title=None, line_h=None):
        """Block of wrapped notes, top-left anchored at (x, y). Returns the y below the block."""
        lh = line_h or (size * 0.352778 * 1.45)
        if title:
            self.text(x, y, title, size=size + 0.6, fontweight="bold")
            y -= lh * 1.25
        for ln in lines:
            if ln == "":
                y -= lh * 0.5
                continue
            wrapped = textwrap.wrap(ln, width_chars, subsequent_indent="   ") or [""]
            for w in wrapped:
                self.text(x, y, w, size=size)
                y -= lh
        return y

    def table(self, x, y, header, rows, col_w, size=FS_SMALL, wrap=True, title=None, row_pad=0.7):
        """Simple ruled table, top-left anchored. col_w in mm. Returns the y below the table."""
        ax = self.ax
        lh = size * 0.352778 * 1.35
        if title:
            self.text(x, y, title, size=size + 1.0, fontweight="bold")
            y -= lh * 1.5
        char_w = size * 0.352778 * 0.58

        def cells(row):
            out = []
            for c, w in zip(row, col_w):
                n = max(4, int(w / char_w) - 1)
                out.append(textwrap.wrap(str(c), n) if wrap else [str(c)])
            return out

        total_w = sum(col_w)
        y_top = y
        all_rows = [header] + rows
        for ri, row in enumerate(all_rows):
            cl = cells(row)
            nl = max(len(c) for c in cl)
            h = nl * lh + row_pad
            cx = x
            for c, w in zip(cl, col_w):
                for li, s in enumerate(c):
                    ax.text(cx + 0.8, y - row_pad / 2 - li * lh, s, fontsize=size, va="top", ha="left",
                            fontweight="bold" if ri == 0 else "normal")
                cx += w
            y -= h
            ax.plot([x, x + total_w], [y, y], color="black", lw=0.6 if ri == 0 else 0.3)
        ax.plot([x, x + total_w], [y_top, y_top], color="black", lw=0.6)
        cx = x
        for w in col_w + [0]:
            ax.plot([cx, cx], [y_top, y], color="black", lw=0.3)
            cx += w
        return y

    def footnote(self):
        if self.footnote_needed:
            self.text(MARGIN + 2, MARGIN + 2.2, UNVERIFIED_NOTE, size=FS_SMALL, va="bottom")

    def general_notes(self, extra: list[str], x=None, y=None, width_chars=64):
        x = MARGIN + 2 if x is None else x
        y = MARGIN + TB_H + 2 if y is None else y
        lines = ["General tolerance +-0.05 (titanium build) / +-0.15 (resin fit-check print).",
                 "Break all edges 0.1 unless a chamfer or radius is called out."] + extra
        return self.notes(x, y, lines, width_chars=width_chars, title="NOTES")

    def save(self, pdf: PdfPages, out_dir: str, png_dir: str | None):
        self.footnote()
        pdf.savefig(self.fig)
        self.fig.savefig(os.path.join(out_dir, f"sheet_{self.n:02d}_{self.file_stem}.svg"), format="svg")
        if png_dir:
            self.fig.savefig(os.path.join(png_dir, f"sheet_{self.n:02d}_{self.file_stem}.png"), dpi=150)
        plt.close(self.fig)


class View:
    """Maps model (u, v) to paper (x, y): x = ox + s*u, y = oy + s*v.  Optional circular clip (detail)."""

    def __init__(self, sheet: Sheet, origin: tuple, scale: float, clip: tuple | None = None):
        self.sheet, self.ax = sheet, sheet.ax
        self.ox, self.oy = origin
        self.s = scale
        self.clip = None
        if clip is not None:
            cx, cy, r = clip
            self.clip = Circle((cx, cy), r, transform=self.ax.transData, fill=False, lw=0.6, ec="black")
            self.ax.add_patch(self.clip)

    def P(self, u, v):
        return (self.ox + self.s * u, self.oy + self.s * v)

    def U(self, x, y):
        return ((x - self.ox) / self.s, (y - self.oy) / self.s)

    def _clipped(self, artist):
        if self.clip is not None:
            artist.set_clip_path(self.clip)
        return artist

    # -- geometry
    def polylines(self, plines, lw=LW_VIS, color="black", ls="-", zorder=3):
        segs = [np.column_stack(self.P(pl[:, 0], pl[:, 1])) for pl in plines if len(pl) >= 2]
        if not segs:
            return None
        lc = LineCollection(segs, linewidths=lw, colors=color, linestyles=ls, zorder=zorder,
                            capstyle="round", joinstyle="round")
        self.ax.add_collection(lc)
        return self._clipped(lc)

    def hlr(self, view: dict, hidden=True):
        if hidden and view["hidden"]:
            self.polylines(view["hidden"], lw=LW_HID, color="#666666", ls=(0, (3.0, 1.6)), zorder=2)
        self.polylines(view["visible"], lw=LW_VIS, color="black", zorder=3)

    def section(self, faces, hatch="////", fc="white", hc="black", outline=True, alpha=1.0, zorder=2):
        """Hatched section faces [(outer, inners)]."""
        for outer, inners in faces:
            verts, codes = [], []
            for poly, ccw in [(outer, True)] + [(inn, False) for inn in inners]:
                poly = _oriented(poly, ccw)
                pts = np.column_stack(self.P(poly[:, 0], poly[:, 1]))
                verts.extend(pts.tolist())
                codes.extend([Path.MOVETO] + [Path.LINETO] * (len(pts) - 2) + [Path.CLOSEPOLY])
            patch = PathPatch(Path(verts, codes), facecolor=fc, edgecolor=hc, hatch=hatch, lw=0.0,
                              alpha=alpha, zorder=zorder)
            self.ax.add_patch(patch)
            self._clipped(patch)
            if outline:
                self.polylines([outer] + list(inners), lw=LW_VIS, color="black", zorder=zorder + 1)

    def centerline(self, u0, v0, u1, v1, color="#1f5fa8"):
        (x0, y0), (x1, y1) = self.P(u0, v0), self.P(u1, v1)
        ln, = self.ax.plot([x0, x1], [y0, y1], color=color, lw=LW_THIN, ls=(0, (6, 1.5, 1.2, 1.5)), zorder=1)
        return self._clipped(ln)

    def center_mark(self, u, v, r_model, ext=1.5):
        e = ext / self.s
        self.centerline(u - r_model - e, v, u + r_model + e, v)
        self.centerline(u, v - r_model - e, u, v + r_model + e)

    # -- dimensioning (paper-mm layout, model-mm values)
    def _arrow(self, p0, p1, style=ARROW, color="black"):
        a = FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=MUT, lw=0.5, color=color,
                            shrinkA=0, shrinkB=0, zorder=5)
        self.ax.add_patch(a)
        return a

    def _thin(self, p0, p1, color="black"):
        self.ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=color, lw=0.45, zorder=4, solid_capstyle="butt")

    def _label(self, x, y, text, rot=0, ha="center", va="center", size=FS_DIM, color="black", box=True):
        bb = dict(boxstyle="square,pad=0.12", fc="white", ec="none", alpha=0.9) if box else None
        if "*" in text:
            self.sheet.footnote_needed = True
        return self.ax.text(x, y, text, fontsize=size, rotation=rot, ha=ha, va=va, color=color,
                            bbox=bb, zorder=6)

    def dim_h(self, u1, u2, v, off, text, text_side=1, outside=None, tpos=None, text_at="right"):
        """Horizontal dimension between model u1 and u2 (measured at v); dimension line `off` paper mm
        from v (sign = direction). text_side: +1 above the line, -1 below. tpos: fraction along the line.
        Short dimensions get outside arrows and the text beside them (text_at 'right' / 'left')."""
        if u2 < u1:
            u1, u2 = u2, u1
        (x1, y0), (x2, _) = self.P(u1, v), self.P(u2, v)
        y = y0 + off
        sgn = 1 if off >= 0 else -1
        for x in (x1, x2):
            self._thin((x, y0 + sgn * GAP), (x, y + sgn * EXT))
        length = x2 - x1
        if outside is None:
            outside = length < 7.0
        if outside:
            self._arrow((x1 - 6, y), (x1, y))
            self._arrow((x2 + 6, y), (x2, y))
        else:
            self._arrow((x1, y), (x2, y), ARROW2)
        tx = (x1 + x2) / 2 if tpos is None else x1 + tpos * length
        if outside and tpos is None:
            if text_at == "left":
                self._label(x1 - 6.5, y, text, ha="right")
            else:
                self._label(x2 + 6.5, y, text, ha="left")
        else:
            self._label(tx, y + text_side * 1.1, text, va="bottom" if text_side > 0 else "top")

    def dim_v(self, v1, v2, u, off, text, text_side=-1, outside=None, tpos=None, text_at="high"):
        """Vertical dimension between model v1 and v2 (at u); dimension line `off` paper mm from u.
        Short dimensions get outside arrows and rotated text beyond them (text_at 'high' / 'low')."""
        if v2 < v1:
            v1, v2 = v2, v1
        (x0, y1), (_, y2) = self.P(u, v1), self.P(u, v2)
        x = x0 + off
        sgn = 1 if off >= 0 else -1
        for y in (y1, y2):
            self._thin((x0 + sgn * GAP, y), (x + sgn * EXT, y))
        length = y2 - y1
        if outside is None:
            outside = length < 7.0
        if outside:
            self._arrow((x, y1 - 6), (x, y1))
            self._arrow((x, y2 + 6), (x, y2))
        else:
            self._arrow((x, y1), (x, y2), ARROW2)
        ty = (y1 + y2) / 2 if tpos is None else y1 + tpos * length
        if outside and tpos is None:
            if text_at == "low":
                self._label(x, y1 - 6.5, text, rot=90, va="top")
            else:
                self._label(x, y2 + 6.5, text, rot=90, va="bottom")
        else:
            self._label(x + text_side * 1.1, ty, text, rot=90, ha="right" if text_side < 0 else "left")

    def dim_aligned(self, p1, p2, off, text):
        """Dimension along the segment p1-p2 (model), offset `off` paper mm to its left normal."""
        (x1, y1), (x2, y2) = self.P(*p1), self.P(*p2)
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        nx, ny = -dy / ln, dx / ln
        ex = (x1 + nx * off, y1 + ny * off)
        ey = (x2 + nx * off, y2 + ny * off)
        sgn = 1 if off >= 0 else -1
        self._thin((x1 + nx * sgn * GAP, y1 + ny * sgn * GAP), (ex[0] + nx * sgn * EXT, ex[1] + ny * sgn * EXT))
        self._thin((x2 + nx * sgn * GAP, y2 + ny * sgn * GAP), (ey[0] + nx * sgn * EXT, ey[1] + ny * sgn * EXT))
        if ln < 7:
            ux, uy = dx / ln, dy / ln
            self._arrow((ex[0] - ux * 6, ex[1] - uy * 6), ex)
            self._arrow((ey[0] + ux * 6, ey[1] + uy * 6), ey)
            self._label(ey[0] + ux * 7, ey[1] + uy * 7, text, rot=math.degrees(math.atan2(dy, dx)))
        else:
            self._arrow(ex, ey, ARROW2)
            ang = math.degrees(math.atan2(dy, dx))
            if ang > 90 or ang <= -90:
                ang += 180
            self._label((ex[0] + ey[0]) / 2 + nx * 1.1, (ex[1] + ey[1]) / 2 + ny * 1.1, text, rot=ang)

    def dim_dia(self, c, r, angle_deg, text, leader=8.0, shoulder=5.0, ha=None):
        """Diameter/radius callout: arrow on the circle at `angle_deg`, leader outward, horizontal shoulder."""
        a = math.radians(angle_deg)
        cx, cy = self.P(*c)
        rp = r * self.s
        p_circ = (cx + rp * math.cos(a), cy + rp * math.sin(a))
        p_elbow = (p_circ[0] + leader * math.cos(a), p_circ[1] + leader * math.sin(a))
        right = math.cos(a) >= 0
        p_end = (p_elbow[0] + (shoulder if right else -shoulder), p_elbow[1])
        self._arrow(p_elbow, p_circ)
        self._thin(p_elbow, p_end)
        ha = ha or ("left" if right else "right")
        self._label(p_end[0] + (0.6 if right else -0.6), p_end[1] + 0.9, text, ha=ha, va="bottom")

    def leader(self, p_model, text, dx, dy, ha=None, size=FS_DIM, shoulder=4.0, arrow=True):
        """Note leader from model point p_model to text at paper offset (dx, dy)."""
        px, py = self.P(*p_model)
        elbow = (px + dx, py + dy)
        right = dx >= 0
        end = (elbow[0] + (shoulder if right else -shoulder), elbow[1])
        if arrow:
            self._arrow(elbow, (px, py))
        else:
            self._thin(elbow, (px, py))
        self._thin(elbow, end)
        ha = ha or ("left" if right else "right")
        return self._label(end[0] + (0.6 if right else -0.6), end[1], text, ha=ha, va="center", size=size)

    def section_line(self, p1, p2, letter, arrow_dir, ext=6.0):
        """Cutting-plane marker between model points p1 and p2; arrow_dir = (dx, dy) paper direction of view."""
        (x1, y1), (x2, y2) = self.P(*p1), self.P(*p2)
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        ux, uy = dx / ln, dy / ln
        ax = self.ax
        ax.plot([x1 - ux * ext, x1 - ux * ext + ux * 5], [y1 - uy * ext, y1 - uy * ext + uy * 5], color="black", lw=1.4, zorder=5)
        ax.plot([x2 + ux * ext - ux * 5, x2 + ux * ext], [y2 + uy * ext - uy * 5, y2 + uy * ext], color="black", lw=1.4, zorder=5)
        ax.plot([x1, x2], [y1, y2], color="black", lw=0.5, ls=(0, (8, 2, 1.5, 2)), zorder=4)
        adx, ady = arrow_dir
        for (x, y) in ((x1 - ux * ext, y1 - uy * ext), (x2 + ux * ext, y2 + uy * ext)):
            self._arrow((x, y), (x + adx * 5, y + ady * 5))
            ax.text(x - adx * 2.5, y - ady * 2.5, letter, fontsize=8, fontweight="bold", ha="center", va="center")

    def ordinate_fan(self, levels, u_tick, x_text, y_lo, y_hi, side=1, size=FS_DIM, tick=4.0):
        """Fan of leaders for a stack of v-levels: each level gets a short tick at model u_tick and a
        leader to text spaced evenly between paper y_lo and y_hi (no overlaps)."""
        levels = sorted(levels, key=lambda t: t[0])
        n = len(levels)
        ys = np.linspace(y_lo, y_hi, n) if n > 1 else [0.5 * (y_lo + y_hi)]
        for (v, text), yt in zip(levels, ys):
            x0, y0 = self.P(u_tick, v)
            x1 = x0 + side * tick
            self._thin((x0, y0), (x1, y0))
            self._thin((x1, y0), (x_text - side * 1.5, yt))
            self._label(x_text, yt, text, ha="left" if side > 0 else "right", size=size)

    def label(self, u, v, text, size=FS_DIM, **kw):
        x, y = self.P(u, v)
        return self._label(x, y, text, size=size, **kw)

    def title(self, text, x=None, y=None, size=7.5):
        x = self.ox if x is None else x
        self.sheet.text(x, y, text, size=size, ha="center", va="top", fontweight="bold")


# =========================================================================== part builders
class Parts:
    """Titanium solids of every part (drawings dimension the titanium build)."""

    def __init__(self, p: params.Params):
        self.p = p
        t0 = time.time()
        self.case = case_body.build(p).val()
        self.back = caseback.build(p).val()
        self.ring = spacer_ring.build(p).val()
        self.dial = dial_blank.build(p).val()
        self.bought = {name: fn(p).val() for name, fn in purchased.PARTS.items()}
        self.ring_geo = spacer_ring.geometry(p)
        print(f"  parts built in {time.time() - t0:.1f} s")

    def solids(self) -> dict:
        d = {"case_body": self.case, "caseback": self.back, "spacer_ring": self.ring, "dial_blank": self.dial}
        d.update(self.bought)
        return d


def fmt(v: float, nd: int = 2) -> str:
    s = f"{v:.{nd}f}"
    return s


# =========================================================================== sheets
def sheet_case_front(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "CASE BODY  --  front view (crystal side)", "case_body",
               f"{case_body.MATERIAL['titanium']} (resin: {case_body.MATERIAL['resin']})", "2:1", date,
               file_stem="case_body_front")
    v = View(sh, (112, 120), 2.0)
    v.hlr(hlr_view(parts.case, (0, 0, 1), (1, 0, 0)))
    v.center_mark(0, 0, p.crystal_bore_d / 2, ext=8)
    cc = p.corner_chamfer
    # overall and corner chamfer
    v.dim_h(-p.W / 2, p.W / 2, -p.L / 2, -14, fmt(p.W, 1))
    v.dim_v(-p.L / 2, p.L / 2, -p.W / 2, -16, fmt(p.L, 1))
    v.dim_h(-p.W / 2, -p.W / 2 + cc, p.L / 2, 8, f"{cc:.1f} x 45 deg (4x)", outside=True)
    v.dim_v(p.L / 2 - cc, p.L / 2, -p.W / 2, -8, fmt(cc, 1), outside=True)
    # bores
    v.dim_dia((0, 0), p.crystal_bore_d / 2, 150, f"Ø{p.crystal_bore_d:.2f} +0.05/0  x {p.crystal_engagement:.1f} deep\n"
              f"(crystal seat, Ra 0.8); {p.crystal_bore_lead_chamfer:.1f} x 45 deg lead chamfer", leader=12)
    v.dim_dia((0, 0), p.dial_aperture_d / 2, 210, f"Ø{p.dial_aperture_d:.1f} through the {p.ledge_t:.1f} ledge",
              leader=16)
    # front slots
    sx, sy = p.gate_corners[0]
    cx, cy = p.front_slot_center(sx, sy)
    v.dim_h(0, cx, p.L / 2, 4, fmt(cx), outside=False)
    v.dim_v(0, cy, p.W / 2, 20, fmt(cy), text_side=1, outside=False)
    v.leader((cx + 0.55 * p.front_slot_len * math.cos(math.radians(p.front_slot_angle_deg)) * 0.5,
              cy + 0.55 * p.front_slot_len * math.sin(math.radians(p.front_slot_angle_deg)) * 0.5),
             f"2x slot {p.front_slot_len:.1f} x {p.front_slot_w:.1f}, {p.front_slot_depth:.1f} deep, long axis "
             f"{p.front_slot_angle_deg:.0f} deg from X\n(1 and 7 o'clock corners, 180 deg symmetric)", 12, 24)
    # crown pocket
    v.dim_v(-p.crown_pocket_w / 2, p.crown_pocket_w / 2, p.W / 2, 8, fmt(p.crown_pocket_w, 1), text_side=1, tpos=0.75)
    v.dim_h(p.x_pocket_floor, p.W / 2, -p.crown_pocket_w / 2, -16, fmt(p.crown_pocket_depth, 1), outside=True)
    v.leader((p.x_pocket_floor + 0.3, -p.crown_pocket_w / 2 + 0.3),
             f"R{p.crown_pocket_corner_r:.1f} (2x), pocket full height", 14, -12)
    # edge chamfer note
    v.leader((-p.W / 2 + cc + 5, p.L / 2), f"front outline chamfer {p.edge_chamfer_front:.1f} x 45 deg all round;\n"
             f"the 5 crown-pocket edges are hand-broken 0.2 x 45 deg", -10, 16)
    # section markers and orientation
    v.section_line((0, -p.L / 2), (0, p.L / 2), "A", (-1, 0), ext=24)
    v.section_line((-p.W / 2, 0), (p.W / 2, 0), "B", (0, 1), ext=32)
    v.label(0, -p.L / 2 - 3.5, "6 o'clock", size=FS_SMALL)
    v.label(0, p.L / 2 + 8, "12 o'clock (+Y)", size=FS_SMALL)
    v.label(p.W / 2 + 2, -p.L / 2 - 3.5, "3 o'clock (+X, crown)", size=FS_SMALL, ha="left")
    sh.general_notes([
        "Surface: sandblast Ra 1.6 after machining; crystal bore, thread, tube hole and keyway masked. "
        "Press-fit bores Ra 0.8.",
        f"Crystal: flat sapphire Ø{p.crystal_d:.1f} x {p.crystal_t:.1f} with a Hytrel I-ring "
        f"{p.iring_wall:.2f} x {p.iring_h:.2f} (bore = crystal + {p.crystal_bore_extra:.1f}); crystal stands "
        f"{p.crystal_proud:.1f} proud of the front face.",
        "Hidden lines (dashed): strap channels, movement bore, thread bore, keyway, tube hole and the "
        "back-face gate features; see sheets 2-4.",
        "Section A-A: sheet 3.  Section B-B: sheet 4.  Back view: sheet 2.",
    ], x=182, y=162, width_chars=58)
    sh.save(pdf, out_dir, png)


def sheet_case_back(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "CASE BODY  --  back view (caseback side, X mirrored)", "case_body",
               f"{case_body.MATERIAL['titanium']} (resin: {case_body.MATERIAL['resin']})", "2:1", date,
               file_stem="case_body_back")
    v = View(sh, (112, 118), 2.0)
    v.hlr(hlr_view(parts.case, (0, 0, -1), (-1, 0, 0)))
    m = -1.0   # model x -> view u (mirrored)
    v.center_mark(0, 0, p.cb_thread_minor / 2, ext=8)
    r_minor = p.cb_thread_minor / 2
    # thread callout (lower right)
    v.dim_dia((0, 0), r_minor, 305,
              f"M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6H, {p.cb_thread_len:.1f} deep, "
              f"{0.2:.1f} x 45 deg lead chamfer\n(shown as the Ø{p.cb_thread_minor:.2f} minor bore; thread-mill, "
              f"then Ø{p.mvt_bore_d:.1f} bore beyond)", leader=12)
    # strap openings: from the centre, right-hand side (model -X side)
    yo, yi = p.y_strap_open_outer, p.y_strap_open_inner
    v.dim_v(0, yi, p.W / 2, 10, fmt(yi, 1), text_side=1)
    v.dim_v(0, yo, p.W / 2, 18, fmt(yo, 1), text_side=1)
    v.dim_h(-p.strap_slot_w / 2, p.strap_slot_w / 2, p.L / 2, 10, f"{p.strap_slot_w:.1f} channel width (both ends)")
    v.leader((0, -(yi + yo) / 2), f"2x strap channel {p.strap_gap:.1f} gap at {p.strap_angle_deg:.0f} deg, "
             f"see A-A (sheet 3);\nchannel edges R{p.strap_fillet:.1f}, bar outer edge R{p.strap_bar_round:.1f} full width",
             28, -20)
    # keyway at 9 o'clock (model -X -> view +u)
    kw = p.ring_key_w + 0.1
    kd = p.ring_key_h + 0.1
    v.leader((m * (-(p.mvt_bore_d / 2 + kd / 2)), kw / 2 - 0.2),
             f"ring keyway {kw:.1f} wide x {kd:.1f} deep (radial, from Ø{p.mvt_bore_d:.1f}),\n"
             f"z {p.z_cb_inner:.1f} to {p.z_cb_inner + p.ring_key_len + 0.2:.1f} (9 o'clock)", 16, -14)
    # back grooves and chamfer notches (left = crown side in this view)
    sx, sy = p.gate_corners[0]
    gx = sx * (p.W / 2 - p.back_groove_inboard)
    gy0 = sy * (p.L / 2 - p.corner_chamfer - 0.5)
    gy1 = gy0 - sy * p.back_groove_len
    v.dim_h(m * gx, m * p.W / 2, gy1, -8, fmt(p.back_groove_inboard, 1), outside=True)
    v.dim_v(gy1, gy0, m * p.W / 2, -8, fmt(p.back_groove_len, 1), text_side=-1, outside=False)
    v.leader((m * gx, gy1 + 1.5), f"2x back groove {p.back_groove_w:.1f} wide x {p.back_groove_depth:.1f} deep\n"
             f"x {p.back_groove_len:.1f} long, R{p.back_groove_w / 2:.1f} ends\n(starts {0.5:.1f} past the corner chamfer)",
             -16, -6)
    mx, my = p.chamfer_mid(sx, sy)
    v.leader((m * mx, my), f"2x chamfer notch {p.chamfer_notch_w:.1f} along the chamfer\nx {p.chamfer_notch_depth:.1f} "
             f"deep x {p.chamfer_notch_h:.1f} high from the back face", -10, 12)
    v.leader((m * (p.W / 2 - 2), -9), f"back outline chamfer {p.edge_chamfer_back:.1f} x 45 deg\n(not on the two end "
             f"edges: R{p.strap_bar_round:.1f} bar round)", -14, -12)
    v.leader((m * p.x_pocket_floor, -p.crown_pocket_w / 2 + 1), "crown pocket, see sheet 1 / B-B", -10, 4)
    v.label(0, -p.L / 2 - 3.5, "6 o'clock", size=FS_SMALL)
    v.label(0, -p.L / 2 - 7, "viewed from the back: the crown side (3 o'clock) is on the LEFT", size=FS_SMALL)
    v.label(0, p.L / 2 + 8, "12 o'clock (+Y)", size=FS_SMALL)
    sh.general_notes([
        "Op 1 (back face up): face; outline with the four 4.0 corner chamfers; bore the thread minor "
        f"Ø{p.cb_thread_minor:.2f} x {p.cb_thread_len:.1f}; thread-mill M{p.cb_thread_major:.0f} x "
        f"{p.cb_thread_pitch:.1f} - 6H; movement bore Ø{p.mvt_bore_d:.1f} from z {p.z_cb_inner:.1f} to "
        f"{p.z_ledge_bottom:.1f}; keyway; grooves; notches; 0.3 chamfer.",
        "Strap channels: wire EDM preferred (1.8 slot), else 4/5-axis with a <= 1.5 end mill on a 40 deg fixture.",
        "Surface: sandblast Ra 1.6; thread and keyway masked.",
    ], x=182, y=152, width_chars=58)
    sh.save(pdf, out_dir, png)


def sheet_case_aa(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "CASE BODY  --  section A-A (YZ plane, x = 0)", "case_body",
               f"{case_body.MATERIAL['titanium']} (resin: {case_body.MATERIAL['resin']})",
               "3:1  (detail C 8:1)", date, file_stem="case_body_section_aa")
    faces = section_faces(parts.case, "YZ", 0.0)
    S = 3.0
    v = View(sh, (150, 142), S)          # (u, v) = (Y, Z); viewed from 3 o'clock, 12 o'clock to the right
    v.section(faces, hatch="////")
    v.centerline(0, -3, 0, p.H + 4)
    v.title("SECTION A-A  (3:1)  viewed from 3 o'clock, 12 o'clock to the right", x=150, y=199)
    v.dim_h(-p.L / 2, p.L / 2, 0, -16, fmt(p.L, 1))
    v.dim_v(0, p.H, -p.L / 2, -10, fmt(p.H, 1))
    v.dim_v(p.z_ledge_bottom, p.z_crystal_seat, -p.L / 2, -20, f"{p.ledge_t:.1f} ledge", outside=True, text_at="low")
    v.dim_h(-p.crystal_bore_d / 2, p.crystal_bore_d / 2, p.H, 8, f"Ø{p.crystal_bore_d:.2f} +0.05/0")
    v.dim_h(-p.dial_aperture_d / 2, p.dial_aperture_d / 2, p.H, 16, f"Ø{p.dial_aperture_d:.1f}")
    v.dim_h(-p.mvt_bore_d / 2, p.mvt_bore_d / 2, p.H, 24, f"Ø{p.mvt_bore_d:.1f} movement bore")
    v.dim_h(-p.cb_thread_minor / 2, p.cb_thread_minor / 2, 0, -8,
            f"Ø{p.cb_thread_minor:.2f} thread minor / M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6H",
            text_side=-1)
    u_r = p.L / 2
    v.dim_v(0, p.z_cb_inner, u_r, 10, fmt(p.z_cb_inner, 1), text_side=1, outside=True)
    v.dim_v(0, p.z_ledge_bottom, u_r, 22, fmt(p.z_ledge_bottom, 1), text_side=1)
    v.dim_v(0, p.z_crystal_seat, u_r, 34, fmt(p.z_crystal_seat, 1), text_side=1)
    v.dim_v(p.z_crystal_seat, p.H, u_r, 46, f"{p.crystal_engagement:.1f} crystal seat", text_side=1, outside=True)
    v.leader((-p.crystal_bore_d / 2, p.H - 0.1), f"{p.crystal_bore_lead_chamfer:.1f} x 45 deg lead chamfer", -14, 12)
    v.leader((p.cb_thread_minor / 2 + 0.1, 0.1), f"thread lead chamfer 0.2 x 45 deg;\nthread zone z 0 to "
             f"{p.cb_thread_len:.1f}", 18, -20)
    v.leader((p.crystal_bore_d / 2 + 0.6, p.H), f"front chamfer {p.edge_chamfer_front:.1f} x 45 deg", 14, 10)
    # detail circle marker on the main view
    cx_p, cy_p = v.P(p.L / 2 - 3.2, 3.6)
    det_r_main = 5.6 * S * 0.5
    sh.ax.add_patch(Circle((cx_p, cy_p), det_r_main, fill=False, lw=0.5, ec="black", ls=(0, (4, 1.5))))
    sh.text(cx_p + det_r_main + 1, cy_p + det_r_main - 1, "C", size=8, fontweight="bold")
    # ---------------- detail C: 8:1 of the 12 o'clock strap channel
    SD = 8.0
    R_DET = 34.0
    ccx, ccy = 62.0, 80.0
    cu, cv = p.L / 2 - 3.6, 3.7
    vd = View(sh, (ccx - SD * cu, ccy - SD * cv), SD, clip=(ccx, ccy, R_DET))
    vd.section(faces, hatch="////")
    sh.text(ccx, ccy + R_DET + 5, "DETAIL C  (8:1)  strap channel, 12 o'clock end", size=7.5, ha="center",
            fontweight="bold", va="bottom")
    a = p.strap_angle
    ya = p.y_strap_axis_back
    vd.centerline(ya - 1.0 * math.cos(a), -1.0 * math.sin(a), ya + 7.5 * math.cos(a), 7.5 * math.sin(a))
    vd.dim_h(p.y_strap_open_inner, p.y_strap_open_outer, 0, -9, fmt(p.strap_open_w_back, 2), text_side=-1)
    vd.dim_h(p.y_strap_open_outer, p.L / 2, 0, -9, fmt(p.strap_bar_back_w, 1), text_side=-1)
    vd.dim_v(0, p.z_strap_exit_low, p.L / 2, 10, fmt(p.z_strap_exit_low, 2), text_side=1)
    vd.dim_v(p.z_strap_exit_low, p.z_strap_exit_high, p.L / 2, 20, f"{p.strap_open_h_end:.2f} exit", text_side=1)
    vd.dim_v(p.z_strap_exit_high, p.H, p.L / 2, 10, f"{p.strap_front_wall:.2f} wall", text_side=1)
    g = p.strap_gap
    zg = 2.2
    axis_pt = (ya + zg / math.tan(a), zg)
    nrm = (-math.sin(a), math.cos(a))
    p1 = (axis_pt[0] + nrm[0] * g / 2, axis_pt[1] + nrm[1] * g / 2)
    p2 = (axis_pt[0] - nrm[0] * g / 2, axis_pt[1] - nrm[1] * g / 2)
    vd.dim_aligned(p2, p1, 12, f"{g:.1f} gap")
    vd.leader((ya + 3.2 / math.tan(a), 3.2), f"{p.strap_angle_deg:.0f} deg to the back face", -20, 8)
    vd.leader((p.y_strap_open_inner, 0.0), f"R{p.strap_fillet:.1f} (4x per end)", -8, -14)
    vd.leader((p.L / 2 - 0.3, 0.3), f"R{p.strap_bar_round:.1f} bar outer edge, full width", 10, -8)
    vd.leader((p.L / 2 - 0.8, (p.z_strap_exit_low + p.z_strap_exit_high) / 2), "strap <= 1.5 thick, 22 wide", 16, 14)
    sh.general_notes([
        "Section faces from the CadQuery solid (x = 0 plane); hatching = Ti Grade 2.",
        f"Crystal seat depth {p.crystal_engagement:.1f}, ledge {p.ledge_t:.1f}, movement bore from z "
        f"{p.z_cb_inner:.1f} to {p.z_ledge_bottom:.1f}, thread zone z 0 to {p.cb_thread_len:.1f}.",
        f"Strap channel both ends, mirror in Y: back-face opening y {p.y_strap_open_inner:.1f} to "
        f"{p.y_strap_open_outer:.1f} from the centre (sheet 2), end-face exit z {p.z_strap_exit_low:.2f} to "
        f"{p.z_strap_exit_high:.2f}, bar {p.strap_bar_back_w:.1f} wide on the back face and {p.strap_bar_end_h:.2f} "
        f"tall on the end face.",
        "Surface: sandblast Ra 1.6; crystal seat and thread Ra 0.8 (masked).",
    ], x=182, y=MARGIN + TB_H + 2 + 52, width_chars=58)
    sh.save(pdf, out_dir, png)


def sheet_case_bb(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "CASE BODY  --  section B-B (XZ plane, y = 0) with the crown stack", "case_body",
               f"{case_body.MATERIAL['titanium']} (resin: {case_body.MATERIAL['resin']})",
               "3:1  (detail D 8:1)", date, file_stem="case_body_section_bb")
    faces = section_faces(parts.case, "XZ", 0.0)
    stack = {k: section_faces(parts.bought[k], "XZ", 0.0) for k in ("tube", "crown", "movement")}
    S = 3.0
    v = View(sh, (150, 145), S)          # (u, v) = (X, Z); viewed from 6 o'clock, crown to the right
    v.section(faces, hatch="////")
    for k in ("movement", "tube", "crown"):
        v.section(stack[k], hatch=PART_HATCH[k], fc=PART_COLOURS[k], hc="black", alpha=0.55, zorder=2)
    v.centerline(0, -3, 0, p.H + 4)
    v.centerline(-p.W / 2 - 2, p.z_stem, p.x_crown_start + p.crown_len + 3, p.z_stem)
    v.title("SECTION B-B  (3:1)  viewed from 6 o'clock, crown (+X) to the right;  tube, crown and movement "
            "envelope in colour", x=150, y=199)
    v.dim_h(-p.W / 2, p.W / 2, 0, -14, fmt(p.W, 1))
    v.dim_v(0, p.H, -p.W / 2, -20, fmt(p.H, 1))
    v.dim_h(-p.crystal_bore_d / 2, p.crystal_bore_d / 2, p.H, 8, f"Ø{p.crystal_bore_d:.2f}")
    v.dim_h(-p.mvt_bore_d / 2, p.mvt_bore_d / 2, p.H, 16, f"Ø{p.mvt_bore_d:.1f}")
    v.dim_h(-p.cb_thread_minor / 2, p.cb_thread_minor / 2, 0, -6, f"Ø{p.cb_thread_minor:.2f} thread minor", text_side=-1)
    kd = p.ring_key_h + 0.1
    v.dim_h(-(p.mvt_bore_d / 2 + kd), -p.mvt_bore_d / 2, p.z_cb_inner + p.ring_key_len + 0.2, 6, f"{kd:.1f}",
            outside=True)
    v.dim_v(p.z_cb_inner, p.z_cb_inner + p.ring_key_len + 0.2, -p.W / 2, -10, f"{p.ring_key_len + 0.2:.1f} keyway",
            text_side=-1, outside=False)
    v.dim_h(p.x_pocket_floor, p.W / 2, p.H, 12, f"{p.crown_pocket_depth:.1f} pocket", outside=True)
    v.dim_h(p.crystal_bore_d / 2, p.x_pocket_floor, p.H, 20,
            f"{p.x_pocket_floor - p.crystal_bore_d / 2:.2f} wall (crystal bore to pocket floor)", outside=True)
    v.leader((-5.0, p.z_dial_seat), "GL32 movement envelope (purchased)", -10, 20)
    # stack-up fan on the right
    levels = [(0.0, "0  back face"), (p.z_cb_inner, f"{p.z_cb_inner:.2f}  caseback inner face / thread end"),
              (p.z_mvt_back, f"{p.z_mvt_back:.2f}  movement back"), (p.z_stem, f"{p.z_stem:.2f}*  stem / tube axis"),
              (p.z_dial_seat, f"{p.z_dial_seat:.2f}  dial seat"), (p.z_dial_face, f"{p.z_dial_face:.2f}  dial face"),
              (p.z_ledge_bottom, f"{p.z_ledge_bottom:.2f}  ledge underside"),
              (p.z_crystal_seat, f"{p.z_crystal_seat:.2f}  crystal seat"), (p.H, f"{p.H:.2f}  front face")]
    v.ordinate_fan(levels, p.x_crown_start + p.crown_len + 1.5, 238, 120, 178, side=1, size=FS_SMALL, tick=6)
    sh.text(238, 183, "stack-up (z from the back face)", size=FS_SMALL, fontweight="bold")
    # detail circle marker
    cx_p, cy_p = v.P(15.3, 3.0)
    det_r_main = 3.3 * S
    sh.ax.add_patch(Circle((cx_p, cy_p), det_r_main, fill=False, lw=0.5, ec="black", ls=(0, (4, 1.5))))
    sh.text(cx_p + det_r_main * 0.7, cy_p - det_r_main - 0.5, "D", size=8, fontweight="bold", va="top")
    # ---------------- detail D: 8:1 of the crown pocket / tube region
    SD = 8.0
    R_DET = 34.0
    ccx, ccy = 150.0, 76.0
    cu, cv = 15.4, 3.4
    vd = View(sh, (ccx - SD * cu, ccy - SD * cv), SD, clip=(ccx, ccy, R_DET))
    vd.section(faces, hatch="////")
    for k in ("movement", "tube", "crown"):
        vd.section(stack[k], hatch=PART_HATCH[k], fc=PART_COLOURS[k], hc="black", alpha=0.55, zorder=2)
    vd.centerline(12.0, p.z_stem, 19.0, p.z_stem)
    sh.text(ccx, ccy + R_DET + 4, "DETAIL D  (8:1)  crown pocket, tube hole and crown stack", size=7.5,
            ha="center", fontweight="bold", va="bottom")
    vd.dim_v(p.z_stem - p.tube_hole_d / 2, p.z_stem + p.tube_hole_d / 2, p.x_pocket_floor - 0.3, -20,
             f"Ø{p.tube_hole_d:.1f} H7 tube hole, Ra 0.8", text_side=-1)
    vd.dim_v(0, p.z_stem, p.x_pocket_floor - 0.3, -9, f"{p.z_stem:.2f}*", text_side=-1)
    vd.leader((p.mvt_bore_d / 2 + 0.35, p.z_stem - p.tube_hole_d / 2 - 0.09),
              f"{p.tube_to_thread_margin:.2f}* margin: tube-hole bottom (z {p.z_stem - p.tube_hole_d / 2:.2f})\n"
              f"to the thread zone (z {p.cb_thread_len:.1f})", -10, -14)
    vd.dim_h(p.mvt_bore_d / 2, p.x_pocket_floor, p.z_stem + p.tube_hole_d / 2 + 0.3, 8,
             f"{p.tube_engagement:.2f} tube engagement", text_side=1)
    vd.dim_h(p.x_pocket_floor, p.x_pocket_floor + p.tube_protrusion, p.z_stem + p.tube_od / 2, 8,
             f"{p.tube_protrusion:.1f} proud", text_side=1, outside=True)
    vd.dim_h(p.x_crown_start, p.x_crown_start + p.crown_len, p.z_stem + p.crown_d / 2, 12,
             f"{p.crown_len:.1f} crown", text_side=1)
    vd.dim_v(p.z_stem - p.crown_d / 2, p.z_stem + p.crown_d / 2, p.x_crown_start + p.crown_len, 6,
             f"Ø{p.crown_d:.1f} A/F", text_side=1)
    vd.dim_h(p.x_pocket_floor, p.W / 2, p.H - 0.6, -8, f"{p.crown_pocket_depth:.1f}", text_side=-1, outside=True,
             text_at="left")
    vd.leader((p.cb_thread_major / 2 + 0.85, 0.75), f"{p.x_pocket_floor - p.cb_thread_major / 2:.2f} wall at the "
              f"thread (Ø{p.cb_thread_major:.0f} major to pocket floor)", -8, -16)
    vd.leader((p.x_bore_wall + 0.6, p.z_stem + p.tube_od / 2), f"tube Ø{p.tube_od:.1f} x {p.tube_len:.2f}, press fit "
              f"(0.02-0.04\ninterference) + retaining compound", -10, 16)
    vd.leader((p.x_crown_start + 0.3, p.z_stem - 0.3), f"stem Ø0.9 (tap 10), crown face at x {p.x_crown_start:.1f}", 6, -16)
    sh.general_notes([
        "Section faces from the CadQuery solids (y = 0 plane): case body hatched, tube / crown / movement "
        "envelope colour-filled (purchased parts, see sheet 8).",
        f"Tube hole Ø{p.tube_hole_d:.1f} H7 drilled and reamed along X at z {p.z_stem:.2f}* through the pocket floor "
        f"into the bore; the z position follows stem_below_dial_seat = {p.stem_below_dial_seat:.2f}* under the dial seat.",
        f"Crown pocket {p.crown_pocket_w:.1f} wide x {p.crown_pocket_depth:.1f} deep, full height, R"
        f"{p.crown_pocket_corner_r:.1f} corners (sheet 1).",
        "Surface: sandblast Ra 1.6; tube hole Ra 0.8 (masked).",
    ], x=190, y=104, width_chars=54)
    sh.save(pdf, out_dir, png)


def sheet_caseback(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "CASEBACK  --  plan (outer face) and section C-C", "caseback",
               f"{caseback.MATERIAL['titanium']} (resin: {caseback.MATERIAL['resin']})",
               "3:1  (detail E 10:1)", date, file_stem="caseback")
    S = 3.0
    v = View(sh, (70, 120), S)           # plan viewed from outside (-Z), 12 o'clock up, X mirrored
    v.hlr(hlr_view(parts.back, (0, 0, -1), (-1, 0, 0)), hidden=True)
    v.center_mark(0, 0, p.cb_flange_d / 2, ext=6)
    v.title("PLAN  (3:1)  outer face, seen from behind the watch", x=70, y=190)
    v.dim_dia((0, 0), p.cb_flange_d / 2, 135, f"Ø{p.cb_flange_d:.1f} flange", leader=8)
    v.dim_dia((0, 0), p.cb_thread_major / 2, 305, f"M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6g boss, "
              f"{p.cb_thread_len:.1f} long\n(hidden line: Ø{p.cb_thread_major:.1f} major)", leader=14)
    pcd_r = p.cb_tool_pcd / 2
    sh.ax.add_patch(Circle(v.P(0, 0), pcd_r * S, fill=False, lw=LW_THIN, ec="#1f5fa8", ls=(0, (6, 1.5, 1.2, 1.5)), zorder=1))
    v.dim_dia((0, 0), pcd_r, 20, f"PCD Ø{p.cb_tool_pcd:.1f}", leader=22)
    v.leader((0, pcd_r + p.cb_tool_hole_d / 2), f"{p.cb_tool_holes}x Ø{p.cb_tool_hole_d:.1f} x {p.cb_tool_hole_depth:.1f} deep "
             f"(blind), equally spaced,\none at 12 o'clock; 2-pin case wrench", 12, 16)
    if p.cb_center_hole:
        v.leader((p.cb_center_hole_d / 2 * 0.7, -p.cb_center_hole_d / 2 * 0.7), f"centre dimple Ø{p.cb_center_hole_d:.1f} "
                 f"x {p.cb_center_hole_depth:.1f} (spot drill)", 26, -30)
    v.leader((-pcd_r * 0.55, -pcd_r * 0.35), f"engraving area inside the PCD, depth {p.cb_engrave_depth:.2f}; "
             f"no text modelled, no maker marks", 22, -40)
    v.section_line((0, -p.cb_flange_d / 2), (0, p.cb_flange_d / 2), "C", (1, 0), ext=8)
    v.label(0, -p.cb_flange_d / 2 - 4, "6 o'clock", size=FS_SMALL)
    # section C-C (YZ plane, x = 0, through the 12 and 6 o'clock holes): (u, v) = (Y, Z), 12 o'clock right
    faces = section_faces(parts.back, "YZ", 0.0)
    vs = View(sh, (215, 166), S)
    vs.section(faces, hatch="\\\\\\\\")
    vs.centerline(0, -3, 0, 3.5)
    vs.title("SECTION C-C  (3:1)  x = 0, through the 12 and 6 o'clock holes; assembly orientation (outer face down)",
             x=215, y=190, size=7)
    vs.dim_h(-p.cb_flange_d / 2, p.cb_flange_d / 2, -p.cb_flange_t, -9, f"Ø{p.cb_flange_d:.1f}", text_side=-1)
    vs.dim_h(-p.cb_thread_major / 2, p.cb_thread_major / 2, p.cb_thread_len, 8,
             f"Ø{p.cb_thread_major:.1f}  M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6g")
    vs.dim_v(-p.cb_flange_t, 0, -p.cb_flange_d / 2, -12, f"{p.cb_flange_t:.1f} flange", text_side=-1, outside=True, text_at="low")
    vs.dim_v(0, p.cb_thread_len, -p.cb_flange_d / 2, -20, f"{p.cb_thread_len:.1f} thread", text_side=-1, outside=True,
             text_at="low")
    vs.dim_v(-p.cb_flange_t, p.cb_thread_len, p.cb_flange_d / 2, 12, f"{p.cb_total_t:.1f}", text_side=1, outside=True)
    vs.leader((-6, 0.0), f"case back face z = 0 (flange {p.cb_flange_t:.1f} proud of the case)", 10, -22)
    # detail E: flange edge with the O-ring groove and a tool hole, 10:1
    SD = 10.0
    R_DET = 26.0
    ccx, ccy = 215.0, 106.0
    cu, cv = 12.6, 0.3
    vd = View(sh, (ccx - SD * cu, ccy - SD * cv), SD, clip=(ccx, ccy, R_DET))
    vd.section(faces, hatch="\\\\\\\\")
    sh.text(ccx, ccy + R_DET + 3.5, "DETAIL E  (10:1)  flange edge, O-ring groove, tool hole (12 o'clock)", size=7.5,
            ha="center", fontweight="bold", va="bottom")
    r_gi = p.oring_groove_mean_d / 2 - p.oring_groove_w / 2
    r_go = p.oring_groove_mean_d / 2 + p.oring_groove_w / 2
    vd.dim_h(r_gi, r_go, 0, 9, f"{p.oring_groove_w:.1f} groove", text_side=1, outside=True, text_at="left")
    vd.dim_v(-p.oring_groove_depth, 0, r_go + 0.05, 8, f"{p.oring_groove_depth:.2f} deep", text_side=1, outside=True)
    vd.leader(((r_gi + r_go) / 2, -p.oring_groove_depth), f"groove mean Ø{p.oring_groove_mean_d:.1f} "
              f"(ID {2 * r_gi:.1f} / OD {2 * r_go:.1f})", -10, -20)
    ph = p.cb_tool_pcd / 2
    vd.dim_h(ph - p.cb_tool_hole_d / 2, ph + p.cb_tool_hole_d / 2, -p.cb_flange_t, -8, f"Ø{p.cb_tool_hole_d:.1f}",
             text_side=-1, outside=True)
    vd.dim_v(-p.cb_flange_t, -p.cb_flange_t + p.cb_tool_hole_depth, ph - p.cb_tool_hole_d / 2 - 0.1, -10,
             f"{p.cb_tool_hole_depth:.1f}", text_side=-1, outside=True)
    vd.leader((p.cb_flange_d / 2 - 0.15, -p.cb_flange_t + 0.15), "0.3 x 45 deg chamfer", 6, -14)
    vd.leader((p.cb_thread_major / 2, p.cb_thread_len * 0.6), "thread 6g, light grease at assembly", -6, 6)
    oring_id = round(2 * r_gi + 0.1, 1)
    sh.general_notes([
        f"Thread M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6g, {p.cb_thread_len:.1f} long, mates the case "
        f"(M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6H, sheet 2). Boss Ø{p.cb_thread_major:.1f} shown at the "
        f"major diameter (cosmetic); inner face flat at z {p.z_cb_inner:.1f}.",
        f"Face-seal O-ring ID {oring_id:.1f} x CS {p.oring_cs:.2f} NBR 70 in the groove (mean Ø{p.oring_groove_mean_d:.1f} "
        f"x {p.oring_groove_w:.1f} x {p.oring_groove_depth:.2f}); alternative: flat gasket ID 28.0 x OD 29.5 x 0.5.",
        "Surface: outer face brushed or blasted; O-ring groove and thread Ra 0.8. Steel in titanium does not gall.",
        "Resin profile: thread modelled as a true helix M27.6 x 1.0 (sheet 9).",
    ], x=MARGIN + 2, y=60, width_chars=96)
    sh.save(pdf, out_dir, png)


def sheet_ring(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    g = parts.ring_geo
    sh = Sheet(n, N, "SPACER RING  --  plan (dial side) and sections D-D, E-E", "spacer_ring",
               f"{spacer_ring.MATERIAL['titanium']} (resin: {spacer_ring.MATERIAL['resin']})", "3:1", date,
               file_stem="spacer_ring")
    S = 3.0
    r_od = p.ring_od / 2
    v = View(sh, (72, 128), S)
    v.hlr(hlr_view(parts.ring, (0, 0, 1), (1, 0, 0)), hidden=True)
    v.center_mark(0, 0, r_od, ext=6)
    v.title("PLAN  (3:1)  seen from the dial side, +X (crown / stem slot) to the right", x=72, y=190)
    v.dim_dia((0, 0), r_od, 45, f"Ø{p.ring_od:.2f} -0/-0.05\n(slides in the Ø{p.mvt_bore_d:.1f} bore)", leader=8)
    v.dim_h(-p.ring_pocket_w / 2, p.ring_pocket_w / 2, -5.0, -36, f"{p.ring_pocket_w:.2f} across flats", text_side=-1)
    v.dim_v(-p.ring_pocket_l / 2, p.ring_pocket_l / 2, r_od, 10, f"{p.ring_pocket_l:.2f}* (Ø{p.ring_pocket_l:.2f} arc)",
            text_side=1)
    v.dim_v(-p.ring_key_w / 2, p.ring_key_w / 2, -(r_od + p.ring_key_h), -14, f"{p.ring_key_w:.1f} key", text_side=-1,
            outside=True)
    v.dim_h(-(r_od + p.ring_key_h), -r_od, p.ring_key_w / 2 + 0.1, 10, f"{p.ring_key_h:.1f}", outside=True)
    v.leader((p.ring_pocket_w / 2 * 0.98, 4.6), "movement pocket: GL32 outline = circle cut by two flats,\n"
             "open through (see D-D / note 2)", 26, 10)
    v.leader((r_od - 0.5, -p.ring_stem_slot_w / 2), f"stem slot {p.ring_stem_slot_w:.1f} wide (see D-D)", 14, -12)
    v.section_line((-r_od - p.ring_key_h, 0), (r_od, 0), "D", (0, 1), ext=8)
    v.section_line((0, -r_od), (0, r_od), "E", (-1, 0), ext=8)
    # sections: D-D = XZ plane y=0 (through key and slot), E-E = YZ plane x=0
    fD = section_faces(parts.ring, "XZ", 0.0)
    fE = section_faces(parts.ring, "YZ", 0.0)
    z0, z_top = g["z0"], g["z_top"]
    vs = View(sh, (215, 158 - S * z0), S)
    vs.section(fD, hatch="xx")
    vs.centerline(0, z0 - 2, 0, z_top + 2)
    vs.title("SECTION D-D  (3:1)  y = 0, through the key (-X) and the stem slot (+X)", x=215, y=186, size=7)
    vs.dim_h(-(r_od + p.ring_key_h), -r_od, z0, -8, f"{p.ring_key_h:.1f}", text_side=-1, outside=True, text_at="left")
    vs.dim_v(z0, z0 + p.ring_key_len, -(r_od + p.ring_key_h), -10, f"{p.ring_key_len:.1f} key", text_side=-1, outside=True)
    vs.dim_v(z0, z_top, r_od, 10, f"{z_top - z0:.2f}", text_side=1, outside=True)
    vs.dim_h(-r_od, -p.ring_pocket_w / 2, z_top, 8, f"{r_od - p.ring_pocket_w / 2:.2f} wall", text_side=1, outside=True)
    vs.leader((10.5, (z0 + z_top) / 2), f"stem slot {p.ring_stem_slot_w:.1f} wide: no wall at y = 0 on the +X side;\n"
              f"it runs through, z_stem + 1.2 = {p.z_stem + 1.2:.2f} is the top face", -10, -18)
    vs2 = View(sh, (215, 112 - S * z0), S)
    vs2.section(fE, hatch="xx")
    vs2.centerline(0, z0 - 2, 0, z_top + 2)
    vs2.title("SECTION E-E  (3:1)  x = 0", x=215, y=134, size=7)
    vs2.dim_h(-r_od, r_od, z0, -8, f"Ø{p.ring_od:.2f}", text_side=-1)
    vs2.dim_h(-p.ring_pocket_l / 2, p.ring_pocket_l / 2, z_top, 8, f"Ø{p.ring_pocket_l:.2f}* pocket", text_side=1)
    vs2.dim_v(z0, z_top, r_od, 10, f"{z_top - z0:.2f}", text_side=1, outside=True)
    vs2.leader((-r_od + 0.4, z0 + 0.05), f"ring bottom on the caseback inner face (z {z0:.1f});\n"
               f"top face z {z_top:.2f} = dial seat (as built, note 2)", 4, -14)
    lip_note = (f"2. AS-BUILT SHORT RING: SPEC 5 asks for a {p.ring_h:.1f} tall ring with a Ø{p.ring_dial_recess_d:.1f} dial "
                f"recess above the step, but with params.py v0.1 the lip would be {g['lip_wall']:.2f} mm "
                f"(ring_od {p.ring_od:.2f} vs recess {p.ring_dial_recess_d:.2f}), so spacer_ring.py builds the ring without "
                f"its upper section: height {z_top - z0:.2f} (z {z0:.1f} to {z_top:.2f}), pocket open through, dial rests on "
                f"the top face and is located by the movement bore; nothing clamps the ring against the ledge. "
                f"Fix in params.py (dial_d <= {p.ring_od - 2 * 0.5 - 0.2:.1f}, or a larger movement bore) before machining.")
    sh.general_notes([
        f"1. Machined from POM-C rod Ø30; general tolerance +-0.05. OD Ø{p.ring_od:.2f} is a sliding fit "
        f"({p.ring_od_clearance:.2f} diametral) in the Ø{p.mvt_bore_d:.1f} movement bore; key "
        f"{p.ring_key_w:.1f} x {p.ring_key_h:.1f} x {p.ring_key_len:.1f} at 9 o'clock enters the case keyway.",
        lip_note,
        f"3. Pocket {p.ring_pocket_w:.2f} x {p.ring_pocket_l:.2f}* = GL32 {p.mvt_w:.1f} x {p.mvt_l:.1f}* + "
        f"{p.ring_pocket_clearance:.2f} per side; the movement length is listed as 18.2 or 18.5 in different sources "
        f"(SPEC 11): an 18.2 movement has 0.3 play in Y, shim or tighten mvt_l after measuring.",
    ], x=MARGIN + 2, y=72, width_chars=96)
    sh.save(pdf, out_dir, png)


def sheet_dial(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "DIAL BLANK  --  plan (dial face) and section F-F", "dial_blank",
               f"{dial_blank.MATERIAL['titanium']} (resin: {dial_blank.MATERIAL['resin']})", "3:1  (detail G 10:1)",
               date, file_stem="dial_blank")
    S = 3.0
    v = View(sh, (85, 118), S)
    v.hlr(hlr_view(parts.dial, (0, 0, 1), (1, 0, 0)), hidden=False)
    r = p.dial_d / 2
    v.center_mark(0, 0, r, ext=6)
    v.title("PLAN  (3:1)  dial face, 12 o'clock up, +X (3 o'clock) to the right", x=85, y=190)
    v.dim_dia((0, 0), r, 140, f"Ø{p.dial_d:.1f} -0/-0.05", leader=10)
    v.dim_dia((0, 0), p.dial_center_hole_d / 2, 35, f"Ø{p.dial_center_hole_d:.1f} centre hole (hand pipes)", leader=30)
    feet = dial_blank.feet_xy(p)
    for k, ((ang, rad), (x, y)) in enumerate(zip(p.dial_feet, feet)):
        v.dim_dia((x, y), p.dial_foot_d / 2, 340 if y < 0 else 20,
                  f"Ø{p.dial_foot_d:.1f}* foot hole at {ang:.0f} deg* cw from 12, r {rad:.1f}*", leader=20)
        v.centerline(0, 0, x * 1.15, y * 1.15)
    v.dim_h(feet[0][0], 0, -r, -12, f"{abs(feet[0][0]):.2f}*", outside=True, text_at="left")
    v.dim_v(feet[0][1], feet[1][1], -r, -12, f"{abs(feet[1][1] - feet[0][1]):.2f}*", text_side=-1)
    v.section_line((-r, 0), (r, 0), "F", (0, 1), ext=24)
    v.leader((r * 0.7, -r * 0.7), "plain disc: no printing, indices or date window modelled;\n"
             "feet are Ø0.8 brass wire soldered to the back at these holes", 8, -18)
    # section F-F (XZ, y=0) at 3:1 and detail at 10:1
    faces = section_faces(parts.dial, "XZ", 0.0)
    vs = View(sh, (215, 150), S)
    vs.section(faces, hatch="||||")
    vs.centerline(0, p.z_dial_seat - 1.5, 0, p.z_dial_face + 1.5)
    vs.title("SECTION F-F  (3:1)  y = 0, shown at its assembly height", x=215, y=180, size=7)
    vs.dim_h(-r, r, p.z_dial_seat, -9, f"Ø{p.dial_d:.1f}", text_side=-1)
    vs.dim_v(p.z_dial_seat, p.z_dial_face, r, 10, f"{p.dial_t:.1f}", text_side=1, outside=True, text_at="low")
    vs.leader((0.5, p.z_dial_face), f"face z {p.z_dial_face:.2f}, seat z {p.z_dial_seat:.2f} (on the movement / ring top)",
              10, -18)
    SD = 10.0
    R_DET = 22.0
    ccx, ccy = 215.0, 108.0
    vd = View(sh, (ccx, ccy - SD * (p.z_dial_seat + p.dial_t / 2)), SD, clip=(ccx, ccy, R_DET))
    vd.section(faces, hatch="||||")
    vd.centerline(0, p.z_dial_seat - 1, 0, p.z_dial_face + 1)
    sh.text(ccx, ccy + R_DET + 3, "DETAIL G  (10:1)  centre hole", size=7.5, ha="center", fontweight="bold", va="bottom")
    vd.dim_h(-p.dial_center_hole_d / 2, p.dial_center_hole_d / 2, p.z_dial_face, 8, f"Ø{p.dial_center_hole_d:.1f}",
             text_side=1, outside=False)
    vd.dim_v(p.z_dial_seat, p.z_dial_face, p.dial_center_hole_d / 2 + 1.2, 8, f"{p.dial_t:.1f}", text_side=1, outside=True)
    sh.general_notes([
        f"Brass CZ108 half-hard sheet {p.dial_t:.1f}; blank >= {p.dial_d + 6:.0f} x {p.dial_d + 6:.0f}; Ø{p.dial_d:.1f} disc "
        f"(-0/-0.05), centre hole Ø{p.dial_center_hole_d:.1f}, {len(p.dial_feet)} feet holes Ø{p.dial_foot_d:.1f}*.",
        "Dial feet positions and diameter are UNVERIFIED placeholders for the GL32: measure on the movement (or its "
        "drawing) before soldering feet or cutting the dial.",
        f"The visible dial area is the Ø{p.dial_aperture_d:.1f} case aperture; the dial edge is hidden under the "
        f"{(p.dial_d - p.dial_aperture_d) / 2:.1f} mm ledge overlap. Printing is a separate operation.",
    ], x=MARGIN + 2, y=56, width_chars=96)
    sh.save(pdf, out_dir, png)


def sheet_assembly(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    sh = Sheet(n, N, "ASSEMBLY  --  section A-A (x = 0), stack-up and purchased parts", "assembly",
               "Ti Grade 2 case, 316L back, POM ring, brass dial + purchased parts", "3:1", date, file_stem="assembly")
    S = 3.0
    order = ["case_body", "caseback", "spacer_ring", "movement", "dial_blank", "hands_envelope", "iring", "crystal", "oring"]
    solids = parts.solids()
    v = View(sh, (140, 160), S)
    for name in order:
        faces = section_faces(solids[name], "YZ", 0.0)
        alpha = 0.35 if name in ("hands_envelope", "crystal") else 0.7
        v.section(faces, hatch=PART_HATCH[name], fc=PART_COLOURS[name], hc="black", alpha=alpha, outline=True)
    v.centerline(0, -3, 0, p.z_crystal_top + 3)
    sh.text(MARGIN + 2, 200, "ASSEMBLY SECTION A-A  (3:1)  x = 0, from 3 o'clock", size=7.5, fontweight="bold")
    v.dim_v(0, p.H, p.L / 2, 6, f"{p.H:.1f}", text_side=1)
    v.dim_v(p.z_cb_outer, p.z_crystal_top, p.L / 2, 14, f"{p.total_height:.1f} total", text_side=1)
    v.leader((p.crystal_d / 2 - 1.0, p.z_crystal_top), f"crystal {p.crystal_proud:.1f} proud of the front face", 14, 8)
    v.dim_v(p.z_dial_face, p.z_crystal_seat, -p.dial_aperture_d / 2 + 1.0, 0.0, f"{p.hands_clearance:.2f} hands clearance",
            text_side=-1)
    v.leader((-6.0, p.z_dial_face + p.hands_top_above_dial_face),
             f"hands envelope {p.hands_top_above_dial_face:.1f}* above the dial face", 12, 14)
    v.dim_h(-p.cb_flange_d / 2, p.cb_flange_d / 2, p.z_cb_outer, -8, f"Ø{p.cb_flange_d:.1f} caseback flange", text_side=-1)
    v.dim_h(-p.crystal_d / 2, p.crystal_d / 2, p.z_crystal_top, 8, f"Ø{p.crystal_d:.1f} x {p.crystal_t:.1f} crystal")
    v.leader((p.oring_groove_mean_d / 2, -p.oring_groove_depth / 2), f"O-ring CS {p.oring_cs:.2f} in the flange groove", 22, -6)
    v.leader((p.crystal_d / 2 + p.iring_wall / 2, p.z_crystal_seat + p.iring_h / 2), f"I-ring {p.iring_wall:.2f} x "
             f"{p.iring_h:.2f}, crushed {p.crystal_d + 2 * p.iring_wall - p.crystal_bore_d:.1f} on Ø", 24, 8)
    v.leader((-4.6, p.z_mvt_back + p.mvt_h / 2), f"GL32 envelope {p.mvt_w:.1f} x {p.mvt_l:.1f}* x {p.mvt_h:.2f}", -14, -22)
    v.leader((-p.ring_od / 2 + 0.6, (p.z_cb_inner + p.z_dial_seat) / 2), "spacer ring (as built, short)", -6, -30)
    v.leader((0.0, p.z_dial_seat + p.mvt_pipe_h - 0.1), f"centre pipe stub {p.mvt_pipe_h:.2f}* above the dial seat", -12, 16)
    # stack-up fan (short labels) on the left; the full descriptions are in the table below
    levels = [(p.z_cb_outer, f"{p.z_cb_outer:.1f}  caseback outer face"), (0.0, "0.0  case back face"),
              (p.z_cb_inner, f"{p.z_cb_inner:.1f}  caseback inner face"),
              (p.z_mvt_back, f"{p.z_mvt_back:.1f}  movement back"),
              (p.z_stem, f"{p.z_stem:.2f}*  stem axis"),
              (p.z_dial_seat, f"{p.z_dial_seat:.2f}  dial seat"),
              (p.z_dial_face, f"{p.z_dial_face:.2f}  dial face"),
              (p.z_ledge_bottom, f"{p.z_ledge_bottom:.1f}  ledge underside"),
              (p.z_crystal_seat, f"{p.z_crystal_seat:.1f}  crystal seat"),
              (p.H, f"{p.H:.1f}  front face"), (p.z_crystal_top, f"{p.z_crystal_top:.1f}  crystal top")]
    v.ordinate_fan(levels, -p.L / 2 - 0.2, 52, 128, 192, side=-1, size=FS_SMALL, tick=8)
    # colour key (right, below the section)
    lx, ly = 228, 140
    sh.text(lx, ly + 1.5, "COLOUR KEY", size=FS_SMALL + 0.5, fontweight="bold")
    for i, name in enumerate(order):
        y = ly - 3.2 - i * 3.5
        sh.ax.add_patch(Rectangle((lx, y - 1.2), 5, 2.6, fc=PART_COLOURS[name], ec="black", lw=0.3,
                                  hatch=PART_HATCH[name], alpha=0.8))
        sh.text(lx + 6.5, y + 1.2, PART_LABEL[name], size=FS_SMALL)
    # tables
    stack_rows = [
        [f"{p.z_cb_outer:.1f}", "caseback outer face"],
        ["0.0", "case back face; thread zone starts"],
        [f"{p.z_cb_inner:.1f}", "thread zone ends = caseback inner face"],
        [f"{p.z_mvt_back:.1f}", f"movement back ({p.mvt_axial_clearance:.1f} clearance)"],
        [f"{p.z_stem:.2f}*", f"stem axis ({p.stem_below_dial_seat:.2f}* below the dial seat)"],
        [f"{p.z_dial_seat:.2f}", "dial seat (movement top), ring top (as built)"],
        [f"{p.z_dial_face:.2f}", "dial face"],
        [f"{p.z_ledge_bottom:.1f}", "ledge underside (ring top per SPEC 5)"],
        [f"{p.z_crystal_seat:.1f}", f"crystal seat (ledge top); hands clearance {p.hands_clearance:.2f}"],
        [f"{p.H:.1f}", "front face"],
        [f"{p.z_crystal_top:.1f}", f"crystal top ({p.crystal_proud:.1f} proud)"],
    ]
    sh.footnote_needed = True
    y = sh.table(MARGIN + 2, 118, ["z mm", "what"], stack_rows, [14, 84], size=4.9,
                 title="STACK-UP  (z from the case back face, titanium defaults)")
    dens = {"case_body": p.density_case, "caseback": p.density_caseback, "spacer_ring": p.density_ring, "dial_blank": p.density_ring}
    mrows = []
    for name, mat in (("case_body", case_body.MATERIAL["titanium"]), ("caseback", caseback.MATERIAL["titanium"]),
                      ("spacer_ring", spacer_ring.MATERIAL["titanium"]), ("dial_blank", dial_blank.MATERIAL["titanium"])):
        vol = solids[name].Volume()
        mrows.append([PART_LABEL[name], mat, f"{vol:.0f}", f"{dens[name]:.2f}", f"{vol / 1000 * dens[name]:.1f}"])
    y = sh.table(MARGIN + 2, y - 5, ["made part", "material (titanium build)", "mm3", "g/cm3", "g"], mrows,
                 [22, 46, 12, 10, 8], size=4.9, title="MADE PARTS  (volumes from the CadQuery solids)")
    rows = [[r["item"], r["spec"]] for r in purchased.bom(p)]
    sh.table(120, 104, ["purchased part (qty 1 each)", "specification (purchased.bom, identical in both profiles)"], rows,
             [32, 134], size=4.7)
    sh.text(120, 107, "PURCHASED PARTS", size=FS_SMALL + 0.5, fontweight="bold")
    sh.notes(MARGIN + 2, y - 4, [
        "Assembly order: press the tube; ring into the case from the back (key at 9); movement with dial and hands into "
        "the ring, stem through the ring slot and tube; cut the stem, fit the crown; O-ring greased, caseback screwed "
        "in with the pin wrench; I-ring then crystal pressed from the front.",
        f"Total height {p.total_height:.1f} = caseback outer face (z {p.z_cb_outer:.1f}) to crystal top (z "
        f"{p.z_crystal_top:.1f}); the crystal stands {p.crystal_proud:.1f} proud, hands clearance {p.hands_clearance:.2f}.",
        "Resin fit-check print first (sheet 9); every value marked * is measured on the real movement before titanium is cut.",
    ], width_chars=66, size=FS_SMALL, title="NOTES")
    sh.save(pdf, out_dir, png)


def sheet_profiles(parts: Parts, n, N, date, out_dir, pdf, png):
    p = parts.p
    pr = params.get("resin")
    sh = Sheet(n, N, "RESIN vs TITANIUM  --  profile differences and print orientation", "all parts",
               "titanium build / resin fit-check print", "n/a", date, file_stem="profiles_and_printing")

    def both(attr, nd=2):
        return f"{getattr(p, attr):.{nd}f}", f"{getattr(pr, attr):.{nd}f}"

    rows = [
        ["purpose", "the real part: CNC Ti Grade 2 case, 316L back, POM ring, brass dial",
         "MSLA fit-check print of the same geometry before any metal is cut"],
        ["thread model", f"plain cosmetic bores: case bore Ø{p.cb_thread_minor:.2f} (minor), back boss Ø{p.cb_thread_major:.1f}; "
         f"machinist cuts M{p.cb_thread_major:.0f} x {p.cb_thread_pitch:.1f} - 6H / 6g",
         f"true helical thread M{pr.cb_thread_major:.1f} x {pr.cb_thread_pitch:.1f}, minor Ø{pr.cb_thread_minor:.2f}, "
         f"radial clearance {pr.cb_thread_clearance:.2f}; chased by hand, no tap exists"],
        ["general tolerance", "+-0.05", "+-0.15"],
        ["tube hole", f"Ø{p.tube_hole_d:.1f} H7 reamed", f"printed Ø{pr.tube_hole_d:.1f}, hand-reamed to 2.0 from the pocket side"],
        ["small-bore compensation (hole_comp)", f"{p.hole_comp:+.2f}", f"{pr.hole_comp:+.2f}"],
        ["sliding-fit extra (fit_extra)", f"{p.fit_extra:+.2f}", f"{pr.fit_extra:+.2f}"],
        ["crystal bore", *both("crystal_bore_d")],
        ["ring OD / dial recess", f"{p.ring_od:.2f} / {p.ring_dial_recess_d:.2f}", f"{pr.ring_od:.2f} / {pr.ring_dial_recess_d:.2f}"],
        ["ring lip (OD - recess)/2", f"{(p.ring_od - p.ring_dial_recess_d) / 2:.2f} -> short ring built",
         f"{(pr.ring_od - pr.ring_dial_recess_d) / 2:.2f} -> short ring built"],
        ["thread minor vs movement bore", f"{p.cb_thread_minor:.2f} vs {p.mvt_bore_d:.1f}", f"{pr.cb_thread_minor:.2f} vs {pr.mvt_bore_d:.1f}"],
        ["case / back / ring material", f"{case_body.MATERIAL['titanium']} / {caseback.MATERIAL['titanium']} / "
         f"{spacer_ring.MATERIAL['titanium']}", "tough ABS-like resin, layer 0.05 (all parts)"],
        ["density case / back / ring g/cm3", f"{p.density_case:.2f} / {p.density_caseback:.2f} / {p.density_ring:.2f}",
         f"{pr.density_case:.2f} / {pr.density_caseback:.2f} / {pr.density_ring:.2f}"],
        ["surface", "sandblast Ra 1.6; bores Ra 0.8; masks on thread, tube hole, crystal seat, keyway",
         "as printed; sand support marks on the back face / outer face only"],
    ]
    y = sh.table(MARGIN + 2, PAPER_H - MARGIN - 4, ["item", "titanium profile", "resin profile"], rows, [44, 118, 118],
                 size=5.4, title="PROFILE DIFFERENCES  (params.titanium() vs params.resin(); geometry otherwise identical)")
    orows = [
        ["case body", "back face toward the plate on supports, tilted 15-20 deg about the long (12-6) axis, crown flank high",
         "front face and crystal seat print up with no support marks; the tilt removes the flat first layer and drains the "
         "thread bore and tube hole; supports land on the back-face annulus, not on the thread teeth"],
        ["caseback", "outer face down on supports, tilted about 10 deg",
         "thread and O-ring groove print clean; support marks on the outer face are sanded; clear the six Ø1.5 holes with a drill"],
        ["spacer ring", "axis vertical, open (movement) side up, light supports under the top face",
         "the pocket prints as a cup; the thin wall stays round; never flat on the plate (first layers compress)"],
        ["dial blank", "face up, tilted 10 deg on supports", "or skip it: a paper disc does the same job for a fit check"],
    ]
    y = sh.table(MARGIN + 2, y - 5, ["part", "orientation", "why"], orows, [28, 110, 142], size=5.4,
                 title="PRINT ORIENTATION  (MSLA, tough resin, 0.05 layers; medium supports 0.3 tips)")
    sh.notes(MARGIN + 2, y - 4, [
        "Coupon first: print a 15 x 15 x 3 plate with Ø1.9 / 2.0 / 2.5 holes and a 1.8 x 8 slot, plus a Ø26.5 x 3 ring and a "
        "Ø26.3 x 3 disc on the same resin and exposure; holes under nominal mean the exposure over-cures: lower it or raise "
        "hole_comp / fit_extra in params.resin() and rebuild.",
        "No supports inside the movement bore, thread, crystal seat, strap channels or front slots. Two-stage IPA wash, blow "
        "the tube hole and channels out before curing; 5-10 min UV per side (tough resins go brittle when over-cured).",
        "After printing: ream the tube hole 1.9 -> 2.0 from the crown pocket; run the printed caseback in and out with wax; "
        "ring into the bore from the back (should slide, 0.3 diametral); movement + stem; caseback; buy the I-ring before "
        "the crystal and confirm it seats in the bore. Anything that binds is a parameter to revisit in params.py.",
    ], width_chars=150, size=5.6, title="PRINT NOTES")
    sh.save(pdf, out_dir, png)


SHEETS = [
    ("case_body_front", sheet_case_front),
    ("case_body_back", sheet_case_back),
    ("case_body_section_aa", sheet_case_aa),
    ("case_body_section_bb", sheet_case_bb),
    ("caseback", sheet_caseback),
    ("spacer_ring", sheet_ring),
    ("dial_blank", sheet_dial),
    ("assembly", sheet_assembly),
    ("profiles_and_printing", sheet_profiles),
]


# =========================================================================== main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="out", help="output root (out/drawings/ is written below it)")
    ap.add_argument("--date", default="2026-09-29", help="date printed in the title blocks")
    ap.add_argument("--png", metavar="DIR", default=None,
                    help="also write 150 dpi PNG previews of every sheet into DIR (review aid, not a deliverable)")
    ap.add_argument("--sheets", nargs="+", type=int, help="only these sheet numbers (1-based)")
    args = ap.parse_args(argv)
    out_root = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
    out_dir = os.path.join(out_root, "drawings")
    os.makedirs(out_dir, exist_ok=True)
    if args.png:
        os.makedirs(args.png, exist_ok=True)

    t0 = time.time()
    print("drawings: building the titanium solids")
    parts = Parts(params.get("titanium"))
    N = len(SHEETS)
    pdf_path = os.path.join(out_dir, "blueprints.pdf")
    with PdfPages(pdf_path) as pdf:
        d = pdf.infodict()
        d["Title"] = "Terra-style GL32 case prototype - blueprints"
        d["Subject"] = "machining / printing drawings generated from params.py"
        for i, (stem, fn) in enumerate(SHEETS, start=1):
            if args.sheets and i not in args.sheets:
                continue
            t1 = time.time()
            fn(parts, i, N, args.date, out_dir, pdf, args.png)
            print(f"  sheet {i}/{N} {stem:24s} {time.time() - t1:.1f} s")
    print(f"wrote {pdf_path} and {N} SVG sheets in {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
