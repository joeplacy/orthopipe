"""
eval/rx_dataset.py — labeled Rx parser evaluation cases.

Each case pairs a free-text prescription with the `Prescription` a correct parse
should yield. Cases are typed with real schema objects (not raw dicts) and cover:
single-side vs bilateral, every Mod type, Relief across landmarks, a
variable-stiffness fill, unit-conversion, and several designed to trip
`needs_manual`. `order_id` is set to the case_id (parse_prescription overwrites it
authoritatively, so it does not affect scoring).

Structured to ingest real de-identified Rx later — just append Case(...) rows.
"""
from __future__ import annotations
from dataclasses import dataclass

from schema import (Prescription, FootRx, Shell, Fill, ZoneFill,
                    MedialWedge, LateralWedge, HeelLift, Relief, MetPad)


@dataclass
class Case:
    case_id: str
    rx_text: str
    expected: Prescription
    note: str = ""


def _rx(order_id, left=None, right=None, shell=None, needs_manual=False, ambiguities=None):
    return Prescription(order_id=order_id, left=left, right=right,
                        shell=shell or Shell(), needs_manual=needs_manual,
                        ambiguities=ambiguities or [])


def get_cases() -> list[Case]:
    C = []

    C.append(Case("single_medial_left",
        "4 degree medial wedge, left foot only.",
        _rx("single_medial_left", left=FootRx(mods=[MedialWedge(degrees=4.0)])),
        "single-side, one mod"))

    C.append(Case("bilateral_medial",
        "Bilateral medial wedges, 5 degrees.",
        _rx("bilateral_medial",
            left=FootRx(mods=[MedialWedge(degrees=5.0)]),
            right=FootRx(mods=[MedialWedge(degrees=5.0)])),
        "bilateral -> both feet"))

    C.append(Case("lateral_wedge_right",
        "Lateral wedge 6 degrees on the right.",
        _rx("lateral_wedge_right", right=FootRx(mods=[LateralWedge(degrees=6.0)])),
        "lateral wedge single side"))

    C.append(Case("heel_lift_quarter_inch",
        "Quarter inch heel lift, right foot.",
        _rx("heel_lift_quarter_inch", right=FootRx(mods=[HeelLift(height_mm=6.35)])),
        "unit conversion 0.25in -> 6.35mm"))

    C.append(Case("heel_lift_cm",
        "1 cm heel lift on the left.",
        _rx("heel_lift_cm", left=FootRx(mods=[HeelLift(height_mm=10.0)])),
        "unit conversion cm -> mm"))

    C.append(Case("relief_base_5th",
        "Relief at the base of the 5th metatarsal, both feet, 2mm deep.",
        _rx("relief_base_5th",
            left=FootRx(mods=[Relief(landmark="base_5th_met", depth_mm=2.0)]),
            right=FootRx(mods=[Relief(landmark="base_5th_met", depth_mm=2.0)])),
        "relief with landmark base_5th_met"))

    C.append(Case("relief_met_heads",
        "Met head offload, left foot, 3mm deep, 15mm radius.",
        _rx("relief_met_heads",
            left=FootRx(mods=[Relief(landmark="met_heads", depth_mm=3.0, radius_mm=15.0)])),
        "relief met_heads with radius"))

    C.append(Case("relief_hallux",
        "Hallux relief right foot.",
        _rx("relief_hallux", right=FootRx(mods=[Relief(landmark="hallux")])),
        "relief hallux, defaults"))

    C.append(Case("met_pad_both",
        "Metatarsal pads bilaterally, 3mm.",
        _rx("met_pad_both",
            left=FootRx(mods=[MetPad(height_mm=3.0)]),
            right=FootRx(mods=[MetPad(height_mm=3.0)])),
        "met_pad bilateral"))

    C.append(Case("mixed_bilateral",
        "Medial wedges both feet 4 deg, with a quarter inch heel lift right only.",
        _rx("mixed_bilateral",
            left=FootRx(mods=[MedialWedge(degrees=4.0)]),
            right=FootRx(mods=[MedialWedge(degrees=4.0), HeelLift(height_mm=6.35)])),
        "bilateral + extra right mod"))

    C.append(Case("degrees_symbol",
        "3° lateral wedge, left.",
        _rx("degrees_symbol", left=FootRx(mods=[LateralWedge(degrees=3.0)])),
        "degree symbol parsing"))

    C.append(Case("fill_soft_heel",
        "Softer heel, firmer forefoot, left foot. 4 degree medial wedge left.",
        _rx("fill_soft_heel",
            left=FootRx(mods=[MedialWedge(degrees=4.0)],
                        fill=Fill(heel=ZoneFill(family="gyroid", density=0.3),
                                  forefoot=ZoneFill(family="gyroid", density=0.8)))),
        "variable-stiffness fill"))

    C.append(Case("shell_specified",
        "Full length shell, 4mm thick, medial wedge 4 deg both feet.",
        _rx("shell_specified",
            left=FootRx(mods=[MedialWedge(degrees=4.0)]),
            right=FootRx(mods=[MedialWedge(degrees=4.0)]),
            shell=Shell(thickness_mm=4.0, trim="full")),
        "explicit shell options"))

    # --- needs_manual cases ---
    C.append(Case("nm_out_of_range",
        "Medial wedge 12 degrees both feet.",
        _rx("nm_out_of_range", needs_manual=True,
            ambiguities=["medial_wedge 12deg exceeds safe range 0-8"]),
        "out-of-range wedge -> needs_manual"))

    C.append(Case("nm_ambiguous_lift",
        "Add a heel lift on the right.",
        _rx("nm_ambiguous_lift", needs_manual=True,
            ambiguities=["heel lift requested with no height"]),
        "heel lift no height -> needs_manual"))

    C.append(Case("nm_contradictory",
        "Medial and lateral wedge, same foot, both 5 degrees, left.",
        _rx("nm_contradictory", needs_manual=True,
            ambiguities=["contradictory medial and lateral wedge on same foot"]),
        "contradictory -> needs_manual"))

    return C
