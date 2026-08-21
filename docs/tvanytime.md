# NorDig TV-Anytime feed

Frikanalen publishes its schedule as NorDig EPG/Event metadata — TV-Anytime
as profiled by NorDig, which is the format Nordic and Irish distributors
expect to pull an EPG in. It is served alongside, not instead of, the older
[XMLTV feed](../agenda/templates/agenda/xmltv.xml): XMLTV is a flat list of
programme slots, while TV-Anytime separates a programme from its
transmissions and can therefore describe the same video as a broadcast and
as an on-demand offer at once.

## Endpoints

| URL | What it covers |
| --- | --- |
| `GET /api/tvanytime` | Today onwards. This is the URL to poll. |
| `GET /api/tvanytime/YYYY/MM/DD` | A window starting on that date. |

Both accept `?days=N` (1–31, default 7) and both are anonymous: NorDig
recommends distributing this by pull from "a public area where the latest
and most updated information is available" (Metadata Exchange format
specification 1.3, §2.11), so there is nothing to authenticate.

```bash
curl -s 'https://frikanalen.no/api/tvanytime?days=14' -o frikanalen-epg.xml
```

The endpoints appear in the OpenAPI schema at `/api/schema/`.

## What the document contains

```
TVAMain
├── MetadataOriginationInformationTable   who published this and on what terms
└── ProgramDescription
    ├── ProgramInformationTable           each programme, described once
    ├── ProgramLocationTable
    │   ├── Schedule                      linear transmissions
    │   └── OnDemandService               the same programmes, on demand
    └── ServiceInformationTable           the channel and the archive
```

The join between the two tables is a **CRID**, a location-independent
content identifier:

- `crid://frikanalen.no/video/<id>` for a video
- `crid://frikanalen.no/schedule/<id>` for a transmission with no video
  behind it, such as a live session

CRIDs are stable and consumers may store them. The authority half —
`frikanalen.no` — is what makes them globally unique, so it must remain a
domain we control; it is `TVA_AUTHORITY` in settings. Note it is
deliberately *not* `CHANNEL_ID` (`frikanalen.tv`), which is an RFC 2838
broadcast URI rather than a registered domain.

## Where each field comes from

Named against the NorDig terms list (`NorDigTVATerms_v1_4.xlsx`).

| NorDig term | TV-Anytime | Source |
| --- | --- | --- |
| title | `Title type="main"` | `Video.name`, or `Scheduleitem.default_name` |
| short synopsis | `Synopsis length="short"` | `Video.header` |
| long synopsis | `Synopsis length="long"` | `Video.description` |
| genres | `Genre` | `Category.tva_genre` — see below |
| parental guidance | `ParentalGuidance/MinimumAge` | `Video.minimum_age` |
| language spoken | `Language type="original"` | `Video.spoken_language` |
| subtitle language | `CaptionLanguage closed="true"` | presence of an `srt` `VideoFile` |
| cast and crew | `CreditsList` | the owning `Organization`, credited as producer |
| images | `RelatedMaterial` | the `large_thumb`/`med_thumb`/`small_thumb` files |
| link more information | `RelatedMaterial` | `Video.ref_url` |
| production year | `ProductionDate` | `Video.uploaded_time`, falling back to `created_time` |
| production country | `ProductionLocation` | always `NO` |
| program duration | `Duration` | `Video.duration` |
| alternativ id | `OtherIdentifier type="URI"` | the video's page on frikanalen.no |
| image format | `AVAttributes/VideoAttributes/FrameRate` | `Video.framerate` |
| event id | `InstanceMetadataId` | `imi:<scheduleitem id>` |
| event display start/endtime | `PublishedStartTime`/`PublishedEndTime` | `Scheduleitem.starttime` and `.endtime` |
| event broadcast start/endtime | `ActualStartTime`/`ActualEndTime` | the same `Scheduleitem`, for airtime already past |
| live | `Live` | `Scheduleitem.is_live` |
| rerun | `Repeat` | whether an earlier `Scheduleitem` exists for the video |
| on demand | `OnDemandProgram` | videos we hold online rights for |
| on demand starttime | `StartOfAvailability` | `Video.uploaded_time` |

Anything the database cannot answer is **omitted rather than guessed**. A
missing element means "we did not say", which consumers handle; an invented
one is wrong in a way nobody downstream can detect.

### Published times and actual times

TV-Anytime distinguishes what is *scheduled* (`PublishedStartTime`) from
what *went out* (`ActualStartTime`), and NorDig asks for both.

We publish actual times only for airtime that has already passed, and they
repeat the published times. Playout follows the schedule — this channel
plays files at the times the schedule gives, and nothing reports back
otherwise — so the schedule is our account of what went out rather than a
measurement of it. An item still on air gets a start and no end, which is
how TV-Anytime expresses a transmission in progress; a future item gets
neither, because there is nothing yet to confirm.

The practical value to a consumer is the confirmation itself: an event
carrying actual times is one we are standing behind as having aired.

### Genres

`Category.tva_genre` holds the full classification-scheme href a category is
published as, and is editable in the admin (Categories, edit in place). The
mapping is an editorial judgement, not a derivation, which is why it lives
in the database rather than in code.

Seeded by `fk/migrations/0031_seed_tva_genres.py` against
`urn:tva:metadata:cs:ContentCS:2011`:

| Category | Term | |
| --- | --- | --- |
| Idrett | `3.2` | SPORTS |
| Kultur | `3.1.4` | Arts |
| Religion/livssyn | `3.1.2` | Religion/Philosophies |
| Samfunn | `3.1.3.2` | Social |
| Velferd | `3.1.3.2` | Social |
| Solidaritet og bistand | `3.1.3.7` | International affairs |
| Minoriteter | `3.1.5.4` | Culture/Tradition/Anthropology/Ethnic studies |
| Barn og ungdom | `3.1.3` | General non-fiction — **see below** |
| Beredskap | `3.1.3` | General non-fiction — **see below** |
| Annet | *(blank)* | residual bucket; carries no genre information |

Two of these are coarser than they look, and both are worth a decision by
whoever owns the category list:

- **Barn og ungdom.** ContentCS 2011 has no children's or youth genre at
  all. TV-Anytime expresses audience through `IntendedAudienceCS`, which
  `BasicContentDescription` has no element for, so there is nowhere
  correct to put it. The parent term is true but says little.
- **Beredskap.** Deliberately *not* `3.1.3.8` Military/Defence, which is
  the only close-looking term and would misdescribe a public-access
  channel's civil-preparedness content.

A wrong genre is worse than none: it files the programme under that genre
in every receiver's EPG.

### Rights

A video is offered as an `OnDemandProgram` only when it is published and
properly imported, its organization has an active ansvarlig redaktør, and —
while `WEB_NO_TONO` stands — it carries no TONO-registered music. Such a
video still appears in the `Schedule`, because it does still go out on air.

There is no `RightsInformationTable`. NorDig's rights model is about device
and platform restrictions, and we do not impose any; simply not offering a
programme on demand already says what needs saying.

## Verifying the output

`agenda/tests/test_tvanytime.py` validates the feed against the real ETSI
schema, vendored under `agenda/tvanytime/schemas/` (see the README there).
This is not ceremony. Every TV-Anytime type is an `xs:sequence`, so element
*order* is a schema constraint: a field added in the wrong position
produces a document that looks perfectly reasonable and that a
distributor's parser rejects. Without the XSD in CI, they would find out
before we did.

To check a live response by hand:

```bash
curl -s https://frikanalen.no/api/tvanytime > feed.xml
xmllint --noout --schema agenda/tvanytime/schemas/tva_metadata_3-1.xsd feed.xml
```

(`xmllint` trips over a whitespace defect in ETSI's published schema —
`maxOccurs=" unbounded"` — which the test suite repairs in memory. Apply the
same edit to a scratch copy if you are validating from the shell.)

## Known gaps and follow-up work

What follows is what the *current* feed cannot say. For the wider menu of
model changes that would let it say more — production year, editorial
imagery, cast and crew, keywords, sign language, multiple subtitle tracks —
see [tvanytime-model-proposals.md](tvanytime-model-proposals.md).

### Series and seasons

The largest gap against the NorDig terms list. TV-Anytime carries series
structure in a `GroupInformationTable`: a `GroupInformation` with
`GroupType value="series"` or `"season"`, joined to programmes by
`MemberOf`/`EpisodeOf` with an `index` for the episode number. It is what
lets a receiver offer "record the whole series" and group a strand in the
guide.

Frikanalen has no series model at all — a weekly programme is currently
*n* unrelated videos with similar names — so nothing can be emitted. A
design that would fit:

- A `Series` model owned by an `Organization`: name, synopsis, image.
- `Video.series` (nullable FK) and `Video.episode_number`.
- Optionally a `Season` between them; most members would not use it, so it
  is probably worth deferring until someone asks.
- The feed then emits one `GroupInformation` per series with
  `numOfItems`, and each episode gains `EpisodeOf crid="..." index="n"`.

The work is mostly editorial rather than technical: members would need to
backfill series membership before any of it appears in the feed, so the
admin and members' pages matter more here than the XML does.

### Imagery

This is the largest gap after series structure, and it is entirely a
content problem rather than a code one.

NorDig defines a whole taxonomy of programme imagery in
`HowRelatedNordigCS:2022`, subdividing "Promotional Still Image" into the
roles a guide actually lays out differently:

| Term | |
| --- | --- |
| 19.1 / 19.2 | network and channel logos |
| 19.3 | show logo |
| 19.4 | show still — *the only one we publish* |
| 19.5 | episode still |
| 19.6 / 19.7 | key art, with and without titling |
| 19.8–19.10 | behind the scenes, location, news event |
| 19.11 / 19.12 | portraits (headshot, half body, full body) and cast ensemble |

We publish exactly one of these, and it is the weakest: a single frame
that ingest extracts from the video, offered at three sizes and mapped to
19.4. It is not chosen, not composed, and often not representative. Key
art is what a modern EPG grid actually renders, and we have none — nor a
channel logo, which is why `ServiceInformation` carries no
`RelatedMaterial` at all.

Closing this needs an image model rather than a feed change: somewhere for
a member organization to upload images against a video (or a series), each
tagged with its `HowRelatedNordigCS` role, with dimensions recorded at
upload. The feed side is then a few lines — `_still_images` in
`agenda/tvanytime/document.py` already emits a list, and
`_add_related_material` already takes the role and the size attributes.

Two smaller pieces belong to the same work:

- **Dimensions.** NorDig asks for `StillPictureFormat` with
  `horizontalSize`/`verticalSize`. We publish the media type but not the
  size, because nothing records thumbnail dimensions. They could go into
  `Video.media_metadata` at ingest time even before an image model exists.
- **Channel logo.** A single published URL in settings would let
  `ServiceInformation` carry term 19.2. It needs a stable, sized asset
  someone is willing to maintain, not just any copy of the logo.

### Measured broadcast times

The actual times we publish are the scheduled ones (see above), which is
honest for schedule-driven playout but is not a measurement.

`AsRun` — a playout log with `played_at`, `in_ms` and `out_ms` — looks like
the answer and is deliberately not used: it is not currently populated, and
it records *what played* rather than *which schedule item it was playing*,
with no foreign key between them. Matching on video and time proximity
would be guesswork dressed as data.

If measured times become worth having, the shape is a nullable
`AsRun.scheduleitem` foreign key set by playout as it logs, after which the
feed can report real start and end instants and a genuine `ActualDuration`.
That is a change to the playout side as much as to this repo.

### Smaller items

- **DVB service triplet.** `ServiceURL name="DTT"` should carry
  `dvb://<original_network_id>.<transport_stream_id>.<service_id>` for our
  DTT carriage, which lets a receiver match the EPG to the tuned service.
  Nobody had the triplet on file; add it to `TVA_LINEAR_SERVICE_URLS` in
  settings once someone does. A guessed one would point receivers at
  another broadcaster's service.
- **`ProductionDate` is really an upload date.** We do not record when a
  programme was made, only when we received it. For archive material the
  difference can be decades. A `production_year` field would fix it — and
  TV-Anytime accepts a bare year, so it need not be a full date. See the
  [proposals](tvanytime-model-proposals.md#videoproduction_year).
- **`ProductionLocation` is hardcoded `NO`.** Wrong for the members who
  film abroad. See the
  [proposals](tvanytime-model-proposals.md#videoproduction_country).
- **Audio and video attributes.** We publish `FrameRate` and nothing else:
  no picture format, aspect ratio, channel count or codec. Ingest already
  probes enough to know all of them — `VideoFile.integrated_lufs` proves
  it reads the streams. See the
  [proposals](tvanytime-model-proposals.md#3-persisting-what-ingest-already-knows).
- **Only one subtitle track per video, with no language of its own.** A
  constraint of ours rather than of the schema; it is why
  `CaptionLanguage` reuses the spoken language. See the
  [proposals](tvanytime-model-proposals.md#4-subtitles-a-structural-limit-worth-naming).

## Reference material

- ETSI TS 102 822-3-1 v1.11.2 (2019-06) — the TV-Anytime metadata schema
- NorDig TVA Implementation Guidelines v1.4 (June 2022), and the
  implementation package containing the terms list, the NorDig
  classification schemes and the example files:
  <https://nordig.org/specifications/>
- NorDig EPG/Event Metadata Exchange format specification v1.3
