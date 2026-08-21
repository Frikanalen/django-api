"""Tests for the NorDig TV-Anytime feed.

Two kinds of assertion live here, and they answer different questions.

`test_feed_validates_against_the_published_schema` and its siblings check
that the document is *well-formed TV-Anytime*: they run the real ETSI XSD
over it, which is the only practical way to catch a field emitted in the
wrong position. TV-Anytime's types are `xs:sequence`, so ordering is a
schema constraint, and getting it wrong produces a document that looks
perfectly reasonable and that a distributor's parser rejects.

The rest check that the mapping from Frikanalen's models onto that
structure is the one we meant -- that a repeat is marked as a repeat, that
a TONO video is not offered on demand, and so on. Those are assertions
about the parsed tree, never about the bytes, so that reformatting the
output is not a test failure.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from lxml import etree
from lxml import html as lxml_html

from fk.models import (
    Category,
    Organization,
    Scheduleitem,
    User,
    Video,
    VideoFile,
    VideoFileVariant,
)

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")
TVA = "urn:tva:metadata:2019"
MPEG7 = "urn:tva:mpeg7:2008"
NS = {"tva": TVA, "mpeg7": MPEG7}

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "tvanytime" / "schemas"


@pytest.fixture(scope="session")
def tva_schema() -> etree.XMLSchema:
    """The published TV-Anytime schema, with one defect repaired.

    ETSI ships `tva_metadata_3-1.xsd` with `maxOccurs=" unbounded"` on
    ServiceInformationType/ServiceType -- a leading space, which is not a
    valid value of the attribute and which every XSD processor refuses to
    load. The vendored copy is kept byte-identical to the published one
    (see schemas/README.md), so the repair happens here, in memory, where
    it is visible rather than baked into a file that claims to be ETSI's.
    """
    source = (SCHEMA_DIR / "tva_metadata_3-1.xsd").read_bytes()
    repaired = source.replace(b'maxOccurs=" unbounded"', b'maxOccurs="unbounded"')
    assert repaired != source, "ETSI fixed the whitespace defect; drop this workaround"
    # Parsed with the schema directory as the base URI so the relative
    # xsd:import of tva_mpeg7.xsd and xml.xsd resolves to the vendored
    # copies rather than being fetched over the network.
    document = etree.fromstring(repaired, base_url=str(SCHEMA_DIR / "tva_metadata_3-1.xsd"))
    return etree.XMLSchema(document)


def assert_valid(schema: etree.XMLSchema, payload: bytes) -> etree._Element:
    document = etree.fromstring(payload)
    if not schema.validate(document):
        # str() of the error log is one `line:column:LEVEL: message` per
        # line, which is what makes a sequence violation readable: it names
        # the element found and lists the ones the schema expected there.
        pytest.fail(f"document is not valid TV-Anytime:\n{schema.error_log}")
    return document


def one(element: etree._Element, path: str) -> etree._Element:
    """The element at `path`, which the caller expects to be there.

    Same reasoning as `child()` in test_xmltv: find() returns None for a
    missing element, so without this a contract that has been broken
    surfaces as an AttributeError on the next line rather than as a failed
    lookup. Tests that assert an element is *absent* call find() directly.
    """
    found = element.find(path, NS)
    assert found is not None, f"no {path} under <{etree.QName(element).localname}>"
    return found


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="tva-editor@example.test", first_name="Kari")


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Foreningen Testsending", editor=editor)


@pytest.fixture
def category() -> Category:
    return Category.objects.create(
        id=900, name="Samfunn", tva_genre="urn:tva:metadata:cs:ContentCS:2011:3.1.3.2"
    )


@pytest.fixture
def video(organization: Organization, editor: User, category: Category) -> Video:
    video = Video.objects.create(
        name="Havna vår",
        header="Om havna i Oslo",
        description="En lengre beskrivelse av dokumentaren om havna.",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
        publish_on_web=True,
        uploaded_time=datetime(2024, 3, 1, 12, tzinfo=OSLO),
    )
    video.categories.add(category)
    return video


def schedule(video: Video | None, starttime: datetime, **fields) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        starttime=starttime,
        duration=fields.pop("duration", timedelta(minutes=30)),
        schedulereason=Scheduleitem.REASON_ADMIN,
        **fields,
    )


def fetch(url: str, **params) -> bytes:
    response = Client().get(url, params)
    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/xml"
    return response.content


def feed_for(day: datetime, **params) -> bytes:
    url = reverse("api-tvanytime-date", args=(f"{day.year:04}", f"{day.month:02}", f"{day.day:02}"))
    return fetch(url, **params)


DAY = datetime(2024, 6, 3, tzinfo=OSLO)


# --------------------------------------------------------------------------
# Schema conformance
# --------------------------------------------------------------------------


def test_feed_validates_against_the_published_schema(tva_schema, video: Video) -> None:
    schedule(video, DAY.replace(hour=12))
    assert_valid(tva_schema, feed_for(DAY))


def test_richest_possible_programme_still_validates(
    tva_schema, video: Video, organization: Organization
) -> None:
    """Every optional field we know how to emit, on one programme.

    The point is coverage of element *order*: each field the builder can
    add is present, so a new one inserted at the wrong position in
    BasicContentDescriptionType's sequence fails here.
    """
    video.minimum_age = 12
    video.spoken_language = "nn"
    video.ref_url = "https://example.test/programmet"
    video.save()
    for variant in (
        VideoFileVariant.LARGE_THUMB,
        VideoFileVariant.MED_THUMB,
        VideoFileVariant.SMALL_THUMB,
    ):
        VideoFile.objects.create(video=video, variant=variant, filename="still.jpg")
    VideoFile.objects.create(video=video, variant=VideoFileVariant.SRT, filename="subs.srt")

    schedule(video, DAY.replace(hour=12), is_live=True, default_name="Havna vår, del 2")
    # A second showing, so Repeat is exercised too.
    schedule(video, DAY.replace(hour=20))

    document = assert_valid(tva_schema, feed_for(DAY))

    description = one(document, ".//tva:ProgramInformation/tva:BasicDescription")
    emitted = [etree.QName(child).localname for child in description]
    assert emitted == [
        "Title",
        "Synopsis",
        "Synopsis",
        "Genre",
        "ParentalGuidance",
        "Language",
        "CaptionLanguage",
        "CreditsList",
        "RelatedMaterial",
        "RelatedMaterial",
        "RelatedMaterial",
        "RelatedMaterial",
        "ProductionDate",
        "ProductionLocation",
        "Duration",
    ]


def test_empty_window_validates(tva_schema) -> None:
    """A day with nothing scheduled is a valid document, not an error.

    ScheduleType requires at least one ScheduleEvent, so the empty case
    has to omit the Schedule element rather than emit an empty one.
    """
    document = assert_valid(tva_schema, feed_for(DAY))
    assert document.find(".//tva:Schedule", NS) is None


def test_item_without_a_video_validates_and_is_described(tva_schema) -> None:
    schedule(None, DAY.replace(hour=12), default_name="Direktesending fra Stortinget")

    document = assert_valid(tva_schema, feed_for(DAY))

    information = one(document, ".//tva:ProgramInformation")
    program_id = information.get("programId") or ""
    assert program_id.startswith("crid://frikanalen.no/schedule/")
    assert one(information, ".//tva:Title").text == "Direktesending fra Stortinget"


# --------------------------------------------------------------------------
# The mapping from our models
# --------------------------------------------------------------------------


def test_programme_is_described_once_however_often_it_airs(video: Video) -> None:
    """The reason for TV-Anytime's two-table shape: a repeat costs one
    ScheduleEvent, not a second copy of the programme's metadata."""
    schedule(video, DAY.replace(hour=12))
    schedule(video, DAY.replace(hour=18))

    document = etree.fromstring(feed_for(DAY))

    assert len(document.findall(".//tva:ProgramInformation", NS)) == 1
    events = document.findall(".//tva:ScheduleEvent", NS)
    assert len(events) == 2
    crids = {one(event, "tva:Program").get("crid") for event in events}
    assert crids == {f"crid://frikanalen.no/video/{video.id}"}


def test_first_showing_is_not_a_repeat_and_the_second_is(video: Video) -> None:
    schedule(video, DAY.replace(hour=12))
    schedule(video, DAY.replace(hour=18))

    document = etree.fromstring(feed_for(DAY))

    repeats = [
        one(event, "tva:Repeat").get("value")
        for event in document.findall(".//tva:ScheduleEvent", NS)
    ]
    assert repeats == ["false", "true"]


def test_a_showing_is_a_repeat_even_when_the_first_is_outside_the_window(video: Video) -> None:
    schedule(video, DAY.replace(hour=12) - timedelta(days=30))
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))

    event = one(document, ".//tva:ScheduleEvent")
    assert one(event, "tva:Repeat").get("value") == "true"


def test_aired_item_reports_actual_times_from_the_schedule(video: Video) -> None:
    """Playout follows the schedule and nothing reports back, so the
    scheduled times are our account of what went out. Emitting them is
    what marks the transmission confirmed rather than merely planned."""
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))
    event = one(document, ".//tva:ScheduleEvent")

    assert one(event, "tva:PublishedStartTime").text == "2024-06-03T12:00:00+02:00"
    assert one(event, "tva:ActualStartTime").text == "2024-06-03T12:00:00+02:00"
    assert one(event, "tva:ActualEndTime").text == "2024-06-03T12:30:00+02:00"
    assert one(event, "tva:ActualDuration").text == "PT00H30M00S"


def test_future_airtime_reports_no_actual_times(video: Video) -> None:
    """Nothing has gone out yet, so there is nothing to confirm."""
    tomorrow = datetime.now(tz=OSLO).replace(
        hour=12, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    schedule(video, tomorrow)

    document = etree.fromstring(fetch(reverse("api-tvanytime-upcoming")))
    event = one(document, ".//tva:ScheduleEvent")

    assert event.find("tva:ActualStartTime", NS) is None
    assert event.find("tva:ActualEndTime", NS) is None


def test_item_still_on_air_reports_a_start_but_no_end(video: Video) -> None:
    """TV-Anytime leaves the end open for a transmission in progress."""
    started = datetime.now(tz=OSLO) - timedelta(minutes=10)
    schedule(video, started, duration=timedelta(hours=2))

    document = etree.fromstring(fetch(reverse("api-tvanytime-upcoming")))
    event = one(document, ".//tva:ScheduleEvent")

    assert event.find("tva:ActualStartTime", NS) is not None
    assert event.find("tva:ActualEndTime", NS) is None
    assert event.find("tva:ActualDuration", NS) is None


def test_categories_become_genres_and_unmapped_ones_are_silent(video: Video) -> None:
    unmapped = Category.objects.create(id=901, name="Annet", tva_genre="")
    video.categories.add(unmapped)
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))

    genres = [genre.get("href") for genre in document.findall(".//tva:Genre", NS)]
    assert genres == ["urn:tva:metadata:cs:ContentCS:2011:3.1.3.2"]


def test_the_owning_organization_is_credited_as_producer(video: Video) -> None:
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))
    item = one(document, ".//tva:CreditsList/tva:CreditsItem")

    assert item.get("role") == "urn:mpeg:mpeg7:cs:RoleCS:2011:PRODUCER"
    assert one(item, "tva:OrganizationName").text == "Foreningen Testsending"


def test_published_video_is_offered_on_demand(video: Video) -> None:
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))
    program = one(document, ".//tva:OnDemandService/tva:OnDemandProgram")

    assert one(program, "tva:Program").get("crid") == f"crid://frikanalen.no/video/{video.id}"
    assert one(program, "tva:ProgramURL").text == f"https://frikanalen.no/video/{video.id}/"
    assert one(program, "tva:StartOfAvailability").text == "2024-03-01T12:00:00+01:00"
    assert one(program, "tva:DeliveryMode").text == "streaming"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("publish_on_web", False),
        ("proper_import", False),
        ("has_tono_records", True),
    ],
)
def test_video_we_hold_no_online_rights_for_is_scheduled_but_not_offered(
    video: Video, field: str, value: bool
) -> None:
    """It still airs, so it still appears in the schedule. What it must not
    do is turn up in the on-demand catalogue."""
    setattr(video, field, value)
    video.save()
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))

    assert document.find(".//tva:ScheduleEvent", NS) is not None
    assert document.find(".//tva:OnDemandProgram", NS) is None


def test_video_without_an_active_responsible_editor_is_not_offered_on_demand(
    video: Video, organization: Organization, editor: User
) -> None:
    editor.is_active = False
    editor.save()
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY))

    assert document.find(".//tva:ScheduleEvent", NS) is not None
    assert document.find(".//tva:OnDemandProgram", NS) is None


def test_schedule_window_is_published_even_when_nothing_airs_in_part_of_it(video: Video) -> None:
    """The Schedule bounds are what distinguish "nothing scheduled" from
    "not covered by this document"."""
    schedule(video, DAY.replace(hour=12))

    document = etree.fromstring(feed_for(DAY, days=3))
    element = one(document, ".//tva:Schedule")

    assert element.get("start") == "2024-06-03T00:00:00+02:00"
    assert element.get("end") == "2024-06-06T00:00:00+02:00"


def test_services_describe_the_channel_and_the_archive() -> None:
    document = etree.fromstring(feed_for(DAY))
    services = document.findall(".//tva:ServiceInformation", NS)

    by_id = {service.get("serviceId"): service for service in services}
    assert set(by_id) == {"frikanalen.no/frikanalen", "frikanalen.no/vod"}

    linear = by_id["frikanalen.no/frikanalen"]
    assert one(linear, "tva:Name").text == "Frikanalen"
    assert [t.get("href") for t in linear.findall("tva:ServiceType", NS)] == [
        "urn:nordig:metadata:cs:ServiceTypeCS:2019:linear",
        "urn:nordig:metadata:cs:ServiceTypeCS:2019:video",
    ]


def test_document_attributes_identify_the_publisher() -> None:
    document = etree.fromstring(feed_for(DAY))

    assert etree.QName(document).localname == "TVAMain"
    assert document.get("type") == "epg"
    assert document.get("originID") == "frikanalen.no"
    assert document.get("publisher") == "Foreningen Frikanalen"
    assert document.get("{http://www.w3.org/XML/1998/namespace}lang") == "no"


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


def test_upcoming_feed_starts_today(video: Video) -> None:
    today = datetime.now(tz=OSLO).replace(hour=12, minute=0, second=0, microsecond=0)
    schedule(video, today)
    schedule(video, today + timedelta(days=8))

    document = etree.fromstring(fetch(reverse("api-tvanytime-upcoming")))

    # Seven days by default, so the item eight days out is excluded.
    assert len(document.findall(".//tva:ScheduleEvent", NS)) == 1


def test_days_parameter_widens_the_window(video: Video) -> None:
    today = datetime.now(tz=OSLO).replace(hour=12, minute=0, second=0, microsecond=0)
    schedule(video, today)
    schedule(video, today + timedelta(days=8))

    document = etree.fromstring(fetch(reverse("api-tvanytime-upcoming"), days=10))

    assert len(document.findall(".//tva:ScheduleEvent", NS)) == 2


@pytest.mark.parametrize("days", ["0", "32", "many", "-1"])
def test_unusable_days_parameter_is_rejected(days: str) -> None:
    response = Client().get(reverse("api-tvanytime-upcoming"), {"days": days})
    assert response.status_code == 400


def test_impossible_date_is_not_found() -> None:
    response = Client().get(reverse("api-tvanytime-date", args=("2024", "02", "31")))
    assert response.status_code == 404


def test_feed_is_readable_without_authenticating(video: Video) -> None:
    """A distributor polls this on a timer; NorDig 2.11 asks for a public
    area rather than something to negotiate credentials for."""
    schedule(video, DAY.replace(hour=12))
    response = Client().get(reverse("api-tvanytime-upcoming"))
    assert response.status_code == 200


# --------------------------------------------------------------------------
# The landing page
# --------------------------------------------------------------------------


def test_landing_page_describes_the_feed() -> None:
    response = Client().get(reverse("api-tvanytime-home"))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert "TV-Anytime" in body
    assert reverse("api-tvanytime-upcoming") in body


def test_landing_page_dates_its_example_in_oslo_time() -> None:
    """`api_utc_middleware` forces the active timezone to UTC for anything
    under /api/, so a date resolved by the template would be yesterday's
    for the first hours of every Norwegian day. The view resolves it."""
    today = datetime.now(tz=OSLO).date()
    expected = reverse(
        "api-tvanytime-date",
        args=(f"{today.year:04}", f"{today.month:02}", f"{today.day:02}"),
    )

    body = Client().get(reverse("api-tvanytime-home")).content.decode()

    assert expected in body


def test_every_link_the_landing_page_offers_resolves() -> None:
    """A published index that points at URLs which no longer exist is worse
    than none, and it is exactly what a later rename would produce."""
    page = lxml_html.fromstring(Client().get(reverse("api-tvanytime-home")).content)
    # findall/get rather than an xpath for @href: xpath returns a union wide
    # enough (element, string, bytes, float...) that nothing useful can be
    # said about a member without narrowing it first.
    local = {
        href
        for anchor in page.findall(".//a")
        # Only our own paths: the outbound links to ETSI and NorDig are
        # somebody else's uptime, and this suite does not use the network.
        if (href := anchor.get("href")) is not None and href.startswith("/")
    }
    assert local, "the landing page offers no local links at all"

    client = Client()
    for href in sorted(local):
        assert client.get(href).status_code != 404, f"landing page links to a dead {href}"


def test_landing_page_is_readable_without_authenticating() -> None:
    assert Client().get(reverse("api-tvanytime-home")).status_code == 200


def test_query_count_does_not_grow_with_the_schedule(
    django_assert_max_num_queries, video: Video, organization: Organization, editor: User
) -> None:
    """The builder dereferences a relation per item; without the eager
    loading in `schedule_queryset` a week of programming is thousands of
    queries. Pinned rather than merely intended.

    Four are expected: the schedule itself, the two prefetches
    (categories and files), and the first-broadcast aggregate behind
    `Repeat`. The bound leaves a little headroom but stays far below the
    ten-plus an N+1 over these items would add.
    """
    for hour in range(12, 22):
        other = Video.objects.create(
            name=f"Program {hour}",
            creator=editor,
            organization=organization,
            duration=timedelta(minutes=30),
            proper_import=True,
        )
        schedule(other, DAY.replace(hour=hour))

    with django_assert_max_num_queries(6):
        feed_for(DAY)
