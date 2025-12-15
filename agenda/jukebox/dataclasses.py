from dataclasses import dataclass
from typing import List

from fk.models import Video


@dataclass(frozen=True)
class WeighingResult:
    criteria_name: str
    score: float


@dataclass(frozen=True)
class ScoredCandidate:
    video: Video
    total: float
    weights: List[WeighingResult]
