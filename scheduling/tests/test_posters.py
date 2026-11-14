from datetime import timedelta

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
