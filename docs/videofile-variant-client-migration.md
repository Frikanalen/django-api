# Client migration: `format` → `variant`

Instructions for updating one client of the Frikanalen API against a
breaking change. Written to be handed to an agent working in a client
repository, with no reference to the API repository's history.

Most clients touch one or two of these. An empty search result is a valid
answer — do not invent work.

## What changed

A video file used to reference a row in a `FileFormat` lookup table, and
the API exposed that row's integer primary key as `format`. The table is
gone: a file now carries the name of its kind directly, as a string from
a fixed set, in a field called `variant`.

The names themselves are unchanged. `format` was renamed because DRF
reserves `?format=` for content negotiation (so `?format=broadcast` could
never work as a filter), and because half the values never described a
format — `srt` is a subtitle track, `cloudflare_id` is not a file.

| Search for | Change to | Where |
| --- | --- | --- |
| `"format": 2` | `"variant": "broadcast"` | videofile payloads, read and write |
| `"fsname"` | `"variant"` | files nested in a schedule item |
| `?format__fsname=` | `?variant=` | videofile list filter |
| `jukebox_csv` | *(gone — see below)* | — |

## The valid values

`original`, `broadcast`, `vc1`, `theora`, `dash`, `webm_med`, `srt`,
`large_thumb`, `med_thumb`, `small_thumb`, `cloudflare_id`.

Every variant except the non-file `cloudflare_id` carries a MIME type.
`original` uses the generic `application/octet-stream` because its uploaded
type is unknown. `dash` is an MPEG-DASH manifest (`.mpd`), which needs a
player — it is not a source a bare `<video>` element can load.

## 1. Videofile payloads

`/api/videofiles` — list, detail, create, update; request and response.

```diff
-{ "id": 12, "video": 5, "format": 2, "filename": "master.mp4" }
+{ "id": 12, "video": 5, "variant": "broadcast", "filename": "master.mp4" }
```

Delete any name-to-id resolution rather than updating it: a hardcoded map
like `{"broadcast": 2}`, a lookup against a formats endpoint, a constant
named `FORMAT_ID`. Send the name.

## 2. Schedule payloads

`/api/scheduleitems` nests each video's files. The key was `fsname`.

```diff
-{ "id": 12, "fsname": "original", "filename": "master.mp4" }
+{ "id": 12, "variant": "original", "filename": "master.mp4" }
```

## 3. The list filter

```diff
-GET /api/videofiles?format__fsname=broadcast
+GET /api/videofiles?variant=broadcast
```

A name outside the set above is now `400`, where it used to return an
empty list. Code that relied on `[]` for an unknown name must handle the
error.

## 4. Errors to expect

Sending a bad value — an integer, an old name, or the old field — returns
`400` in the standard error envelope:

```json
{
  "type": "validation_error",
  "errors": [
    { "code": "invalid_choice", "detail": "\"2\" is not a valid choice.", "attr": "variant" }
  ]
}
```

Sending `format` instead of `variant` reports `{"code": "required",
"attr": "variant"}` — the unknown `format` key is ignored, so a client
that only half-migrates fails here rather than silently writing nothing.

## 5. The jukebox CSV feed is gone

`GET /api/jukebox_csv` returns `404` and no longer appears in the index at
`GET /api`. It listed filler videos with their broadcast file. The
nearest equivalent is `/api/videos?is_filler=true` plus each video's
`files.broadcast`, but it is not exact: the feed also required the video
to be properly imported, free of TONO records, and owned by a member
organization with a responsible editor. Ask before reimplementing those
rules client-side.

(The nightly job that fills airtime with fillers is a different thing
sharing the name. It is unaffected.)

## 6. Typed clients

The OpenAPI schema defines the values as one named component,
`VideoFileVariantEnum`, referenced by the videofile payloads, the
schedule-nested files, and the query parameter. Regenerate rather than
hand-writing a union:

```sh
curl -o schema.yaml https://<api-host>/api/schema/
```

If the client's types are hand-written, declare the union once and use it
at every site that accepts or returns a variant, so an unhandled value is
a compile error.

## What has *not* changed

Do not "fix" these:

- **Media URLs and paths** — still `<prefix>/<video-id>/<variant>/<filename>`.
- **The `files` map on a video payload** — always keyed by name, camel-cased
  by the renderer: `files.original`, `files.largeThumb`, `files.dash`. Each
  entry contains its `url` and a nullable `mimeType`.
- **`ogvUrl`, `largeThumbnailUrl`** and the thumbnail fallbacks.
- **Every other videofile field** — `id`, `video`, `filename`,
  `integratedLufs`, `truepeakLufs`, `createdTime`.

## Checklist

1. No hits remain for `format__fsname`, `fsname`, `jukebox_csv`, or any
   format-name-to-id map.
2. No request sends an integer where a variant belongs.
3. Fixtures, mocks and recorded HTTP responses updated — these are the
   usual stragglers, since they fail only at runtime.
4. Test suite passes; types regenerated if the client is generated.

## Rollout

There is no compatibility window. The field changes name and type in one
deploy; afterwards `format` is neither accepted nor returned. A client
sending `format` gets a `400`; one reading `format` gets `undefined`.
Ship client updates with the API release, not before it.
