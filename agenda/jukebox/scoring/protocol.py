import datetime
from typing import Protocol, List

from agenda.jukebox.dataclasses import WeighingResult
from fk.models import Video, Scheduleitem


class VideoScorer(Protocol):
    def score(
        self,
        video: Video,
        scheduled_items: List[Scheduleitem],
        now: datetime.datetime,
    ) -> WeighingResult: ...
