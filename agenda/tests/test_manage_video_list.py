"""
The members' "My videos" page: who sees it, whose videos it lists, and
what it does with the ?page= it puts in its own pagination links.

The page number arrives from a query string, so it is whatever the
caller typed. Every case here is a URL a user can reach by editing the
address bar, and none of them may be a 500.
"""

import pytest
from django.test import Client
from django.urls import reverse

from fk.models import User, Video

pytestmark = pytest.mark.django_db

# ManageVideoList.VIDEOS_PER_PAGE is 20, so 25 videos make two pages
# with the second one short -- enough to tell first from last.
VIDEO_COUNT = 25


@pytest.fixture
def member() -> User:
    return User.objects.create(email="video-list-member@example.test")


@pytest.fixture
def member_client(member: User) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def videos(member: User) -> list[Video]:
    return [Video.objects.create(name=f"Video {n:02}", creator=member) for n in range(VIDEO_COUNT)]


def page_of(client: Client, page: str | None = None):
    query = {} if page is None else {"page": page}
    return client.get(reverse("manage-video-list"), query)


def test_anonymous_visitors_are_sent_to_the_login_page() -> None:
    response = page_of(Client())

    assert response.status_code == 302
    assert response.url == "/login/?next=/members/video/"


def test_lists_only_the_members_own_videos(member_client: Client, member: User) -> None:
    Video.objects.create(name="Mine", creator=member)
    stranger = User.objects.create(email="stranger@example.test")
    Video.objects.create(name="Theirs", creator=stranger)

    response = page_of(member_client)

    assert response.status_code == 200
    assert [video.name for video in response.context["videos"]] == ["Mine"]


def test_defaults_to_the_first_page(member_client: Client, videos: list[Video]) -> None:
    response = page_of(member_client)

    assert response.status_code == 200
    assert response.context["page"].number == 1
    assert len(response.context["videos"]) == 20


def test_an_explicit_page_is_honoured(member_client: Client, videos: list[Video]) -> None:
    response = page_of(member_client, "2")

    assert response.status_code == 200
    assert response.context["page"].number == 2
    assert len(response.context["videos"]) == VIDEO_COUNT - 20


@pytest.mark.parametrize(
    "page",
    [
        pytest.param("abc", id="not-a-number"),
        pytest.param("", id="empty"),
        pytest.param("1.5", id="fractional"),
    ],
)
def test_a_page_number_that_is_not_a_number_gives_the_first(
    member_client: Client, videos: list[Video], page: str
) -> None:
    response = page_of(member_client, page)

    assert response.status_code == 200
    assert response.context["page"].number == 1


@pytest.mark.parametrize(
    "page",
    [
        pytest.param("999", id="past-the-end"),
        # Django's get_page() makes no distinction between out of range
        # above and below: both land on the last page. Pinned because it
        # surprises, not because the low end deserves that answer.
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
    ],
)
def test_a_page_number_out_of_range_gives_the_last(
    member_client: Client, videos: list[Video], page: str
) -> None:
    """Rather than the 500 that Paginator.page() would raise."""
    response = page_of(member_client, page)

    assert response.status_code == 200
    assert response.context["page"].number == 2
    assert not response.context["page"].has_next()
