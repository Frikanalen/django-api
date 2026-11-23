# ADR 0007: A live programme is a video with a live transmission

- Date: 2026-11-11

## Status

Accepted 2026-11-11 (django-api #101; playout #28; frontend #97). In production since django 3.0.0. Amends [ADR 0001](0001-airtime-grants.md): replaces `GrantItem.live_transmission` and the plan that schedule rows point to a live transmission.

## Context

ADR 0001 let a grant item be a video or a live transmission. The first implementation (#100) gave grant items a live source, a length and a title of their own, compiling to a live schedule row with no video. That made a live programme a second kind of thing with its own title, description and page, and left nothing for the recording to become afterwards.

## Decision

1. **A video owns its live transmission.** A live programme is a `Video` with a `LiveTransmission` (its live source) and a duration set by hand. It is listed in a grant like any other video.
2. **It airs live until it has a file.** While the video has no processed file, the compiled row airs from the live source; once a recording is uploaded, the same video airs as a file.
3. **No backward-compatible schedule API.** Grant items with their own live fields were removed in the same change, and playout was updated in step.

## Consequences

- One entry per programme: title, description, page and later recording are the same video.
- `GrantItem` holds a video, a poster or a graphic, never a live transmission directly.
- `Scheduleitem.is_live` is still a stored field rather than derived from the video, as ADR 0001's cleanup suggested.
