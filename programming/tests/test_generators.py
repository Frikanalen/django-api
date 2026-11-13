"""Recipes: what each kind may pick from, how it picks, and that
planning is deterministic."""

import datetime
from datetime import timedelta

import pytest

from fk.models import Category, Organization, Scheduleitem, Series, User
from programming.generators import candidates, plan
from programming.models import PlaylistSource, Recipe
from programming.selection import ScheduleContext
from programming.services import jukebox_recipe, series_recipe, uploads_recipe

from .conftest import NOW, OSLO, Window

pytestmark = pytest.mark.django_db


def ids(videos) -> list[int]:
    return [v.pk for v in videos]


@pytest.fixture
def uploads(organization) -> Recipe:
    return uploads_recipe(organization)


# -- Everything the owner uploads ---------------------------------------------


def test_uploads_fill_the_window_newest_first(uploads, make_video) -> None:
    old, middle, new = make_video(30), make_video(20), make_video(25)

    picks = plan(uploads, Window(), ScheduleContext(), NOW)

    # Newest (25) then middle (20); the oldest (30) no longer fits.
    assert ids(picks) == [new.pk, middle.pk]
    assert old not in picks


def test_a_video_too_long_for_the_window_is_skipped(uploads, make_video) -> None:
    fits = make_video(30)
    make_video(90)

    assert ids(plan(uploads, Window(), ScheduleContext(), NOW)) == [fits.pk]


def test_zero_length_and_unaccountable_videos_are_never_candidates(uploads, make_video) -> None:
    good = make_video(10)
    make_video(0)
    make_video(10, organization=Organization.objects.create(name="No editor"))

    assert ids(candidates(uploads, NOW)) == [good.pk]


def test_uploads_may_pick_a_pending_upload(uploads, make_video) -> None:
    pending = make_video(10, proper_import=False)

    assert ids(candidates(uploads, NOW)) == [pending.pk]


def test_another_organizations_recipe_draws_on_its_own_uploads(uploads, make_video) -> None:
    other = Organization.objects.create(
        name="Other", fkmember=True, editor=User.objects.create(email="other@example.test")
    )
    make_video(10)
    theirs = make_video(10, organization=other)

    assert ids(candidates(uploads_recipe(other), NOW)) == [theirs.pk]


def test_an_empty_pool_plans_nothing(uploads) -> None:
    assert plan(uploads, Window(), ScheduleContext(), NOW) == []


def test_the_unaired_lead_and_then_the_archive_rotates(uploads, make_video) -> None:
    often, once = make_video(20), make_video(20)
    fresh_old, fresh_new = make_video(20), make_video(20)
    for day, video in enumerate((often, often, once), start=1):
        Scheduleitem.objects.create(
            video=video, starttime=NOW - timedelta(days=day), duration=video.duration
        )

    picks = plan(uploads, Window(duration=timedelta(minutes=80)), ScheduleContext(), NOW)

    assert ids(picks) == [fresh_new.pk, fresh_old.pk, once.pk, often.pk]


def test_a_slot_does_not_repeat_week_after_week(uploads, make_video) -> None:
    a, b, c = make_video(30), make_video(30), make_video(30)
    context = ScheduleContext()
    half_hour = timedelta(minutes=30)

    weeks = [plan(uploads, Window(pk=n, duration=half_hour), context, NOW) for n in (1, 2, 3)]

    assert [ids(week) for week in weeks] == [[c.pk], [b.pk], [a.pk]]


def test_pinned_videos_lead_and_are_not_picked_again(uploads, make_video) -> None:
    old, new = make_video(20), make_video(20)

    picks = plan(uploads, Window(), ScheduleContext(), NOW, pinned=[old])

    assert ids(picks) == [old.pk, new.pk]


# -- A series -----------------------------------------------------------------


def test_a_series_recipe_picks_only_its_episodes(organization, make_video) -> None:
    series = Series.objects.create(organization=organization, name="Ukens program")
    make_video(10)
    first, second = make_video(10, series=series), make_video(10, series=series)

    recipe = series_recipe(series)

    assert ids(plan(recipe, Window(), ScheduleContext(), NOW)) == [second.pk, first.pk]
    assert series_recipe(series) == recipe  # the same choice is the same recipe


def test_a_deleted_series_falls_back_to_everything_uploaded(organization, make_video) -> None:
    series = Series.objects.create(organization=organization, name="Ukens program")
    recipe = series_recipe(series)
    video = make_video(10)

    series.delete()
    recipe = Recipe.objects.get(pk=recipe.pk)

    assert ids(candidates(recipe, NOW)) == [video.pk]
    assert recipe.describe() == "New uploads, then the archive"


# -- A playlist ---------------------------------------------------------------


def test_a_playlist_recipe_picks_from_its_videos(organization, make_video) -> None:
    listed, unlisted = make_video(10), make_video(10)
    recipe = Recipe.objects.create(organization=organization, kind=Recipe.Kind.PLAYLIST)
    PlaylistSource.objects.create(recipe=recipe, name="Favourites").videos.add(listed)

    assert ids(candidates(recipe, NOW)) == [listed.pk]
    assert unlisted.pk not in ids(candidates(recipe, NOW))


# -- The jukebox --------------------------------------------------------------


def test_the_jukebox_draws_on_the_filler_pool(organization, make_video) -> None:
    make_video(10)
    filler = make_video(10, is_filler=True)
    make_video(10, is_filler=True, has_tono_records=True)

    assert ids(candidates(jukebox_recipe(organization), NOW)) == [filler.pk]


def test_a_jukebox_theme_narrows_the_pool(organization, make_video) -> None:
    culture = Category.objects.create(id=1, name="Kultur")
    society = Category.objects.create(id=2, name="Samfunn")
    short = make_video(5, is_filler=True, categories=[culture])
    long = make_video(50, is_filler=True, categories=[culture])
    other = make_video(5, is_filler=True, categories=[society])

    def pool(**theme) -> set[int]:
        return set(ids(candidates(jukebox_recipe(organization, **theme), NOW)))

    assert pool() == {short.pk, long.pk, other.pk}
    assert pool(categories=[culture]) == {short.pk, long.pk}
    assert pool(longest=timedelta(minutes=10)) == {short.pk, other.pk}
    assert pool(shortest=timedelta(minutes=30)) == {long.pk}
    assert pool(categories=[culture], longest=timedelta(minutes=10)) == {short.pk}


def test_a_jukebox_theme_can_ask_for_new_or_old_material(organization, make_video) -> None:
    recent = make_video(5, is_filler=True, uploaded_time=NOW - timedelta(days=10))
    ancient = make_video(
        5,
        is_filler=True,
        created_time=datetime.datetime(2012, 1, 1, tzinfo=OSLO),  # no upload time recorded
    )

    def pool(**theme) -> list[int]:
        return ids(candidates(jukebox_recipe(organization, **theme), NOW))

    assert pool(newer_than_days=30) == [recent.pk]
    assert pool(older_than_days=365 * 5) == [ancient.pk]


def test_the_jukebox_draw_repeats_for_the_same_window(organization, make_video) -> None:
    for _ in range(12):
        make_video(5, is_filler=True)
    recipe = jukebox_recipe(organization)

    first = plan(recipe, Window(pk=7), ScheduleContext(), NOW)
    again = plan(recipe, Window(pk=7), ScheduleContext(), NOW)
    other = plan(recipe, Window(pk=8), ScheduleContext(), NOW)

    assert ids(first) == ids(again)
    assert ids(first) != ids(other)
    assert sum((v.duration for v in first), timedelta(0)) == timedelta(hours=1)
