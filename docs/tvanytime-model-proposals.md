# Enriching the TV-Anytime feed: proposed model changes

What Frikanalen could publish in its [TV-Anytime feed](tvanytime.md) but
cannot today, and what each one would take.

Except for the series structure called out below, this remains a menu rather
than a plan.

**How to read an entry.** Each names the TV-Anytime element it would fill,
the model change it needs, and what it buys. Where a form has been checked
against the vendored ETSI schema that is stated explicitly — several of
these turned out to be more permissive than expected, which changes the
right field type.

**One principle throughout.** The feed omits rather than guesses, so an
absent field means an absent element. Adding a field is therefore only
worth it if somebody will actually fill it in. Half the proposals below
are really proposals about the members' upload form.

---

## 1. New fields on existing models

The cheap end: one migration each, no new tables.

### `Video.production_year`

**Fills:** `ProductionDate/TimePoint` — NorDig term *production year*.

Today the feed publishes `uploaded_time` as the production date, because
that is the only date we have. For archive material that can be decades
wrong: a 1975 union documentary uploaded in 2021 is published as a 2021
programme, and sorts that way in every guide.

```python
production_year = models.PositiveSmallIntegerField(
    blank=True,
    null=True,
    help_text="Year the programme was made, if different from when it was uploaded.",
)
```

**A year, not a date, and this is the interesting part.** `TimePoint` is an
`mpeg7:timePointType`, whose pattern accepts truncated values — `1975`,
`1975-06` and `1975-06-03` all validate (checked). So there is no schema
pressure to record a precision members do not have. A `DateField` would
make somebody invent a month and a day, and the feed would then publish
that invention as fact.

If exact dates are later wanted for recorded events, the field can become a
partial-date string validated against the same three shapes, since
`TimePoint` accepts all of them.

**Also worth deciding:** once this exists, the feed should probably stop
falling back to `uploaded_time` and simply omit `ProductionDate` when the
year is unknown. Publishing an upload date under a production label is the
kind of small untruth that is invisible until someone builds a decade
filter on it.

### `Video.production_country`

**Fills:** `ProductionLocation` — NorDig term *production country*.

Currently hardcoded `NO`. That is wrong for a meaningful slice of the
schedule: solidarity and aid organizations film abroad, and minority
organizations carry material from their communities of origin.

```python
production_country = models.CharField(
    max_length=2,
    blank=True,
    default="NO",
    help_text="ISO 3166-1 alpha-2 country the programme was produced in.",
)
```

Non-`NO` values validate (`GB` checked). A default of `NO` keeps the
present behaviour for everything nobody touches.

### `Video.sign_language`

**Fills:** `SignLanguage` — NorDig term *signlanguage*.

We publish nothing here today, and for a public-access channel that is a
conspicuous omission: deaf organizations are exactly the kind of member
Frikanalen exists for.

```python
sign_language = models.CharField(
    max_length=32,
    blank=True,
    help_text="Language tag of the sign language interpreted or used, e.g. 'no' "
    "for Norwegian Sign Language. Leave blank if there is none.",
)
```

`SignLanguageType` extends `language` with `primary`, `translation`, `type`
and `closed` attributes (`translation="true"` checked). Interpreted content
is a translation of the spoken dialogue; content *in* sign language is
primary. Whether that distinction is worth a second field depends on
whether members would understand it — probably start with one field and
emit `translation="true"`.

### Keywords

**Fills:** `Keyword` — NorDig term *keywords*.

Ten broad categories are all the subject metadata we have. `Keyword` takes
free text with `type="main|secondary|other"` (checked), and is what makes a
programme findable in a receiver's search rather than only in a genre list.

```python
keywords = ArrayField(models.CharField(max_length=64), blank=True, default=list)
```

`ArrayField` is fair game here — the codebase is already Postgres-only, with
`SearchVectorField`, `GinIndex` and range types in use. A `Tag` model would
be the alternative if keywords ever need to be curated or shared across
videos.

**A second reason to want this:** `Video.search_document` is a generated
column over name, header and description. Adding keywords to that vector
improves the site's own search at the same time, which makes the field
worth filling in for reasons members can see — the surest way to get
metadata actually entered.

---

## 2. Changes that need a new model

### Editorial imagery — key art and the rest

**Fills:** `RelatedMaterial` with the `HowRelatedNordigCS:2022` roles.

Covered at length in [tvanytime.md](tvanytime.md#imagery). The short
version: NorDig defines a dozen image roles — channel logo, show logo, show
still, episode still, key art with and without titling, behind the scenes,
portraits at three crops, cast ensemble — and we publish one auto-extracted
frame mapped to *show still*. Key art is what a modern EPG grid renders.

```python
class ProgramImage(models.Model):
    video = models.ForeignKey(Video, null=True, blank=True, related_name="images", ...)
    organization = models.ForeignKey(Organization, null=True, blank=True, ...)
    role = models.CharField(max_length=32, choices=ImageRole)  # HowRelatedNordigCS 19.x
    filename = models.CharField(max_length=256)
    width = models.PositiveSmallIntegerField()
    height = models.PositiveSmallIntegerField()
```

**Why not another `VideoFileVariant`.** Two reasons, and the first is a hard
block: `VideoFile` has a `unique_variant_per_video` constraint on
`(video, variant)`, so a video can hold exactly one file per variant —
one key art image, never a set at different crops. Second, images attach to
organizations (channel and network logos) and would attach to series, and
`VideoFile` cannot express either.

`role` should be a `TextChoices` mirroring the NorDig terms, in the same
style as `VideoFileVariant`, so the classification-scheme href is derived
rather than typed.

Recording `width`/`height` is what lets the feed emit `StillPictureFormat`
with `horizontalSize`/`verticalSize`, which is how a receiver picks the
right asset for its layout.

### Cast and crew

**Fills:** `CreditsList/CreditsItem` — NorDig term *cast and crew*.

Today every programme is credited to its owning organization as producer,
which is true but thin. TV-Anytime models a credit as a role plus a person
or organization, optionally with a character or a display label.

```python
class Credit(models.Model):
    video = models.ForeignKey(Video, related_name="credits", ...)
    role = models.CharField(max_length=32, choices=CreditRole)
    name = models.CharField(max_length=255)
    is_organization = models.BooleanField(default=False)
    index = models.PositiveSmallIntegerField(default=0)  # display order
```

Roles come from two schemes, and a public-access channel wants terms from
both:

| Scheme | Terms that fit |
| --- | --- |
| `urn:mpeg:mpeg7:cs:RoleCS:2011` | `PRODUCER`, `DIRECTOR`, `NARRATOR`, `INTERVIEWER`, `COMPOSER`, `CAMERA-OPERATOR`, `ACTOR` |
| `urn:tva:metadata:cs:TVARoleCS:2011` | `AD6` Presenter, `V43` Participant, `V96` Expert, `V97` Interviewed Guest, `V32` Commentator |

A curated `TextChoices` of maybe a dozen is far more likely to be filled in
correctly than either full scheme (RoleCS alone has hundreds of terms, most
of them film-crew grades).

**Worth raising before building this:** credits are personal data about
named individuals, published in a public B2B feed that distributors
redistribute. Members would need to be entering names of people who agreed
to be credited, and there should be a way to remove one. That is a policy
question, not a schema question, and it should be settled first.

The existing organization-as-producer credit stays as the fallback when a
video has no explicit credits.

### Series and seasons

**Fills:** `GroupInformationTable`, `MemberOf`, `EpisodeOf`.

**Implemented without seasons.** The `Series` model is owned by an
`Organization`; `Video.series` and `Video.episode_number` provide membership
and ordering. The feed emits `GroupInformation` and `EpisodeOf`, and both the
API and member pages support the editorial backfill. See
[tvanytime.md](tvanytime.md#series-and-episodes).

`Series.image_url` is reserved as read-only output for a future managed upload
flow. Managed upload and typed editorial imagery still belong to the image
proposal above. Seasons remain deferred until the extra hierarchy has a real
user; TV-Anytime can add each season as a `GroupInformation` member of the
existing series without changing today's series identifiers.

---

## 3. Persisting what ingest already knows

Ingest probes every upload — it records `integrated_lufs` and
`truepeak_lufs` on `VideoFile`, so it is already reading the streams. A few
more values from the same probe would fill in most of what TV-Anytime says
about how a programme looks and sounds.

### Video attributes

**Fills:** `AVAttributes/VideoAttributes`. We publish `FrameRate` and
nothing else.

| Element | Value |
| --- | --- |
| `HorizontalSize` / `VerticalSize` | pixel dimensions of the broadcast master |
| `AspectRatio` | `16:9` |
| `PictureFormat` | `PictureFormatCS:2015:1.1` SD, `1.2` HD, `1.4` UHD |

`PictureFormat` is the one receivers actually surface — an "HD" badge in
the guide. It is derivable from the dimensions, so it need not be stored.

### Audio attributes

**Fills:** `AVAttributes/AudioAttributes`.

`NumOfChannels` and `Coding` are straightforward. The interesting one is
`AudioLanguage` with a `purpose` from `AudioPurposeCS:2007`:

| Term | |
| --- | --- |
| 1 | Audio description for the visually impaired |
| 2 | Audio description for the hard of hearing |
| 6 | Main programme audio |

Term 1 is *synstolking*. If Frikanalen ever carries a described audio
track, this is how it reaches a viewer who needs it — and an accessibility
feature nobody can discover may as well not exist.

**Where to put it.** Dedicated nullable columns on `VideoFile` are easier
to query and validate than JSON. `Video.media_metadata` is an existing
`JSONField` that nothing currently reads, and would do if the shape is
still in flux.

### Measured broadcast times

**Fills:** real `ActualStartTime`/`ActualEndTime` rather than the scheduled
ones. See [tvanytime.md](tvanytime.md#measured-broadcast-times) — needs a
nullable `AsRun.scheduleitem` foreign key set by playout, which is a change
on the playout side as much as here.

---

## 4. Subtitles: a structural limit worth naming

Not a proposal so much as a constraint somebody should know about.

`VideoFile` has a unique constraint on `(video, variant)`, so a video can
carry **exactly one** `srt` file. Frikanalen therefore cannot offer
Norwegian and English subtitles on the same programme, and the feed cannot
publish more than one `CaptionLanguage`.

The subtitle file also has no language of its own, so the feed assumes the
subtitles are in the spoken language. That is wrong in the common case it
matters most: Norwegian subtitles on a foreign-language programme, which is
precisely the accessibility win a viewer is looking for. (`CaptionLanguage`
with a language different from `Language` validates fine — the limit is
ours, not the schema's.)

Either fix works:

- **`VideoFile.language`**, plus relaxing `unique_variant_per_video` to
  `(video, variant, language)`. Smaller change, but it loosens a constraint
  that other code relies on — `videofile_url()` and the thumbnail helpers
  all call `.get()` on the pair and would start raising
  `MultipleObjectsReturned`.
- **A `SubtitleTrack` model**, leaving `VideoFile` alone. More code, no
  risk to existing lookups.

The second is probably right, for the same reason `ProgramImage` should not
be a variant.

`CaptionLanguageType` also carries `supplemental`, which distinguishes
subtitles for the hard of hearing (with speaker labels and sound
description) from plain translation subtitles. Worth a boolean if the
distinction is ever recorded.

---

## 5. Wins that need no model change at all

Worth doing whenever somebody is next in this code.

- **`FirstShowing` / `LastShowing`.** TV-Anytime flags alongside `Repeat`,
  which the feed already computes from a first-broadcast aggregate.
  `FirstShowing` is the inverse of `Repeat`; `LastShowing` needs one more
  aggregate over future airtime. No new data.
- **Organization homepage as `RelatedMaterial`.** `Organization.homepage`
  exists and the feed ignores it. It could go on the programme as a
  "For more information" link (`HowRelatedCS:2012:10`), giving viewers a
  route to the organization behind a programme.
- **A brief synopsis.** `SynopsisLengthType` allows
  `brief|short|medium|long|extended`; we emit `short` and `long`. Some DVB
  carriage truncates hard, and a `brief` variant would control how.

### One thing to watch

`Video.header` is marked *"Retire, use description instead"* in the model.
It is currently the **short** synopsis, with `description` as the long one.
Retiring it without a replacement would silently drop the short synopsis
from the feed — and a short synopsis is what most EPG grids display, so the
visible result would be worse listings everywhere.

Whatever replaces `header` should keep two lengths, or the retirement
should include deriving a short synopsis from the long one.

---

## 6. Deliberately not proposed

| | Why not |
| --- | --- |
| `SegmentInformationTable`, `isSkippable` | Built for ad breaks and split transmissions. Frikanalen carries no advertising, and split programmes are rare enough to handle by hand if they arise. |
| `OtherIdentifier` for ISAN / EIDR | Registry identifiers for commercially distributed works. Member-produced content does not have them, and nothing would fill the field. |
| `ProgramReviewTable` | There are no reviews. |
| `RightsInformationTable`, device blocking | NorDig's rights model expresses platform and device restrictions. We impose none — not offering a programme on demand already says what needs saying. |
| `ContentAlertCS` warnings | `minimum_age` carries what Medietilsynet's scale gives us. Per-programme content warnings would need an editorial policy that does not exist, and inventing one in a schema field is the wrong order. |

---

## Reference material

Classification schemes cited above ship in the ETSI package
(`ts_1028220301v011102p0.zip`) and the NorDig implementation package; see
[tvanytime.md](tvanytime.md#reference-material). The three XSDs needed to
check a proposed form against the schema are vendored under
[`agenda/tvanytime/schemas/`](../agenda/tvanytime/schemas/).
