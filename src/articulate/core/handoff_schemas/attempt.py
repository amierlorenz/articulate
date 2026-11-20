"""encore.attempt v1 schema models (§3.7)."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class Attempt(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    attemptId: UUID
    promptId: str
    conceptId: str
    answeredAt: datetime
    answerText: str
    grade: int
    msTaken: int
    sessionKind: Literal["plan", "postclass", "practice"]
    calibrated: bool


class AttemptBatch(BaseModel):
    """Container for schema generation of attempts."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    attempts: list[Attempt] = []
