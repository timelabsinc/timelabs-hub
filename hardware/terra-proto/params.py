"""
Single source of truth for the Terra-style GL32 case prototype.

Every part module, the verifier and the drawings import `Params` from here.
All dimensions are millimetres. Coordinate system (see SPEC.md):

    origin  = centre of the case, ON THE BACK FACE plane
    +X      = toward 3 o'clock (crown side)
    +Y      = toward 12 o'clock
    +Z      = toward the front (dial / crystal side);  back face z=0, front face z=H

Two build profiles share the same geometry and only change fits, thread pitch
and whether threads are modelled as true helices (resin print) or left as plain
cosmetic bores for the machinist (titanium).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict, replace


@dataclass
class Params:
    # ------------------------------------------------------------------ profile
    profile: str = "titanium"          # "titanium" | "resin"
    thread_model: str = "plain"        # "plain" (cosmetic bores) | "helix" (true thread geometry)

    # ------------------------------------------------------------- slab (case)
    W: float = 32.0                    # X, 9 -> 3 o'clock
    L: float = 44.0                    # Y, 6 -> 12 o'clock
    H: float = 8.3                     # Z, back face (0) -> front face (H)
    corner_chamfer: float = 4.0        # plan-view 45 deg chamfer leg on the 4 vertical corners
    edge_chamfer_front: float = 0.3    # perimeter chamfer on the front face outline (0.4 cannot run around the crown pocket)
    edge_chamfer_back: float = 0.3     # perimeter chamfer on the back face outline

    # ------------------------------------------- crystal: flat sapphire + I-ring
    crystal_d: float = 27.0            # generic flat sapphire, stock size (0.1 mm steps exist)
    crystal_t: float = 2.0
    crystal_engagement: float = 1.3    # how deep the crystal sits below the front face
    crystal_bore_extra: float = 0.7    # bore = crystal_d + 0.7 (Hytrel I-ring supplier rule)
    iring_wall: float = 0.45           # Hytrel I-ring for 1.5-2.0 mm glass
    iring_h: float = 0.9
    crystal_bore_lead_chamfer: float = 0.2

    # ------------------------------------------------------------ ledge / dial
    dial_aperture_d: float = 24.4      # what you see through the crystal; dial overlaps the ledge by 0.3/side
    ledge_t: float = 1.5               # solid ledge between crystal seat and dial cavity
    dial_d: float = 25.0               # must leave a >= 0.3 lip in the spacer ring: dial_d + 0.4 <= ring_od - 0.6
    dial_t: float = 0.4
    dial_center_hole_d: float = 1.6
    # dial feet: (angle in degrees clockwise from 12, radius). UNVERIFIED placeholder for GL32.
    dial_feet: tuple = ((195.0, 6.0), (345.0, 6.0))
    dial_foot_d: float = 0.8

    # ---------------------------------------------- movement: Miyota GL32 (6 3/4 x 8''')
    mvt_w: float = 15.3                # X (3-9), the stem axis
    mvt_l: float = 18.5                # Y (12-6); sources disagree 18.2 vs 18.5, pocket uses the larger
    mvt_h: float = 2.28                # casing height including battery (Miyota spec)
    mvt_pipe_h: float = 1.45           # tallest hand pipe above the dial seat (catalogue, low confidence)
    hands_top_above_dial_face: float = 1.8   # estimated top of seconds hand above dial face
    stem_below_dial_seat: float = 1.20 # UNVERIFIED - measure on the real movement before cutting Ti
    stem_thread: str = "tap 10 (0.90 mm)"
    hands_clearance_min: float = 2.2   # dial face -> crystal inner face, minimum accepted
    mvt_axial_clearance: float = 0.10  # between movement back and caseback inner face

    # ---------------------------------------------- movement bore & spacer ring
    mvt_bore_d: float = 26.5           # >= caseback thread minor dia so the ring inserts from the back
    ring_od_clearance: float = 0.20    # ring OD = mvt_bore_d - this  (diametral)
    ring_dial_clearance: float = 0.40  # dial recess = dial_d + this (diametral); >= 2x pocket clearance so the dial never binds
    ring_pocket_clearance: float = 0.15  # per side around the movement outline
    ring_stem_slot_w: float = 2.4      # slot in the ring wall for the stem, open toward the back
    ring_key_w: float = 2.0            # anti-rotation key on the ring OD (at 9 o'clock)
    ring_key_h: float = 0.6            # radial height of the key / depth of keyway in the case
    ring_key_len: float = 2.0          # axial length of the key from the ring bottom

    # ------------------------------------------------------- caseback (316L)
    cb_thread_major: float = 27.0      # M27 x 0.5 (titanium) / M27 x 1.0 (resin)
    cb_thread_pitch: float = 0.5
    cb_thread_len: float = 1.5         # thread engagement, inside the case: z 0 -> cb_thread_len
    cb_thread_clearance: float = 0.05  # radial clearance on the external thread (resin: 0.15)
    cb_flange_d: float = 29.4          # protruding flange behind the back face; must clear the strap opening (y_strap_open_inner)
    cb_flange_t: float = 0.8
    cb_tool_holes: int = 6
    cb_tool_hole_d: float = 1.5
    cb_tool_hole_depth: float = 0.9
    cb_tool_pcd: float = 20.0
    cb_center_hole: bool = True        # decorative centre dimple as on the reference
    cb_center_hole_d: float = 1.5
    cb_center_hole_depth: float = 0.4
    cb_engrave_depth: float = 0.05     # for the drawing note only, text not modelled
    cb_seal: str = "flat_gasket"       # "flat_gasket" (default) | "oring"
    gasket_recess_depth: float = 0.35  # annular recess in the flange's inner face (z=0 side), from the boss outward
    gasket_recess_od: float = 29.0     # leaves a 0.2 seating land at the flange rim
    gasket_t: float = 0.45             # flat gasket, ~22 % squeeze when the land seats
    gasket_id: float = 27.2
    gasket_od: float = 28.8
    oring_cs: float = 0.4              # "oring" option: face-seal O-ring on the flange underside
    oring_groove_w: float = 0.6
    oring_groove_depth: float = 0.28
    oring_groove_mean_d: float = 28.2  # centred in the 27.0-29.4 annulus

    # ----------------------------------------------------------------- crown
    crown_d: float = 4.0               # generic waterproof push-pull crown, tap 10
    crown_len: float = 2.5
    crown_octagonal: bool = True
    tube_od: float = 2.0               # case tube, press fit
    tube_hole_d: float = 2.0           # H7 in Ti; resin prints 1.9 and is reamed to 2.0
    tube_protrusion: float = 1.2       # beyond the crown-pocket floor
    crown_pocket_w: float = 7.0        # Y width of the recess in the 3 o'clock flank
    crown_pocket_depth: float = 0.8    # X depth into the flank
    crown_pocket_corner_r: float = 0.3 # small enough for the 0.3 front chamfer to run around the pocket

    # --------------------------------------------------------- strap channels
    strap_w: float = 22.0
    strap_slot_w: float = 22.4         # X width of the through channel
    strap_gap: float = 1.8             # channel thickness measured perpendicular to its axis
    strap_angle_deg: float = 40.0      # channel axis angle from the back-face plane
    strap_bar_back_w: float = 4.0      # bar width on the back face, measured from the end face (3.0 yielded at ~100 N pull)
    strap_fillet: float = 0.4          # round on the channel edges (strap contact)
    strap_bar_round: float = 0.8       # round on the bar's outer edge (back face / end face), full width

    # ------------------------------------------ "carabiner gate" decorative cuts
    gate_corners: tuple = ((1, 1), (-1, -1))   # (sign x, sign y): 1 o'clock and 7 o'clock corners
    front_slot: bool = True            # small angled slot on the front face near the corner
    front_slot_len: float = 5.0
    front_slot_w: float = 1.3
    front_slot_depth: float = 1.0      # blind; must stay < channel clearance if made through
    front_slot_angle_deg: float = 55.0 # from +X axis, mirrored per corner
    front_slot_inboard: float = 3.5    # from the chamfer face midpoint, along the inboard diagonal
    back_groove: bool = True           # groove along the 3/9 flank from the corner notch
    back_groove_w: float = 1.0
    back_groove_depth: float = 0.5
    back_groove_inboard: float = 1.2   # groove centreline from the flank
    back_groove_len: float = 10.0
    chamfer_notch: bool = True         # notch in the corner chamfer, blind from the back face
    chamfer_notch_w: float = 2.5
    chamfer_notch_depth: float = 0.8   # radial, into the chamfer face
    chamfer_notch_h: float = 0.8       # from the back face

    # ------------------------------------------------- resin-print compensation
    hole_comp: float = 0.0             # added to small bore diameters (resin: +0.10)
    fit_extra: float = 0.0             # added to sliding-fit clearances (resin: +0.10)

    # ------------------------------------------------------------- materials
    density_case: float = 4.51         # g/cm3 Ti Grade 2 (resin ~1.15)
    density_caseback: float = 8.00     # g/cm3 316L
    density_ring: float = 1.41         # g/cm3 POM
    density_dial: float = 8.50         # g/cm3 brass

    # ================================================================ derived
    # -- crystal / ledge
    @property
    def crystal_bore_d(self) -> float:
        return self.crystal_d + self.crystal_bore_extra + self.hole_comp * 0.5

    @property
    def z_front(self) -> float:
        return self.H

    @property
    def z_crystal_seat(self) -> float:        # ledge top = crystal underside
        return self.H - self.crystal_engagement

    @property
    def z_crystal_top(self) -> float:
        return self.z_crystal_seat + self.crystal_t

    @property
    def crystal_proud(self) -> float:
        return self.z_crystal_top - self.H

    @property
    def z_ledge_bottom(self) -> float:        # dial cavity ceiling
        return self.z_crystal_seat - self.ledge_t

    # -- stack from the back
    @property
    def z_cb_inner(self) -> float:            # caseback inner face
        return self.cb_thread_len

    @property
    def z_mvt_back(self) -> float:
        return self.z_cb_inner + self.mvt_axial_clearance

    @property
    def z_dial_seat(self) -> float:
        return self.z_mvt_back + self.mvt_h

    @property
    def z_dial_face(self) -> float:
        return self.z_dial_seat + self.dial_t

    @property
    def hands_clearance(self) -> float:       # dial face -> crystal underside
        return self.z_crystal_seat - self.z_dial_face

    @property
    def z_stem(self) -> float:
        return self.z_dial_seat - self.stem_below_dial_seat

    @property
    def tube_to_thread_margin(self) -> float: # bottom of tube hole above the thread zone
        return (self.z_stem - self.tube_hole_d / 2) - self.cb_thread_len

    # -- movement bore / ring
    @property
    def ring_od(self) -> float:
        return self.mvt_bore_d - self.ring_od_clearance - self.fit_extra

    @property
    def ring_h(self) -> float:                # sits on the caseback, touches the ledge underside
        return self.z_ledge_bottom - self.z_cb_inner

    @property
    def ring_step_h(self) -> float:           # ring bottom -> dial seat step
        return self.z_dial_seat - self.z_cb_inner

    @property
    def ring_pocket_w(self) -> float:
        return self.mvt_w + 2 * self.ring_pocket_clearance

    @property
    def ring_pocket_l(self) -> float:
        return self.mvt_l + 2 * self.ring_pocket_clearance

    @property
    def ring_dial_recess_d(self) -> float:    # dial sits inside the ring's upper lip
        return self.dial_d + self.ring_dial_clearance + self.fit_extra

    @property
    def ring_lip_wall(self) -> float:         # radial lip between dial recess and ring OD; must be >= 0.3
        return (self.ring_od - self.ring_dial_recess_d) / 2

    @property
    def dial_ledge_overlap(self) -> float:    # how far the dial hides under the ledge, per side
        return (self.dial_d - self.dial_aperture_d) / 2

    @property
    def flange_to_strap_margin(self) -> float:  # caseback flange edge to the strap opening on the back face
        return self.y_strap_open_inner - self.cb_flange_d / 2

    # -- caseback thread
    @property
    def cb_thread_minor(self) -> float:       # ISO internal minor dia D1 = D - 1.0825 P
        return self.cb_thread_major - 1.0825 * self.cb_thread_pitch

    @property
    def cb_total_t(self) -> float:
        return self.cb_thread_len + self.cb_flange_t

    @property
    def z_cb_outer(self) -> float:
        return -self.cb_flange_t

    @property
    def total_height(self) -> float:          # caseback outer face -> crystal top
        return self.z_crystal_top - self.z_cb_outer

    # -- crown
    @property
    def x_flank(self) -> float:
        return self.W / 2

    @property
    def x_pocket_floor(self) -> float:
        return self.W / 2 - self.crown_pocket_depth

    @property
    def x_bore_wall(self) -> float:
        return self.mvt_bore_d / 2

    @property
    def tube_len(self) -> float:              # bore wall -> pocket floor + protrusion
        return (self.x_pocket_floor - self.x_bore_wall) + self.tube_protrusion

    @property
    def tube_engagement(self) -> float:
        return self.x_pocket_floor - self.x_bore_wall

    @property
    def x_crown_start(self) -> float:
        return self.x_pocket_floor + 0.3

    # -- strap channel geometry (YZ section, mirrored for both ends)
    @property
    def strap_angle(self) -> float:
        return math.radians(self.strap_angle_deg)

    @property
    def strap_open_w_back(self) -> float:     # width of the opening on the back face
        return self.strap_gap / math.sin(self.strap_angle)

    @property
    def strap_open_h_end(self) -> float:      # height of the opening on the end face
        return self.strap_gap / math.cos(self.strap_angle)

    @property
    def y_end(self) -> float:
        return self.L / 2

    @property
    def y_strap_open_outer(self) -> float:    # back-face opening edge nearest the end face
        return self.y_end - self.strap_bar_back_w

    @property
    def y_strap_open_inner(self) -> float:
        return self.y_strap_open_outer - self.strap_open_w_back

    @property
    def y_strap_axis_back(self) -> float:     # where the channel axis crosses z=0
        return self.y_strap_open_outer - self.strap_open_w_back / 2

    @property
    def z_strap_axis_end(self) -> float:      # where the channel axis crosses the end face
        return (self.y_end - self.y_strap_axis_back) * math.tan(self.strap_angle)

    @property
    def z_strap_exit_low(self) -> float:
        return self.z_strap_axis_end - self.strap_open_h_end / 2

    @property
    def z_strap_exit_high(self) -> float:
        return self.z_strap_axis_end + self.strap_open_h_end / 2

    @property
    def strap_front_wall(self) -> float:      # solid above the end-face opening
        return self.H - self.z_strap_exit_high

    @property
    def strap_bar_end_h(self) -> float:       # bar height on the end face
        return self.z_strap_exit_low

    @property
    def end_face_flat_half_w(self) -> float:  # half width of the flat end face between chamfers
        return self.W / 2 - self.corner_chamfer

    # -- gate corner helpers
    def chamfer_mid(self, sx: int, sy: int) -> tuple:
        c = self.corner_chamfer / 2
        return (sx * (self.W / 2 - c), sy * (self.L / 2 - c))

    def front_slot_center(self, sx: int, sy: int) -> tuple:
        mx, my = self.chamfer_mid(sx, sy)
        d = self.front_slot_inboard / math.sqrt(2)
        return (mx - sx * d, my - sy * d)

    # ------------------------------------------------------------ utilities
    def as_dict(self) -> dict:
        d = asdict(self)
        for name in dir(type(self)):
            attr = getattr(type(self), name)
            if isinstance(attr, property):
                d[name] = getattr(self, name)
        return d

    def summary(self) -> str:
        keys = [
            "profile", "thread_model", "W", "L", "H", "total_height", "crystal_d", "crystal_bore_d",
            "z_crystal_seat", "crystal_proud", "z_ledge_bottom", "z_cb_inner", "z_mvt_back",
            "z_dial_seat", "z_dial_face", "hands_clearance", "z_stem", "tube_to_thread_margin",
            "mvt_bore_d", "ring_od", "ring_h", "ring_step_h", "cb_thread_major", "cb_thread_pitch",
            "cb_thread_minor", "cb_flange_d", "tube_len", "tube_engagement", "strap_open_w_back",
            "strap_open_h_end", "z_strap_exit_low", "z_strap_exit_high", "strap_front_wall",
            "strap_bar_end_h", "y_strap_open_inner", "y_strap_open_outer", "ring_dial_recess_d",
            "ring_lip_wall", "dial_ledge_overlap", "flange_to_strap_margin", "cb_seal",
        ]
        d = self.as_dict()
        return "\n".join(f"{k:>24s} = {d[k]:.3f}" if isinstance(d[k], float) else f"{k:>24s} = {d[k]}" for k in keys)


def titanium() -> Params:
    return Params(profile="titanium", thread_model="plain")


def resin() -> Params:
    """MSLA fit-check print: same geometry, coarse printable thread, opened-up fits."""
    return Params(
        profile="resin",
        thread_model="helix",
        cb_thread_major=27.6,      # keeps the minor dia >= movement bore at 1.0 pitch
        cb_thread_pitch=1.0,
        cb_thread_clearance=0.15,
        tube_hole_d=1.9,           # print undersize, ream to 2.0
        hole_comp=0.10,
        fit_extra=0.10,
        density_case=1.15,
        density_caseback=1.15,
        density_ring=1.15,
        density_dial=1.15,
    )


PROFILES = {"titanium": titanium, "resin": resin}


def get(profile: str = "titanium", **overrides) -> Params:
    p = PROFILES[profile]()
    return replace(p, **overrides) if overrides else p


if __name__ == "__main__":
    import sys
    prof = sys.argv[1] if len(sys.argv) > 1 else "titanium"
    print(get(prof).summary())
