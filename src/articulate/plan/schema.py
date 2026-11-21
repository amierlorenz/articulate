"""Phase 3 additive `encore.plan` v1 fields (§7.1 "Plan, additive fields").

The fields are additive (#18: no version bump), so they live on a subclass of the core `Plan` model until the core
schema module takes them in. The committed `schemas/encore.plan.v1.json` allows extra properties.
"""
from pydantic import BaseModel, ConfigDict, Field

from articulate.core.handoff_schemas import Plan


class PracticeTest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    examId: str
    itemCount: int                       # 15–20
    freeRecallMin: float = 0.5           # mix like past exams, never below half free recall


class ReviewQuiz(BaseModel):
    model_config = ConfigDict(extra="ignore")

    examId: str
    conceptIds: list[str]                # core concepts of the exam, short


class DailyPlan(Plan):
    practiceTest: PracticeTest | None = None
    reviewQuiz: ReviewQuiz | None = None
    reviewLimit: int | None = None       # backlog spread: at most this many due reviews today
    reviewPriority: list[str] = Field(default_factory=list)  # exam ids whose concepts jump the backlog
    newPaused: bool = False              # backlog doesn't fit the budget yet
    holdNewForExams: list[str] = Field(default_factory=list)  # last 2 days before an exam: no new for it
