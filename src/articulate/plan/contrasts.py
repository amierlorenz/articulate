"""Contrast prompts in the daily plan (§7.1 rule 7, Confusable pairs across decks #41).

A contrast is eligible once both members are introduced and each has a review at least a day after its
introduction, both are core or standard, and the prompt itself is new to Encore. At most 2 a day, inside the cap.
During a ramp, contrasts whose members are both in the exam's scope come first.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from articulate.core.handoff_schemas import PlanNew
from articulate.plan.ramp import Ramp, in_scope
from articulate.plan.simple import Candidate

CONTRASTS_PER_DAY = 2
CONTRAST_TIERS = ("core", "standard")


@dataclass(frozen=True)
class ContrastPrompt:
    prompt_id: str
    concept_ids: tuple[str, str]     # lower id first
    group_id: str | None = None


def _reviewed_after_intro(cid: str, introduced_at: dict[str, datetime], reviews: dict[str, list[datetime]]) -> bool:
    intro = introduced_at.get(cid)
    if intro is None:
        return False
    return any(t >= intro + timedelta(days=1) for t in reviews.get(cid, []))


def eligible(
    cp: ContrastPrompt,
    concepts: dict[str, Candidate],
    introduced_at: dict[str, datetime],
    reviews: dict[str, list[datetime]],
    known_prompts: set[str],
) -> bool:
    if cp.prompt_id in known_prompts:
        return False
    for cid in cp.concept_ids:
        c = concepts.get(cid)
        if c is None or c.tier not in CONTRAST_TIERS:
            return False
        if not _reviewed_after_intro(cid, introduced_at, reviews):
            return False
    return True


def pick(
    prompts: list[ContrastPrompt],
    concepts: dict[str, Candidate],
    introduced_at: dict[str, datetime],
    reviews: dict[str, list[datetime]],
    known_prompts: set[str],
    active_ramps: list[Ramp],
    limit: int = CONTRASTS_PER_DAY,
) -> list[PlanNew]:
    ok = [cp for cp in prompts if eligible(cp, concepts, introduced_at, reviews, known_prompts)]

    def lower_tier(cp: ContrastPrompt) -> str:
        tiers = [concepts[cid].tier for cid in cp.concept_ids]
        return "standard" if "standard" in tiers else "core"

    def key(cp: ContrastPrompt) -> tuple:
        members = [concepts[cid] for cid in cp.concept_ids]
        days = [r.day for r in active_ramps if not r.held and all(in_scope(r.exam, m) for m in members)]
        return (not days, min(days, default=0), lower_tier(cp) != "core", cp.prompt_id)

    return [
        PlanNew(conceptId=cp.concept_ids[0], promptIds=[cp.prompt_id], reason=lower_tier(cp))
        for cp in sorted(ok, key=key)[:limit]
    ]
