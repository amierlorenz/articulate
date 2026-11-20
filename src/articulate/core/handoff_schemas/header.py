"""Common Header for hand-off files (§3.7)."""
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Header(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_: Literal[
        "encore.import",
        "encore.plan",
        "encore.state",
        "encore.library",
        "encore.attempt",
        "encore.flag",
    ] = Field(alias="schema", validation_alias=AliasChoices("schema", "schema_"), serialization_alias="schema")
    version: int
    id: str
    created_at: datetime = Field(
        validation_alias=AliasChoices("created_at", "createdAt"),
        serialization_alias="created_at",
    )
    producer: str
