"""How a text for the screen splits into pages and how long it takes to read.

Used for an organization's text poster and for the channel's bulletins
(:mod:`news`). The on-air graphics (src/core/posterSlides.ts) and the
frontend (src/components/screen-text/pages.ts) keep the same rule, and all
three run the cases in ``paging-cases.json``:

* A blank line in the text starts a new page.
* A block longer than PAGE_LENGTH characters is paged by itself: at
  sentence ends where it can, between words where a sentence is too long,
  and into pages of about the same length rather than full ones and a
  short last one.
* A page takes SLIDE_SECONDS plus WORD_SECONDS for each word on it, and the
  heading is read on the first page.

A poster is listed for at least long enough to show every page once;
longer, and the pages go round again.
"""

import math
import re
from datetime import timedelta

SLIDE_SECONDS = 3
WORD_SECONDS = 0.4
SHORTEST = timedelta(seconds=5)
LONGEST = timedelta(minutes=10)
MOST_SLIDES = 6
#: What fits on one page in the largest of the type sizes long text gets.
PAGE_LENGTH = 280
#: Room for MOST_SLIDES full pages and the blank lines between them.
TEXT_LENGTH = MOST_SLIDES * (PAGE_LENGTH + 2)

_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_SPACE = " \t\n "
_SENTENCE_END = ".!?…"
_CLOSERS = "\"'»”’)]"
_OPENERS = "\"'«“‘(["


def _starts_sentence(char: str) -> bool:
    return char in _OPENERS or (char != char.lower() and char == char.upper())


def _sentences(block: str) -> list[str]:
    """The block cut where a page may end: after a sentence, when the next
    one starts with a capital, and after a line break. Each piece keeps the
    space that follows it, so the pieces join back into the block."""
    pieces = []
    start = i = 0
    while i < len(block):
        char = block[i]
        end = -1
        if char == "\n":
            end = i + 1
        elif char in _SENTENCE_END:
            j = i + 1
            while j < len(block) and (block[j] in _SENTENCE_END or block[j] in _CLOSERS):
                j += 1
            if j < len(block) and block[j] in _SPACE:
                end = j
        if end < 0:
            i += 1
            continue
        while end < len(block) and block[end] in _SPACE:
            end += 1
        if char == "\n" or end == len(block) or _starts_sentence(block[end]):
            pieces.append(block[start:end])
            start = end
        i = end
    if start < len(block):
        pieces.append(block[start:])
    return pieces


def _words(piece: str, limit: int) -> list[str]:
    """A sentence too long for one page, cut between words (and a word too
    long for a page, cut where the page ends)."""
    words = []
    for word in re.split(f"[{_SPACE}]+", piece.strip(_SPACE)):
        while len(word) > limit:
            words.append(word[:limit])
            word = word[limit:]
        if word:
            words.append(word)
    return [word + " " for word in words]


def _fill(pieces: list[str], limit: int) -> list[str]:
    pages = []
    current = ""
    for sentence in pieces:
        fits = len(sentence.strip(_SPACE)) <= limit
        for piece in [sentence] if fits else _words(sentence, limit):
            if len((current + piece).strip(_SPACE)) <= limit:
                current += piece
                continue
            if current.strip(_SPACE):
                pages.append(current.strip(_SPACE))
            current = piece
    if current.strip(_SPACE):
        pages.append(current.strip(_SPACE))
    return pages


def _page(block: str) -> list[str]:
    if len(block) <= PAGE_LENGTH:
        return [block]
    pieces = _sentences(block)
    count = len(_fill(pieces, PAGE_LENGTH))
    # The same number of pages, as even as they come: the smallest page
    # size that still gives that number.
    for limit in range(math.ceil(len(block) / count), PAGE_LENGTH):
        pages = _fill(pieces, limit)
        if len(pages) <= count:
            return pages
    return _fill(pieces, PAGE_LENGTH)


def slides(text: str) -> list[str]:
    blocks = [s.strip(_SPACE) for s in _BLANK_LINE.split(text.replace("\r\n", "\n"))]
    found = [page for block in blocks if block for page in _page(block)]
    return found or [""]


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
