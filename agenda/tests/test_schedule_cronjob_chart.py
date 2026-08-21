from pathlib import Path

CHART_TEMPLATES = Path(__file__).parents[2] / "chart" / "templates"


def test_chart_has_one_ordered_oslo_schedule_draft_cronjob() -> None:
    cronjobs = sorted(CHART_TEMPLATES.glob("cronjob-*.yaml"))

    assert [path.name for path in cronjobs] == ["cronjob-draft-broadcast-schedule.yaml"]
    manifest = cronjobs[0].read_text()
    assert "name: draft-broadcast-schedule" in manifest
    assert 'schedule: "5 0 * * *"' in manifest
    assert "timeZone: Europe/Oslo" in manifest
    assert "concurrencyPolicy: Forbid" in manifest
    assert "- draft_broadcast_schedule" in manifest
    assert "fill_next_weeks_agenda" not in manifest
    assert "fill_agenda_with_jukebox" not in manifest
