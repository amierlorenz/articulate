"""encore.plan v1 schema models (§3.7)."""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class PlanNew(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    conceptId: str
    promptIds: list[str] = []
    reason: Literal["core", "standard", "background", "correction", "postclassSkipped", "reading"]


class ExamFocus(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    examId: str
    rampDay: int


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    kind: Literal["daily", "postclass"]
    date: date
    validUntil: datetime
    reviewScope: list[str]
    new: list[PlanNew]
    examFocus: list[ExamFocus] = []
    budgetMinutes: dict[str, Any]
    kindsMix: dict[str, Any]
    readingShare: float = 0.0
    intervalCaps: list[dict[str, Any]] = []
    importBatch: str | None = None
    macPractice: list[str] = []
