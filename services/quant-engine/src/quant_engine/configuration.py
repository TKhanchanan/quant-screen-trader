"""Validated configuration domain. No platform parsing or execution capabilities."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type Platform = Literal["capitalbear", "iqoption"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Bounds(Model):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside(self) -> Self:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Bounds overflow browser content")
        return self


class Slot(Model):
    id: int = Field(ge=1, le=9, strict=True)
    platform: Platform
    enabled: bool = Field(strict=True)
    assetName: str = Field(max_length=120)
    assetMode: Literal["AUTO", "MANUAL"] = "AUTO"
    displayName: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def asset_required(self) -> Self:
        if self.enabled and not self.assetName:
            raise ValueError("Enabled slots require an asset name")
        return self


class Slots(Model):
    platform: Platform
    slots: list[Slot] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def nine_slots(self) -> Self:
        if {s.id for s in self.slots} != set(range(1, 10)):
            raise ValueError("Exactly slots 1–9 required")
        if any(s.platform != self.platform for s in self.slots):
            raise ValueError("Wrong slot platform")
        return self


class CalibrationSlot(Model):
    id: int = Field(ge=1, le=9, strict=True)
    bounds: Bounds


class Calibration(Model):
    platform: Platform
    geometrySource: Literal["AUTO", "MANUAL"] = "MANUAL"
    name: str = Field(min_length=1, max_length=120)
    referenceBrowserWidth: int = Field(gt=0, le=32768, strict=True)
    referenceBrowserHeight: int = Field(gt=0, le=32768, strict=True)
    zoomFactor: float = Field(ge=0.25, le=5)
    slots: list[CalibrationSlot] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def nine_bounds(self) -> Self:
        if {s.id for s in self.slots} != set(range(1, 10)):
            raise ValueError("Exactly slots 1–9 required")
        return self


class GetRequest(Model):
    operation: Literal["get"]
    platform: Platform


class SlotsRequest(Slots):
    operation: Literal["slots"]


class SyncSlotsRequest(Slots):
    operation: Literal["syncAssets"]
    expectedSlots: list[Slot] = Field(min_length=9, max_length=9)
    expectedCalibrationVersion: str | None = Field(max_length=128)

    @model_validator(mode="after")
    def expected_valid(self) -> Self:
        Slots(platform=self.platform, slots=self.expectedSlots)
        return self


class PresetRequest(Slots):
    operation: Literal["savePreset"]
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)


class CalibrationRequest(Calibration):
    operation: Literal["saveCalibration"]
    id: UUID | None = None


class RecordRequest(Model):
    operation: Literal["deletePreset", "loadPreset", "deleteCalibration", "loadCalibration"]
    platform: Platform
    id: UUID


type ConfigurationRequest = (
    GetRequest
    | SlotsRequest
    | SyncSlotsRequest
    | PresetRequest
    | CalibrationRequest
    | RecordRequest
)
