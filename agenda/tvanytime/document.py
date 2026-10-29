"""Turn schedule items into a NorDig TV-Anytime document.

The shape is fixed by ETSI TS 102 822-3-1: every table is a sequence, so
element *order* is part of the contract and not a matter of taste. The
builders below emit fields in schema order, and `test_tvanytime` holds
the whole document against the published XSD so that a reordering is a
test failure rather than something a consumer discovers.

Two structural choices are worth stating up front.

A programme and its transmissions are separate things here, as they are
in TV-Anytime and as they are not in XMLTV: `ProgramInformationTable`
describes each video once, `ProgramLocationTable` says when and where it
goes out, and the two are joined by a CRID. That is what lets the same
video appear as a linear broadcast and as an on-demand offer without
repeating its metadata, and it is why a repeat costs one small
`ScheduleEvent` rather than a second copy of everything.

Anything the database cannot answer is omitted rather than guessed. A
missing element means "we did not say", which every consumer handles; an
invented one is wrong in a way nobody downstream can detect.
"""

import mimetypes
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Min, Q

from fk.models import ImageRole, Scheduleitem, Series, Video, VideoFileVariant

from . import cs

OSLO = ZoneInfo("Europe/Oslo")

TVA = "urn:tva:metadata:2019"
MPEG7 = "urn:tva:mpeg7:2008"
XML = "http://www.w3.org/XML/1998/namespace"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Prefixes for the serialized document. ElementTree keeps this mapping in
# module-global state, so registering at import is the only way to stop it
# inventing `ns0:`; the names match the NorDig example files.
ET.register_namespace("tva", TVA)
ET.register_namespace("mpeg7", MPEG7)
ET.register_namespace("xsi", XSI)

# The still images our ingest produces, largest first. All three are the
# same frame at different sizes, so they are the same kind of related
# material and differ only in the URL a consumer picks.
THUMBNAIL_VARIANTS = (
    VideoFileVariant.LARGE_THUMB,
    VideoFileVariant.MED_THUMB,
    VideoFileVariant.SMALL_THUMB,
)

# --------------------------------------------------------------------------
# Serialization primitives
# --------------------------------------------------------------------------


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _sub(parent: ET.Element, tag: str, text: str | None = None, /, **attrs: str) -> ET.Element:
    """A TVA-namespaced child, with attribute names in TVA's own casing.

    Attributes arrive as keyword arguments, so the trailing underscore on
    `type_` is stripped: `type` is a Python keyword and `Title type="main"`
    is the attribute TV-Anytime actually wants. The three positional
    parameters are positional-only for the same reason -- `ServiceURL` has
    a `name` attribute, which would otherwise collide with the tag.
    """
    element = ET.SubElement(parent, _q(TVA, tag))
    for key, value in attrs.items():
        element.set(key.rstrip("_"), value)
    if text is not None:
        element.text = text
    return element


def _duration(delta: timedelta) -> str:
    """An xs:duration in the zero-padded form the NorDig examples use.

    `PT01H30M00S` rather than the equally valid `PT1H30M`: consumers
    written against the example files have been known to parse it by
    position, and the padded form costs us nothing.
    """
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"PT{hours:02d}H{minutes:02d}M{seconds:02d}S"


def _instant(moment: datetime) -> str:
    """An xs:dateTime in Europe/Oslo, which is the wall clock a Norwegian
    EPG is read in. Microseconds are dropped: they are an artefact of how
    a row was written, not a statement about when a programme starts."""
    return moment.astimezone(OSLO).replace(microsecond=0).isoformat()


def _time_point(moment: datetime) -> str:
    """An mpeg7 timePoint, which is what ProductionDate carries."""
    return moment.astimezone(OSLO).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _frame_rate(thousandths: int) -> str:
    """`Video.framerate` is thousandths of a frame per second; TVA wants
    frames per second, as an integer or a ratio."""
    if thousandths % 1000 == 0:
        return str(thousandths // 1000)
    return f"{thousandths}/1000"


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


def video_crid(video: Video) -> str:
    """The content reference identifier for a video.

    Location-independent by design (Guidelines 2.3.1): the same CRID names
    the programme whether it is going out on air or sitting in the on-demand
    catalogue, which is what lets a receiver recognise the two as one thing.
    It is derived from the primary key and must stay stable -- a consumer
    that has recorded a CRID expects it to keep meaning the same programme.
    """
    return f"crid://{settings.TVA_AUTHORITY}/video/{video.id}"


def series_crid(series: Series) -> str:
    """The stable group identifier shared by a series and all its episodes."""
    return f"crid://{settings.TVA_AUTHORITY}/series/{series.id}"


def item_crid(item: Scheduleitem) -> str:
    """The CRID for a schedule item that has no video.

    These are named transmissions -- live sessions, continuity -- with no
    content record behind them, so the transmission is the only thing there
    is to identify and the CRID is minted from it.
    """
    return f"crid://{settings.TVA_AUTHORITY}/schedule/{item.id}"


# --------------------------------------------------------------------------
# Facts gathered per document, in bulk
# --------------------------------------------------------------------------


class _FirstBroadcasts:
    """When each video first went out, for deciding `Repeat`.

    Asked across the whole schedule rather than the requested window: a
    programme shown today for the second time is a repeat even if the first
    showing was last year and is nowhere in this document.
    """

    def __init__(self, video_ids: Sequence[int]):
        self._first: dict[int, datetime] = {}
        if video_ids:
            self._first = dict(
                Scheduleitem.objects.filter(video_id__in=video_ids)
                .values("video_id")
                .annotate(first=Min("starttime"))
                .values_list("video_id", "first")
            )

    def is_repeat(self, item: Scheduleitem) -> bool | None:
        """None where we cannot tell, which omits the element."""
        video_id = item.video_id
        if video_id is None:
            return None
        first = self._first.get(video_id)
        if first is None:
            return None
        return item.starttime > first


# --------------------------------------------------------------------------
# Programme description
# --------------------------------------------------------------------------


def _has_responsible_editor(video: Video) -> bool:
    editor = video.organization.editor
    return editor is not None and editor.is_active


def _offered_on_demand(video: Video) -> bool:
    """Whether the video may be published as an on-demand programme.

    The same three rules the website applies, restated against a single
    instance because the feed already has the rows in memory: it must be
    published and properly imported, its organization must have an
    ansvarlig redaktør to answer for it, and -- while WEB_NO_TONO stands --
    it must not carry TONO-registered music, which we hold no online
    rights for.
    """
    if not (video.publish_on_web and video.proper_import):
        return False
    if settings.WEB_NO_TONO and video.has_tono_records:
        return False
    return _has_responsible_editor(video)


def _still_images(
    video: Video,
) -> list[tuple[str, str, str | None, int | None, int | None]]:
    """Role, URL, MIME type and dimensions for each programme image."""

    images: list[tuple[str, str, str | None, int | None, int | None]] = [
        (
            ImageRole(image.role).how_related,
            settings.FK_MEDIA_URLPREFIX + image.filename,
            image.media_type,
            image.width,
            image.height,
        )
        for image in video.images.all()
    ]

    # Ingest thumbnails remain useful fallbacks alongside the editorial
    # images. Their dimensions predate the image model and are unknown.
    by_variant = {file.variant: file for file in video.videofile_set.all()}
    for variant in THUMBNAIL_VARIANTS:
        file = by_variant.get(variant)
        if file is None:
            continue
        url = settings.FK_MEDIA_URLPREFIX + file.location(relative=True)
        images.append(
            (
                cs.HOW_RELATED_SHOW_STILL,
                url,
                mimetypes.guess_type(file.filename)[0],
                None,
                None,
            )
        )
    return images


def _has_subtitles(video: Video) -> bool:
    return any(file.variant == VideoFileVariant.SRT for file in video.videofile_set.all())


def _add_related_material(parent: ET.Element, how_related: str, url: str, **image: str) -> None:
    """One RelatedMaterial block: what it is, optionally its format, where
    it is. `Format` is skipped entirely when the media type is unknown --
    an empty `StillPictureFormat` would assert a format we do not have."""
    material = _sub(parent, "RelatedMaterial")
    _sub(material, "HowRelated", href=how_related)
    if image:
        _sub(_sub(material, "Format"), "StillPictureFormat", **image)
    _sub(_sub(material, "MediaLocator"), "MediaUri", url)


def _add_basic_description(parent: ET.Element, video: Video) -> None:
    """BasicDescription for a video, in schema sequence order.

    The order below is BasicContentDescriptionType's: Title, Synopsis,
    Keyword, Genre, ParentalGuidance, Language, CaptionLanguage,
    SignLanguage, CreditsList, RelatedMaterial, ProductionDate,
    ProductionLocation, Duration. Inserting a field in the wrong place
    makes the document invalid, not merely untidy.
    """
    description = _sub(parent, "BasicDescription")
    _sub(description, "Title", video.name, type_="main")

    if video.description:
        _sub(description, "Synopsis", video.description.strip(), length="long")

    for category in video.categories.all():
        if category.tva_genre:
            _sub(description, "Genre", href=category.tva_genre, type_="main")

    if video.minimum_age is not None:
        guidance = _sub(description, "ParentalGuidance")
        ET.SubElement(guidance, _q(MPEG7, "MinimumAge")).text = str(video.minimum_age)

    if video.spoken_language:
        _sub(description, "Language", video.spoken_language, type_="original")

    if _has_subtitles(video):
        # Sidecar SubRip, which a player can switch off -- that is what
        # `closed` means here, as against subtitles burned into the video.
        _sub(
            description,
            "CaptionLanguage",
            video.spoken_language or settings.TVA_DEFAULT_LANGUAGE,
            closed="true",
        )

    # The member organization is the party responsible for the programme,
    # which is the closest true statement we can make about who made it.
    # Named as producer rather than left out: for a public-access channel
    # it is the single most useful credit in the whole record.
    credits_list = _sub(description, "CreditsList")
    item = _sub(credits_list, "CreditsItem", role=cs.ROLE_PRODUCER)
    _sub(item, "OrganizationName", video.organization.name)

    for role, url, media_type, width, height in _still_images(video):
        image = {"href": media_type} if media_type else {}
        if width is not None and height is not None:
            image.update(horizontalSize=str(width), verticalSize=str(height))
        _add_related_material(description, role, url, **image)

    if video.ref_url:
        _add_related_material(description, cs.HOW_RELATED_PROGRAMME_WEBSITE, video.ref_url)

    # When the programme came to us. Not strictly its production date --
    # we do not record that -- but the earliest date we can stand behind,
    # and the field consumers use to sort archive material.
    produced = video.uploaded_time or video.created_time
    if produced is not None:
        _sub(_sub(description, "ProductionDate"), "TimePoint", _time_point(produced))

    _sub(description, "ProductionLocation", "NO")

    if video.duration:
        _sub(description, "Duration", _duration(video.duration))


def _add_program_information(table: ET.Element, crid: str, video: Video) -> None:
    information = _sub(table, "ProgramInformation", programId=crid)
    _add_basic_description(information, video)

    # A resolvable web address for the programme, offered as an alternative
    # identifier so a consumer that does not follow CRIDs still has
    # something to link its own record to.
    _sub(
        information,
        "OtherIdentifier",
        f"{settings.SITE_URL}{video.get_absolute_url()}",
        type_="URI",
        authority=settings.TVA_AUTHORITY,
    )

    if video.framerate:
        attributes = _sub(information, "AVAttributes")
        video_attributes = _sub(attributes, "VideoAttributes")
        _sub(video_attributes, "FrameRate", _frame_rate(video.framerate))

    if video.series is not None:
        episode = _sub(information, "EpisodeOf", crid=series_crid(video.series))
        if video.episode_number is not None:
            episode.set("index", str(video.episode_number))


def _add_group_information(
    table: ET.Element,
    series: Series,
    episode_count: int,
    numbered_episode_count: int,
) -> None:
    """Describe one series referenced by a programme in this document."""
    information = _sub(
        table,
        "GroupInformation",
        groupId=series_crid(series),
        numOfItems=str(episode_count),
        ordered="true"
        if episode_count > 0 and numbered_episode_count == episode_count
        else "false",
    )
    group_type = _sub(information, "GroupType", value="series")
    group_type.set(_q(XSI, "type"), "tva:ProgramGroupTypeType")

    description = _sub(information, "BasicDescription")
    _sub(description, "Title", series.name, type_="main")
    if series.synopsis:
        _sub(description, "Synopsis", series.synopsis.strip(), length="long")
    if series.image_url:
        media_type = mimetypes.guess_type(series.image_url)[0]
        image = {"href": media_type} if media_type else {}
        _add_related_material(description, cs.HOW_RELATED_SHOW_STILL, series.image_url, **image)

    _sub(
        information,
        "OtherIdentifier",
        f"{settings.SITE_URL}{series.get_absolute_url()}",
        type_="URI",
        authority=settings.TVA_AUTHORITY,
    )


def _add_placeholder_information(table: ET.Element, crid: str, item: Scheduleitem) -> None:
    """ProgramInformation for a transmission with no video behind it.

    Every CRID a ScheduleEvent points at has to resolve to a programme
    somewhere, so a named item still gets a record -- just one holding the
    only two things known about it.
    """
    information = _sub(table, "ProgramInformation", programId=crid)
    description = _sub(information, "BasicDescription")
    _sub(description, "Title", item.default_name or "Frikanalen", type_="main")
    if item.duration:
        _sub(description, "Duration", _duration(item.duration))


# --------------------------------------------------------------------------
# Programme locations
# --------------------------------------------------------------------------


def _add_schedule_event(
    schedule: ET.Element,
    item: Scheduleitem,
    crid: str,
    repeats: _FirstBroadcasts,
    now: datetime,
) -> None:
    event = _sub(schedule, "ScheduleEvent")
    _sub(event, "Program", crid=crid)
    _sub(event, "InstanceMetadataId", f"imi:{item.id}")

    # InstanceDescription carries only what differs from the programme
    # record. A schedule item that names itself something other than its
    # video is the one case that arises here.
    video = item.video
    if video is not None and item.default_name and item.default_name != video.name:
        _sub(_sub(event, "InstanceDescription"), "Title", item.default_name)

    _sub(event, "PublishedStartTime", _instant(item.starttime))
    _sub(event, "PublishedEndTime", _instant(item.endtime))
    if item.duration:
        _sub(event, "PublishedDuration", _duration(item.duration))

    # Actual times, for airtime that has already passed. NorDig asks for
    # these, and they are what tells a consumer that a transmission is
    # confirmed rather than merely planned -- so they are emitted only
    # once there is something to confirm, never for future airtime.
    #
    # They repeat the published times because playout follows the
    # schedule: this channel plays files at the times the schedule gives,
    # and nothing downstream reports back otherwise. That makes the
    # schedule our best account of what went out, not a measurement of it
    # -- see docs/tvanytime.md on what measuring would take.
    if item.starttime <= now:
        _sub(event, "ActualStartTime", _instant(item.starttime))
        # An item still on air has started but not ended, which
        # TV-Anytime expresses by leaving the end open.
        if item.endtime <= now:
            _sub(event, "ActualEndTime", _instant(item.endtime))
            if item.duration:
                _sub(event, "ActualDuration", _duration(item.duration))

    _sub(event, "Live", value="true" if item.is_live else "false")

    repeat = repeats.is_repeat(item)
    if repeat is not None:
        _sub(event, "Repeat", value="true" if repeat else "false")

    # Free-to-air, always: Frikanalen is public access and nothing on it
    # sits behind a subscription or a payment.
    _sub(event, "Free", value="true")


def _add_on_demand_program(service: ET.Element, crid: str, video: Video) -> None:
    program = _sub(service, "OnDemandProgram")
    _sub(program, "Program", crid=crid)
    _sub(program, "ProgramURL", f"{settings.SITE_URL}{video.get_absolute_url()}")
    _sub(program, "InstanceMetadataId", f"imi:vod{video.id}")
    if video.duration:
        _sub(program, "PublishedDuration", _duration(video.duration))

    available_from = video.uploaded_time or video.created_time
    if available_from is not None:
        _sub(program, "StartOfAvailability", _instant(available_from))
    # No EndOfAvailability on purpose: our archive does not expire, and an
    # invented end date would have distributors withdraw content that is
    # still up.

    _sub(program, "DeliveryMode", "streaming")
    _sub(program, "Free", value="true")


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


def _add_service(
    table: ET.Element,
    service_id: str,
    name: str,
    urls: dict[str, str],
    service_types: Sequence[str],
) -> None:
    # No RelatedMaterial: a service may carry a channel logo, and we hold
    # no published image to point at. See docs/tvanytime.md on imagery.
    service = _sub(table, "ServiceInformation", serviceId=service_id)
    _sub(service, "Name", name)
    for carrier, url in urls.items():
        _sub(service, "ServiceURL", url, name=carrier)
    for service_type in service_types:
        _sub(service, "ServiceType", href=service_type)


def _add_service_information(table: ET.Element) -> None:
    channel_name = settings.CHANNEL_DISPLAY_NAMES[0]
    _add_service(
        table,
        settings.TVA_LINEAR_SERVICE_ID,
        channel_name,
        settings.TVA_LINEAR_SERVICE_URLS,
        (cs.SERVICE_TYPE_LINEAR, cs.SERVICE_TYPE_VIDEO),
    )
    _add_service(
        table,
        settings.TVA_ONDEMAND_SERVICE_ID,
        f"{channel_name} arkiv",
        settings.TVA_ONDEMAND_SERVICE_URLS,
        (cs.SERVICE_TYPE_ON_DEMAND, cs.SERVICE_TYPE_VIDEO),
    )


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


def _add_origination_information(root: ET.Element) -> None:
    table = _sub(root, "MetadataOriginationInformationTable")
    origination = _sub(table, "MetadataOriginationInformation", originID=settings.TVA_AUTHORITY)
    _sub(origination, "Publisher", settings.TVA_PUBLISHER)
    _sub(origination, "RightsOwner", settings.TVA_RIGHTS_OWNER)
    notice = _sub(origination, "CopyrightNotice", settings.TVA_COPYRIGHT_NOTICE)
    notice.set(_q(XML, "lang"), "no")


def build(
    items: Iterable[Scheduleitem],
    window_start: datetime,
    window_end: datetime,
    published_at: datetime,
) -> ET.Element:
    """The TVAMain element for `items`, which must already be ordered by
    start time and have their video, organization, categories and files
    loaded (see `views.schedule_queryset`).

    `window_start`/`window_end` become the Schedule element's bounds, which
    is how a consumer tells "nothing is scheduled then" apart from "this
    document does not cover then" -- a distinction an empty feed cannot
    otherwise make.
    """
    items = list(items)

    root = ET.Element(_q(TVA, "TVAMain"))
    root.set(_q(XML, "lang"), settings.TVA_DEFAULT_LANGUAGE)
    root.set("type", "epg")
    root.set("publisher", settings.TVA_PUBLISHER)
    root.set("rightsOwner", settings.TVA_RIGHTS_OWNER)
    root.set("originID", settings.TVA_AUTHORITY)
    root.set("publicationTime", _instant(published_at))

    _add_origination_information(root)
    description = _sub(root, "ProgramDescription")

    # One entry per programme, in the order it is first scheduled, so the
    # table reads down the day rather than by primary key.
    programmes: dict[str, Video | Scheduleitem] = {}
    for item in items:
        video = item.video
        if video is not None:
            programmes.setdefault(video_crid(video), video)
        else:
            programmes[item_crid(item)] = item

    information_table = _sub(description, "ProgramInformationTable")
    for crid, programme in programmes.items():
        if isinstance(programme, Scheduleitem):
            _add_placeholder_information(information_table, crid, programme)
        else:
            _add_program_information(information_table, crid, programme)

    series_by_id: dict[int, Series] = {}
    for programme in programmes.values():
        if isinstance(programme, Video) and programme.series is not None:
            series_by_id[programme.series.pk] = programme.series
    if series_by_id:
        counts = {
            row["series_id"]: (row["episode_count"], row["numbered_episode_count"])
            for row in Video.objects.filter(series_id__in=series_by_id)
            .values("series_id")
            .annotate(
                episode_count=Count("id"),
                numbered_episode_count=Count("id", filter=Q(episode_number__isnull=False)),
            )
        }
        group_table = _sub(description, "GroupInformationTable")
        for series_id, series in series_by_id.items():
            episode_count, numbered_episode_count = counts[series_id]
            _add_group_information(
                group_table,
                series,
                episode_count,
                numbered_episode_count,
            )

    location_table = _sub(description, "ProgramLocationTable")
    video_ids = [item.video_id for item in items if item.video_id is not None]
    repeats = _FirstBroadcasts(video_ids)

    schedule = _sub(
        location_table,
        "Schedule",
        serviceIDRef=settings.TVA_LINEAR_SERVICE_ID,
        start=_instant(window_start),
        end=_instant(window_end),
    )
    for item in items:
        crid = video_crid(item.video) if item.video is not None else item_crid(item)
        _add_schedule_event(schedule, item, crid, repeats, published_at)

    # ScheduleType requires at least one event, so an empty window would
    # make the document invalid. Drop the element and let the absence of a
    # Schedule say what an empty one cannot.
    if not items:
        location_table.remove(schedule)

    on_demand = [
        (crid, programme)
        for crid, programme in programmes.items()
        if isinstance(programme, Video) and _offered_on_demand(programme)
    ]
    if on_demand:
        service = _sub(
            location_table,
            "OnDemandService",
            serviceIDRef=settings.TVA_ONDEMAND_SERVICE_ID,
        )
        for crid, video in on_demand:
            _add_on_demand_program(service, crid, video)

    _add_service_information(_sub(description, "ServiceInformationTable"))

    ET.indent(root)
    return root


def to_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
