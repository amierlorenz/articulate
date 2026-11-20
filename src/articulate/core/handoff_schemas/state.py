"""encore.state v1 schema models (§3.7)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class StatePrompt(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    promptId: str
    conceptId: str
    due: datetime | None = None
    ease: float | None = None
    interval: float | None = None
    lapses: int = 0
    introducedAt: datetime | None = None
    suspended: bool = False


class StateConcept(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    conceptId: str
    introducedAt: datetime | None = None
    suspended: bool = False


class State(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    exportedAt: datetime
    appliedBatches: list[str] = []
    planIdSeen: str | None = None
    concepts: list[StateConcept] = []
    prompts: list[StatePrompt] = []
