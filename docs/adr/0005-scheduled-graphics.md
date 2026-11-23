# ADR 0005: Staff can schedule HTML graphics in a grant

- Date: 2026-11-16

## Status

Accepted 2026-11-16 (django-api #149; playout 2.7.0; frontend 3.12.0; infographics 0.2.0). In production since django 3.9.0. Amends [ADR 0001](0001-airtime-grants.md): "Graphics are never schedule items" now holds only for the graphics playout shows in gaps.

## Context

ADR 0001 kept graphics out of the schedule: playout shows broadcast graphics wherever nothing is scheduled. Once the infographics existed (weather, trains, power, Stortinget), staff wanted to put a particular page on air for a chosen stretch, such as the Stortinget page during a sitting, rather than whatever playout shows by default in a gap.

## Decision

1. **A `Graphic` is an HTML page CasparCG loads**: a path on the site such as `/infographics/` (optionally holding one page) or a full address. Staff manage the list of graphics.
2. **Staff list a graphic in a grant with a duration of their choosing**, at least 5 seconds. Members can't list graphics.
3. **Scheduled graphics compile to `Scheduleitem` rows** with `graphic` set. Playout airs them like the gap graphics, with filler music under them.
4. **They aren't programmes.** Like posters, they follow straight after the entry before them, and the public schedule and EPG feeds leave them out (`include_fillers` shows them).

## Consequences

- Staff can plan long stretches of infographics deliberately, which ADR 0001 expected for off hours.
- The schedule now holds three kinds of rows besides historical ones: videos, posters and graphics. Consumers that only want programming filter on that.
- Playout's gap graphics are unchanged and still never appear in the schedule.
