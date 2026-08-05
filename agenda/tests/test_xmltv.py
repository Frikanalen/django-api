"""
Characterization tests for the XMLTV program guide feed.

External EPG consumers parse this XML, so the contract is the parsed
document: element names, attributes and text. Template whitespace is
deliberately not part of it, which is why these tests assert on an
ElementTree rather than on raw bytes.
"""

from datetime import datetime, timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="xmltv-editor@example.test")
    organization = Organization.objects.create(name="XMLTV org", editor=editor)
    return Video.objects.create(
        name="Documentary",
        header="About the harbour",
        creator=editor,
        organization=organization,
        duration=timedelta(hours=1),
        proper_import=True,
    )


def schedule(video: Video | None, starttime: datetime, **fields) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        starttime=starttime,
        duration=fields.pop("duration", timedelta(hours=1)),
        schedulereason=Scheduleitem.REASON_ADMIN,
        **fields,
    )


def fetch_feed(url: str) -> ElementTree.Element:
    response = Client().get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/xml"
    return ElementTree.fromstring(response.content)


def test_daily_feed_document_structure(video: Video) -> None:
    schedule(video, datetime(2015, 1, 1, 12, tzinfo=OSLO))
    schedule(
        None,
        datetime(2015, 1, 1, 13, tzinfo=OSLO),
        default_name="Pause programming",
        duration=timedelta(minutes=30),
    )

    doc = fetch_feed(reverse("xmltv-feed", args=("2015", "01", "01")))

    assert doc.tag == "tv"
    assert doc.attrib == {"generator-info-name": "fkweb.agenda.xmltv"}

    channel = doc.find("channel")
    assert channel.attrib == {"id": "frikanalen.tv"}
    assert [name.text for name in channel.findall("display-name")] == ["Frikanalen"]
    assert channel.find("url").text == "https://frikanalen.no"

    with_video, without_video = doc.findall("programme")

    assert with_video.attrib == {
        "channel": "frikanalen.tv",
        "start": "20150101120000 +0100",
        "stop": "20150101130000 +0100",
    }
    assert with_video.find("title").attrib == {"lang": "no"}
    assert with_video.find("title").text == "Documentary"
    assert with_video.find("desc").text == "About the harbour"
    assert with_video.find("url").text == f"https://frikanalen.no/video/{video.id}/"
    assert with_video.find("length").attrib == {"units": "seconds"}
    assert with_video.find("length").text == "3600"

    # Items without a video fall back to default_name and carry no
    # description or URL.
    assert without_video.attrib["start"] == "20150101130000 +0100"
    assert without_video.attrib["stop"] == "20150101133000 +0100"
    assert without_video.find("title").text == "Pause programming"
    assert without_video.find("length").text == "1800"
    assert without_video.find("desc") is None
    assert without_video.find("url") is None


def test_daily_feed_covers_one_oslo_calendar_day(video: Video) -> None:
    schedule(video, datetime(2014, 12, 31, 23, tzinfo=OSLO), duration=timedelta(minutes=30))
    included = schedule(video, datetime(2015, 1, 1, 0, tzinfo=OSLO))
    schedule(video, datetime(2015, 1, 2, 0, tzinfo=OSLO))

    doc = fetch_feed(reverse("xmltv-feed", args=("2015", "01", "01")))

    starts = [programme.attrib["start"] for programme in doc.findall("programme")]
    assert starts == [included.starttime.strftime("%Y%m%d%H%M%S +0100")]


def test_missing_video_header_renders_as_the_string_none(video: Video) -> None:
    """
    Known wart, pinned on purpose: the template renders `video.header`
    directly, so a NULL header becomes the literal text 'None' in the
    feed. A refactor that wants to change this should have to meet this
    test and decide consciously.
    """
    video.header = None
    video.save()
    schedule(video, datetime(2015, 1, 1, 12, tzinfo=OSLO))

    doc = fetch_feed(reverse("xmltv-feed", args=("2015", "01", "01")))

    assert doc.find("programme").find("desc").text == "None"


def test_upcoming_feed_spans_seven_days_from_today(video: Video) -> None:
    now = timezone.now()
    schedule(video, now - timedelta(days=1, hours=2))
    included = [
        schedule(video, now + timedelta(hours=1)),
        schedule(video, now + timedelta(days=6, hours=1)),
    ]
    schedule(video, now + timedelta(days=8))

    doc = fetch_feed(reverse("xmltv-feed-upcoming"))

    urls = [programme.find("url").text for programme in doc.findall("programme")]
    assert len(urls) == len(included)


def test_home_page_links_to_todays_feed() -> None:
    response = Client().get(reverse("xmltv-home"))

    assert response.status_code == 200
    now = timezone.now()
    today_url = reverse("xmltv-feed", args=(now.year, f"{now.month:02}", f"{now.day:02}"))
    assert today_url.encode() in response.content
