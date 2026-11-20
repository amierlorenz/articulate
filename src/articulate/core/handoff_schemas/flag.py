"""encore.flag v1 schema models (§3.7)."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class Flag(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    flagId: UUID
    promptId: str
    conceptId: str
    reason: Literal["wrong", "unclear", "duplicate", "other"]
    note: str = ""
    suggestedFix: str | None = None
    flaggedAt: datetime
