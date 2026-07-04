"""
Prescription schema: the contract between the LLM parser and the geometry engine.
The LLM's ONLY job is to emit valid instances of this. Geometry code never sees free text.
"""
from typing import Literal, Optional, List
from pydantic import BaseModel, Field

Landmark = Literal["base_5th_met", "met_heads", "heel_center", "arch_apex", "hallux"]


class MedialWedge(BaseModel):
    type: Literal["medial_wedge"] = "medial_wedge"
    degrees: float = Field(4.0, ge=0, le=8)


class LateralWedge(BaseModel):
    type: Literal["lateral_wedge"] = "lateral_wedge"
    degrees: float = Field(4.0, ge=0, le=8)


class HeelLift(BaseModel):
    type: Literal["heel_lift"] = "heel_lift"
    height_mm: float = Field(..., ge=0, le=15)


class Relief(BaseModel):
    type: Literal["relief"] = "relief"
    landmark: Landmark
    depth_mm: float = Field(2.0, ge=0.5, le=4)
    radius_mm: float = Field(12.0, ge=5, le=25)


class MetPad(BaseModel):
    type: Literal["met_pad"] = "met_pad"
    height_mm: float = Field(3.0, ge=1, le=6)


Mod = MedialWedge | LateralWedge | HeelLift | Relief | MetPad


class FootRx(BaseModel):
    mods: List[Mod] = []


class Shell(BaseModel):
    thickness_mm: float = Field(3.5, ge=2, le=6)
    trim: Literal["full", "3quarter", "sulcus"] = "3quarter"
    heel_cup_depth_mm: float = Field(10.0, ge=0, le=18)


class Prescription(BaseModel):
    order_id: str
    left: Optional[FootRx] = None
    right: Optional[FootRx] = None
    shell: Shell = Shell()
    needs_manual: bool = False
    ambiguities: List[str] = []
