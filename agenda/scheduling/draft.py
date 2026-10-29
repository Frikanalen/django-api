"""The complete nightly broadcast-schedule draft, in dependency order."""

from datetime import datetime

from django.utils import timezone

from agenda.scheduling.jukebox import fill_agenda_with_jukebox
from agenda.scheduling.weekly_slots import fill_next_weeks_agenda


def draft_broadcast_schedule(now: datetime | None = None) -> None:
    """Place weekly slots, then fill only the airtime they leave behind.

    Both stages receive the same instant so a run at the Monday boundary
    cannot calculate two different scheduling horizons. If weekly-slot
    placement fails, the exception deliberately prevents the jukebox stage
    from running against an incomplete draft.
    """
    now = now or timezone.now()
    fill_next_weeks_agenda(now=now)
    fill_agenda_with_jukebox(start=now)
