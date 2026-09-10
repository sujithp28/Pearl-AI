"""
The plan-preview timeline stages, shared by both protocol adapters.

The stage names are a client-facing contract: the VS Code extension
keys its plan-preview timeline off them today, and the web plan preview
will key off the same names. The event kinds behind them decide the
wording each stage gets, so a wrong mapping shows the user "still
thinking" while Pearl is waiting for their approval.
"""
from __future__ import annotations

from src.personality import EventKind
from src.personality.timeline import TIMELINE_EVENT_KINDS


class TestTimelineEventKinds:
    def test_stage_names_match_the_extension_timeline(self):
        assert set(TIMELINE_EVENT_KINDS) == {
            "planning",
            "plan_ready",
            "waiting_approval",
            "running_tool",
            "completed",
        }

    def test_each_stage_maps_to_the_event_kind_it_describes(self):
        """
        Pinned individually rather than as a bulk comparison, because
        the pair most easily got wrong is planning against plan_ready:
        one means "still thinking", the other means "here is the plan,
        review it". Swapping them type-checks and passes any test that
        only counts entries.
        """
        assert TIMELINE_EVENT_KINDS["planning"] is EventKind.PLANNING
        assert TIMELINE_EVENT_KINDS["plan_ready"] is EventKind.PLAN_READY
        assert TIMELINE_EVENT_KINDS["waiting_approval"] is EventKind.APPROVAL
        assert TIMELINE_EVENT_KINDS["running_tool"] is EventKind.EXECUTING
        assert TIMELINE_EVENT_KINDS["completed"] is EventKind.COMPLETED

    def test_every_value_is_an_event_kind(self):
        assert all(
            isinstance(kind, EventKind) for kind in TIMELINE_EVENT_KINDS.values()
        )
