import json
from datetime import timedelta
from pathlib import Path

from scheduling import posters


def test_a_blank_line_starts_a_slide_and_a_newline_does_not() -> None:
    assert posters.slides("a\nb\n\n \nc\n\n") == ["a\nb", "c"]
    assert posters.slides("a\r\n\r\nb") == ["a", "b"]
    assert posters.slides("  ") == [""]


def test_reading_time_matches_the_graphics() -> None:
    # The same cases as on-air-graphics src/core/posterSlides.test.ts.
    assert posters.slide_seconds("To ord", "en to tre\n\nfire") == [5.0, 3.4]
    assert posters.minimum_duration("To ord", "en to tre\n\nfire") == timedelta(seconds=9)
    assert posters.minimum_duration("Hei", "") == timedelta(seconds=5)


def test_a_whole_number_of_seconds_is_not_rounded_up() -> None:
    # 3 s + 15 words: 0.4 * 15 is 6.000000000000001 in floating point.
    assert posters.minimum_duration("", " ".join(["ord"] * 15)) == timedelta(seconds=9)


def test_paging_matches_the_graphics_and_the_frontend() -> None:
    # The same file is in on-air-graphics (src/core/paging-cases.json) and
    # the frontend (src/components/screen-text/paging-cases.json).
    cases = json.loads((Path(__file__).parent / "paging-cases.json").read_text())
    assert len(cases) >= 10
    for case in cases:
        assert posters.slides(case["text"]) == case["pages"], case["name"]
        assert all(len(page) <= posters.PAGE_LENGTH for page in case["pages"])
        minimum = posters.minimum_duration(case["heading"], case["text"])
        assert minimum == timedelta(seconds=case["minimumSeconds"]), case["name"]


def test_a_long_block_is_paged_at_sentence_ends_into_even_pages() -> None:
    sentence = "Dette er en setning som er akkurat passe lang til å fylle litt av en side. "
    pages = posters.slides(sentence * 8)
    assert len(pages) == 3
    assert all(page.endswith("side.") for page in pages)
    # Not two full pages and a short one.
    assert max(map(len, pages)) - min(map(len, pages)) <= len(sentence)


def test_a_date_or_an_abbreviation_does_not_end_a_page() -> None:
    text = (
        "Møtet er tirsdag 17. november kl. 18.00 i Folkets hus, og alle er velkomne. " * 5
    ).strip()
    for page in posters.slides(text):
        assert not page.endswith(("17.", "kl."))
