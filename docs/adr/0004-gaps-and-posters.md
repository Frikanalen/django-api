# ADR 0004: Programmes start on a five-minute grid, and grants can list text posters

- Date: 2026-11-14

## Status

Accepted 2026-11-14 (django-api #137; playout #34; on-air-graphics #41; frontend #134). In production since django 3.4.0. Amends [ADR 0001](0001-airtime-grants.md): items no longer play back-to-back, and a grant's list is no longer only videos and live transmissions.

## Context

ADR 0001 played a grant's items back-to-back from its start. On air, one programme ran straight into the next with no moment of graphics between them, and start times like 21:47:13 were useless in a printed schedule. Members also had no way to put a message on screen in their own airtime, such as "next week: part two" or a notice, without producing a video.

## Decision

1. **Programmes start on the five-minute grid.** Each video starts at the next five-minute wall-clock mark that is at least 30 seconds (`BREATHER`) after the previous programme ends. The first entry starts with the grant. The rule lives in `scheduling/layout.py`, and the frontend mirrors it.
2. **Jukebox grants are not padded.** They are a stream of short fillers, not a schedule of programmes.
3. **Text posters.** An organization makes a `Poster` from an on-air graphics template (`standard` or `notice`): a heading and text, where a blank line starts a new slide. It is listed in a grant like a video and airs for its own duration, which must be at least the time it takes to read every slide once. Light or dark follows the time of day at air, not a choice.
4. **Posters aren't programmes.** A poster plays straight after whatever comes before it, so it can fill the gap a programme leaves; the next programme still starts on the grid. Posters compile to `Scheduleitem` rows with `poster` set, and the public schedule and EPG feeds leave them out.
5. A `GrantItem` is exactly one of a video or a poster (and, from [ADR 0005](0005-scheduled-graphics.md), a graphic).

## Consequences

- Viewers get a short break between programmes, and the schedule shows round times.
- A window holds less programme than its length: two 30-minute programmes no longer fit an hour.
- Playout shows posters on their own layers via the graphics page (`?poster=…`).
- The layout rule exists in two places (`layout.py` and the frontend's `grants.ts`) and must be kept in step.
