"""Strict, versioned contracts shared by the UI, agents and CAD adapters."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    def __setattr__(self, name, value):
        # Pydantic after-model validators can raise after assignment has occurred.
        # Restore the prior record so a rejected edit cannot poison live state.
        previous = self.__dict__.copy()
        fields_set = self.__pydantic_fields_set__.copy()
        try:
            super().__setattr__(name, value)
        except Exception:
            object.__setattr__(self, "__dict__", previous)
            object.__setattr__(self, "__pydantic_fields_set__", fields_set)
            raise


class Element(Contract):
    id: str = Field(min_length=1)
    kind: Literal["equipment", "valve", "instrument", "pipe", "signal", "text", "connector", "junction", "annotation"]
    subclass: str = "unknown"
    tag: str | None = None
    text: str = ""
    bbox: list[float] = Field(min_length=4, max_length=4)
    points: list[list[float]] = Field(default_factory=list)
    rotation: float = 0
    parent_equipment: str | None = None
    connected_to: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    tile_id: str
    source_agent: str
    status: Literal["EXTRACTED", "UNREADABLE", "LOW_CONFIDENCE", "ACCEPTED", "REJECTED"] = "EXTRACTED"
    attributes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coordinates(self):
        x0, y0, x1, y1 = self.bbox
        if x0 < 0 or y0 < 0 or x1 < x0 or y1 < y0:
            raise ValueError("bbox must be nonnegative top-left xyxy")
        if any(len(p) != 2 for p in self.points):
            raise ValueError("geometry points must be 2D")
        if any(p[0] < 0 or p[1] < 0 for p in self.points):
            raise ValueError("geometry points must be nonnegative source pixels")
        if self.status == "UNREADABLE" and self.tag not in (None, "UNREADABLE"):
            raise ValueError("UNREADABLE elements cannot carry an inferred tag")
        return self


class Edge(Contract):
    id: str
    from_id: str
    to_id: str
    kind: Literal["pipe", "signal"] = "pipe"
    line_number: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class ReviewItem(Contract):
    id: str
    element_id: str | None = None
    reason: str
    status: Literal["NEEDS_HUMAN", "ACCEPTED", "REJECTED"] = "NEEDS_HUMAN"
    evidence: str | None = None


class PidGraph(Contract):
    schema_version: Literal["1.0"] = "1.0"
    page_id: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    elements: list[Element] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph_integrity(self):
        ids = {e.id for e in self.elements}
        if len(ids) != len(self.elements):
            raise ValueError("duplicate element IDs")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("duplicate edge IDs")
        for element in self.elements:
            if element.bbox[2] > self.width_px + 1 or element.bbox[3] > self.height_px + 1:
                raise ValueError(f"element {element.id} is outside the page")
            if any(i not in ids for i in element.connected_to):
                raise ValueError(f"element {element.id} references unknown connections")
            if element.parent_equipment is not None and element.parent_equipment not in ids:
                raise ValueError(f"element {element.id} references unknown parent equipment")
            if any(p[0] > self.width_px or p[1] > self.height_px for p in element.points):
                raise ValueError(f"element {element.id} has geometry outside the page")
        for edge in self.edges:
            if edge.from_id not in ids or edge.to_id not in ids:
                raise ValueError(f"edge {edge.id} references unknown nodes")
        return self


class Extraction(Contract):
    elements: list[Element] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class NoiseRegion(Contract):
    bbox: list[int] = Field(min_length=4, max_length=4)
    label: Literal["speckle", "stain", "shadow", "streak", "border", "bleed_through", "punch_hole", "markup", "watermark"]
    action: Literal["remove", "keep", "separate_layer"]
    reason: str


class DenoisePlan(Contract):
    regions: list[NoiseRegion] = Field(default_factory=list)
    deskew_degrees: float = Field(default=0, ge=-10, le=10)
    rationale: str


class LossCheck(Contract):
    content_lost: bool
    details: list[str] = Field(default_factory=list)


class LayoutPlan(Contract):
    page_class: Literal["P&ID", "legend/symbol sheet", "title block only", "other"]
    regions: dict[str, list[float]] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)


class QaResult(Contract):
    discrepancies: list[str] = Field(default_factory=list)
    needs_human: bool = True


class AgentMessage(Contract):
    job_id: str
    page_id: str
    agent: str
    task: str
    tile_id: str
    attempt: int = Field(ge=1)
    input_artifacts: list[str]
    output_artifact: str
    elapsed_seconds: float = Field(ge=0)
    status: Literal["done", "failed", "cached"]


class PipelineSettings(Contract):
    provider_mode: Literal["local", "cloud"] = "local"
    dpi: int = Field(default=300, ge=300, le=600)
    tile_size: int = Field(default=1536, ge=256, le=4096)
    overlap: float = Field(default=.2, ge=.05, le=.5)
    confidence_threshold: float = Field(default=.85, ge=0, le=1)
    keep_markups: bool = True
    max_calls: int = Field(default=200, ge=1)
    max_seconds: int = Field(default=600, ge=1)
    max_estimated_cost: float = Field(default=5, ge=0)
    estimated_cost_per_call: float = Field(default=.02, ge=0)
    cloud_consent: bool = False
    retries: int = Field(default=2, ge=0, le=3)


class PipelineResult(Contract):
    graph: PidGraph
    review: list[ReviewItem]
    artifact_dir: str
    status: str
