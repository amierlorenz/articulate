"""The inbox step's registry line (§3.12): after pocket.poll (100), before tasks (130)."""
import importlib

from articulate.registry import STEPS


def test_inbox_step_is_registered_for_watch_and_nightly_at_120():
    [s] = [s for s in STEPS if s.name == "inbox.notes"]
    assert s.order == 120
    assert s.jobs == {"watch", "nightly"}
    mod, func = s.entry.split(":")
    assert callable(getattr(importlib.import_module(mod), func))
