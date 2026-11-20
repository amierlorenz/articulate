"""encore.import v2 schema models (§3.7)."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from articulate.core.handoff_schemas.header import Header


class Citation(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    sourceFileId: str
    page: int | None = None
    startSec: float | None = None
    endSec: float | None = None
    quote: str
    status: Literal["verified", "unverified"]
    verifiedBy: Literal["text", "ocr", "vl"] | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    text: str
    provenance: Literal["cited", "gapFill"]
    citations: list[Citation] = []


class ConceptDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    explanationCitations: list[Citation] = []
    aliases: list[str] = []
    useWhen: list[Claim] = []
    conditions: list[Claim] = []
    workedExamples: list[Claim] = []


class Prompt(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    kind: str
    status: Literal["active", "retired"] = "active"
    content: dict[str, Any] = {}


class Concept(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    title: str
    explanation: str
    kind: Literal["definition", "fact", "method"]
    status: Literal["active", "retired"] = "active"
    tier: Literal["core", "standard", "background", "out"]
    details: ConceptDetails
    groupIds: list[str] = []
    tags: list[str] = []
    prompts: list[Prompt] = []


class Unit(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    order: int
    concepts: list[Concept] = []


class ContrastPair(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    conceptA: str
    conceptB: str
    decidingFeature: Claim


class ContrastGroup(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    question: str | None = None
    memberIds: list[str]
    contrasts: list[ContrastPair] = []


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    origin: Literal["canvas", "dropped", "pocket"]
    displayName: str
    pageCount: int | None = None
    contentHash: str
    canvasFileId: int | None = None


class Course(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    shortName: str | None = None
    coding: bool = False
    units: list[Unit] = []
    contrastGroups: list[ContrastGroup] = []
    sourceFiles: list[SourceFile] = []


class Exam(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    courseId: str
    name: str
    date: date
    kind: Literal["test", "final"]
    unitIds: list[str] = []


class ImportBatch(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    header: Header
    courses: list[Course]
    exams: list[Exam] = []
