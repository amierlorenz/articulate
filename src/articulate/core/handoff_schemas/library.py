"""encore.library v1 schema models (§3.7)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class Library(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    exportedAt: datetime
    courses: list[dict[str, Any]] = []
    exams: list[dict[str, Any]] = []
    concepts: list[dict[str, Any]] = []
