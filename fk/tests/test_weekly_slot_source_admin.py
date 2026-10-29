"""
The source admin's `eligible_videos` line, which is the only place the
two silent filters in videos_queryset() are ever spelt out: a failed
import and an organization with no responsible editor both otherwise
produce a source that simply never airs anything.
"""

import pytest
from django.contrib import admin

from fk.admin import WeeklySlotSourceAdmin
from fk.models import Organization, SlotSourceType, User, WeeklySlotSource

from .test_weekly_slot_source import make_video, organization  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def source_admin() -> WeeklySlotSourceAdmin:
    return WeeklySlotSourceAdmin(WeeklySlotSource, admin.site)


def org_source(organization: Organization) -> WeeklySlotSource:  # noqa: F811
    return WeeklySlotSource.objects.create(
        name="Org source",
        type=SlotSourceType.ORGANIZATION,
        strategy="latest",
        organization=organization,
    )


def test_an_unsaved_source_says_so_instead_of_querying(source_admin) -> None:
    assert "Save the source" in source_admin.eligible_videos(WeeklySlotSource())


def test_an_organization_source_with_no_organization_names_the_gap(source_admin) -> None:
    source = WeeklySlotSource.objects.create(
        name="Rudderless", type=SlotSourceType.ORGANIZATION, strategy="latest"
    )

    assert "no organization is set" in source_admin.eligible_videos(source)


def test_an_empty_pool_says_so(source_admin, organization) -> None:  # noqa: F811
    assert source_admin.eligible_videos(org_source(organization)) == "The pool is empty."


def test_a_healthy_pool_counts_without_excuses(source_admin, organization) -> None:  # noqa: F811
    make_video(organization, "Airs fine")

    assert source_admin.eligible_videos(org_source(organization)) == "1 of 1 videos eligible."


def test_a_failed_import_is_reported_as_the_reason(source_admin, organization) -> None:  # noqa: F811
    make_video(organization, "Airs fine")
    make_video(organization, "Broken file", proper_import=False)

    line = source_admin.eligible_videos(org_source(organization))

    assert line == "1 of 2 videos eligible -- 1 did not import cleanly."


def test_a_missing_responsible_editor_is_reported_as_the_reason(
    source_admin,
    organization,  # noqa: F811
) -> None:
    """The rule that keeps unattended airtime off the channel. It leaves
    the pool full and the eligible count at zero, which without this line
    looks exactly like a misconfigured source."""
    make_video(organization, "Nobody answers for this")
    organization.editor = User.objects.create(email="not-responsible@example.test")
    organization.editor.is_active = False
    organization.editor.save()
    organization.save()

    line = source_admin.eligible_videos(org_source(organization))

    assert line == "0 of 1 videos eligible -- 1 from an organization with no responsible editor."


def test_the_slot_count_column_reads_the_annotation(source_admin, organization, rf) -> None:  # noqa: F811
    org_source(organization)

    listed = source_admin.get_queryset(rf.get("/admin/fk/weeklyslotsource/")).get()

    assert source_admin.slot_count(listed) == 0


def test_the_video_count_column_is_a_bare_number_when_nothing_is_dropped(
    source_admin,
    organization,  # noqa: F811
) -> None:
    make_video(organization, "Airs fine")

    assert source_admin.video_count(org_source(organization)) == "1"


def test_the_video_count_column_shows_the_shortfall_when_videos_are_dropped(
    source_admin,
    organization,  # noqa: F811
) -> None:
    """The whole point of the column: a source whose pool looks healthy
    but whose scheduler sees nothing is visible from the list."""
    make_video(organization, "Airs fine")
    make_video(organization, "Broken file", proper_import=False)

    assert source_admin.video_count(org_source(organization)) == "1 of 2"


def test_the_video_count_column_survives_a_source_with_no_organization(source_admin) -> None:
    source = WeeklySlotSource.objects.create(
        name="Rudderless", type=SlotSourceType.ORGANIZATION, strategy="latest"
    )

    assert source_admin.video_count(source) == "--"
