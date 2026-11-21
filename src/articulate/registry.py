"""Job registry: the single shared file where slices register their steps (§3.12)."""

from articulate.core.jobs import Step

STEPS: list[Step] = [
    Step(
        "core.encore_read",
        "articulate.core.steps:read_encore",
        jobs={"nightly", "pass2", "watch"},
        order=10,
    ),
    Step("core.flags", "articulate.core.steps:apply_flags", jobs={"nightly"}, order=20),
    # Phase 1: 100–199
    Step("pocket.poll", "articulate.pocket.source:step", jobs={"watch", "nightly"}, order=100),
    Step("inbox.notes", "articulate.inbox:step", jobs={"watch", "nightly"}, order=120),
    Step("tasks.extract_send", "articulate.tasks:step", jobs={"watch", "nightly"}, order=130),
    # Phase 2: 200–399
    Step("intake.pick", "articulate.intake.canvas.pick:pick_step", jobs={"nightly", "pass2"}, order=200),
    Step(
        "intake.convert",
        "articulate.intake.convert:convert_step",
        jobs={"nightly", "pass2"},
        order=220,
    ),
    Step("intake.library", "articulate.intake.library:step", jobs={"nightly", "pass2"}, order=300),
    Step("plan.daily", "articulate.plan:step", jobs={"pass2"}, order=380),
    # Phase 3: 400–499
    # Phase 4: 500–599
    # Phase 5: 600–699
    Step("core.plan_hook", "articulate.core.steps:noop", jobs={"pass2"}, order=900),
]
