"""How a text poster splits into slides and how long it takes to read.

The on-air graphics (src/core/posterSlides.ts) and the frontend keep the
same rule: a blank line in the text starts a new slide; a slide takes
SLIDE_SECONDS plus WORD_SECONDS for each word on it, and the heading is
read on the first slide. A poster is listed for at least long enough to
show every slide once; longer, and the slides go round again.
"""

import math
import re
from datetime import timedelta

SLIDE_SECONDS = 3
WORD_SECONDS = 0.4
SHORTEST = timedelta(seconds=5)
LONGEST = timedelta(minutes=10)
MOST_SLIDES = 6
SLIDE_LENGTH = 400

_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def slides(text: str) -> list[str]:
    found = [s.strip() for s in _BLANK_LINE.split(text.replace("\r\n", "\n"))]
    return [s for s in found if s] or [""]


def slide_seconds(heading: str, text: str) -> list[float]:
    return [
        SLIDE_SECONDS
        + WORD_SECONDS * (len(slide.split()) + (len(heading.split()) if i == 0 else 0))
        for i, slide in enumerate(slides(text))
    ]


def minimum_duration(heading: str, text: str) -> timedelta:
    """The shortest the poster may be listed for, in whole seconds."""
    # Summed in tenths, so float error can't round 6.0 s up to 7.
    tenths = sum(round(s * 10) for s in slide_seconds(heading, text))
    return max(SHORTEST, timedelta(seconds=math.ceil(tenths / 10)))
