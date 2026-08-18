# Migrating a client to `VideoFile.variant`

Instructions for updating one client repository against a breaking change
in the Frikanalen API. Hand this document to an agent working in that
repository; it is written to be actionable without reference to the API
repository's history.

Work through it in order. Every step names what to search for, so an
empty search result is a valid answer — most clients touch only one or
two of these.

## What changed, in one paragraph

A video file used to point at a row in a `FileFormat` lookup table, and
the API exposed that row's integer primary key as `format`. The table is
gone. A file now carries the name of its kind directly, as a string from
a fixed enum, and the field is called **`variant`**. It was renamed from
`format` because DRF reserves `?format=` for content negotiation (so
`?format=broadcast` could never work as a filter), and because half the
values never described a format: `srt` is a subtitle track and
`cloudflare_id` is not a file at all.

The values are unchanged. Anything that already worked in terms of the
names — media URLs, directory layout, the `files` map on a video — is
untouched.

## The valid values

| Value | Meaning |
| --- | --- |
| `original` | The file as uploaded |
| `broadcast` | Broadcast master |
| `vc1` | VC-1 |
| `theora` | Ogg Theora (`video/ogg`) |
| `dash` | MPEG-DASH manifest (`application/dash+xml`) |
| `srt` | SubRip subtitles |
| `large_thumb` | Large thumbnail |
| `med_thumb` | Medium thumbnail |
| `small_thumb` | Small thumbnail |
| `cloudflare_id` | Cloudflare Stream identifier |

## Step 1: the videofile payload

`/api/videofiles/` — list, detail, create and update, in both directions.

```diff
-{ "id": 12, "video": 5, "format": 2, "filename": "master.mp4" }
+{ "id": 12, "video": 5, "variant": "broadcast", "filename": "master.mp4" }
```

Search for: `format` near `videofile`, `videofiles`, `filename`.

Anything that resolved a name to an id before sending — a hardcoded map
like `{"broadcast": 2}`, a lookup against a formats endpoint, a constant
called `FORMAT_ID` — should be **deleted**, not updated. Send the name.

## Step 2: the schedule payload

`/api/scheduleitems/` nests a video's files, and the key there was
`fsname`. It is now `variant`, holding the same strings.

```diff
-{ "id": 12, "fsname": "original", "filename": "master.mp4" }
+{ "id": 12, "variant": "original", "filename": "master.mp4" }
```

Search for: `fsname`.

## Step 3: the query parameter

Filtering the videofile list by kind:

```diff
-GET /api/videofiles/?format__fsname=broadcast
+GET /api/videofiles/?variant=broadcast
```

A value outside the table above is now a `400`, where it used to be an
empty result set. If any code relies on an unknown name returning `[]`,
it needs to handle the error instead.

Search for: `format__fsname`.

## Step 4: the jukebox CSV feed is gone

`GET /api/jukebox_csv` has been removed and now returns `404`. It is also
gone from the endpoint index at `GET /api/`. Its rows were filler videos
with their broadcast file. The nearest replacement is
`/api/videos/?is_filler=true` plus each video's `files.broadcast`, but it
is not an exact one: the feed also required a video to be properly
imported, free of TONO records, and owned by a member organization with a
responsible editor. A client that needs those rules applied should say so
rather than reimplement them.

Search for: `jukebox_csv`, `jukebox-csv`.

This does **not** affect the nightly scheduling job that fills airtime
with fillers — that runs inside the API service and never had a client.

## Step 5: regenerate typed clients

The OpenAPI schema now defines the values as a named enum component,
`VideoFileVariantEnum`, shared by the videofile and schedule payloads and
by the query parameter. Regenerate from the live schema rather than
hand-writing a union:

```sh
curl -o schema.yaml https://<api-host>/api/schema/
```

Then re-run whatever generator the repository uses. If the client is
TypeScript and hand-written, declare the values as a union type and use
it everywhere a variant is accepted or returned, so an unhandled value is
a compile error rather than a runtime surprise.

## What has *not* changed

Do not "fix" these — they were already correct:

- **Media URLs and paths.** Still `<media-prefix>/<video-id>/<variant>/<filename>`.
- **The `files` map on a video payload.** Still keyed by variant name,
  camel-cased by the renderer: `files.largeThumb`, `files.original`,
  `files.dash`. This was never the integer id and needs no change.
- **`ogvUrl`, `largeThumbnailUrl`, and the thumbnail fallbacks.**
- **Every other field of a videofile**: `id`, `video`, `filename`,
  `integratedLufs`, `truepeakLufs`, `createdTime`.

## Verifying

1. Search the repository for each term above and confirm no hits remain:
   `format__fsname`, `fsname`, `jukebox_csv`, and any name-to-id mapping
   for file formats.
2. Confirm no request body or query string sends an integer where a
   variant belongs. An integer is now a `400` with an `invalid_choice`
   error code.
3. Run the repository's test suite, and update fixtures or recorded HTTP
   responses that still carry `"format": <int>` or `"fsname"`.

## Rollout

There is no compatibility window: the field changes name and type in the
same deploy, and the old `format` key is not accepted or returned
afterwards. A client that sends `format` gets a `400`; one that reads
`format` gets `undefined`. Update and deploy clients alongside the API
release, not before it.
