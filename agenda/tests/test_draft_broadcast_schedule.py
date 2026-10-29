from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from agenda.scheduling import draft

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 1, 5, 0, 5, tzinfo=OSLO)


def test_draft_runs_weekly_slots_before_jukebox_with_one_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, datetime]] = []
    monkeypatch.setattr(draft.timezone, "now", lambda: NOW)
    monkeypatch.setattr(
        draft,
        "fill_next_weeks_agenda",
        lambda *, now: calls.append(("weekly slots", now)),
    )
    monkeypatch.setattr(
        draft,
        "fill_agenda_with_jukebox",
        lambda *, start: calls.append(("jukebox", start)),
    )

    call_command("draft_broadcast_schedule")

    assert calls == [("weekly slots", NOW), ("jukebox", NOW)]


def test_draft_does_not_run_jukebox_when_weekly_slots_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jukebox_called = False

    def fail_weekly_slots(*, now: datetime) -> None:
        raise RuntimeError(f"weekly slots failed at {now}")

    def record_jukebox(*, start: datetime) -> None:
        nonlocal jukebox_called
        jukebox_called = True

    monkeypatch.setattr(draft, "fill_next_weeks_agenda", fail_weekly_slots)
    monkeypatch.setattr(draft, "fill_agenda_with_jukebox", record_jukebox)

    with pytest.raises(RuntimeError, match="weekly slots failed"):
        draft.draft_broadcast_schedule(now=NOW)

    assert not jukebox_called
