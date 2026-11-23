# ADR 0003: Recipes replace fill rules

- Date: 2026-11-13

## Status

Accepted 2026-11-13 (django-api #121, #124; frontend #117). In production since django 3.2.0. Amends [ADR 0001](0001-airtime-grants.md): replaces its fill rules and playlists ("Automatic filling", "Deterministic filling", and the fill rule in "Jukebox slots and submitted videos").

## Context

ADR 0001 gave grants a `FillRule`: a pool, an ordering and a mode (one video or fill the window), plus `Playlist`. Two days in production showed three problems:

- **Too primitive for members.** All 22 migrated weekly-slot sources were "one video", so most slots aired one programme and graphics for the rest. Members had no sensible way to say "my newest episode of this series, then reruns".
- **Hand editing was all or nothing.** Saving a list cleared the fill rule, so a member who added one programme lost automatic filling for the rest of the window.
- **Parameters didn't fit the shape.** Each new need (a series, a jukebox theme) meant another nullable column or parameter on one generic rule.

The station also decided how automatic programming should behave: a member's slot airs the member's own content, never jukebox filler in the leftover time; only grants staff give the jukebox are jukebox; members set and forget, and the schedule may change until about a day before air.

## Decision

1. **A recipe is how a window programs itself.** `programming.Recipe` has an owner and a kind. What a kind needs lives in its own table with real columns and foreign keys, not JSON parameters:
   - `uploads`: everything the owner uploads, new first, then the least aired;
   - `series`: one of the owner's series (`SeriesSource`), its newest unaired episode first, then earlier ones;
   - `playlist`: a hand-picked list (`PlaylistSource`);
   - `jukebox`: the station's weighted draw, narrowed by an optional theme (`JukeboxSettings`: categories, length, age). See [ADR 0002](0002-jukebox-selection.md) for its pool.
2. **Recipes live in their own app, `programming`**, apart from the scheduling tables, so their data model can change without touching grants. Scheduling only points at a recipe.
3. **Recipes always fill the window.** There is no single-video mode and no choice of ordering; each kind has one.
4. **Recipes are values.** Choosing something else points the slot or grant at another recipe row; a recipe is never edited in place, so a change never leaks to another grant.
5. **The choice belongs to each broadcast.** `AirtimeGrant.recipe` is chosen per grant, and can optionally be made the choice for the grant's recurring slot (`wholeSlot`), which `RecurringSlot.recipe` copies onto every grant it issues.
6. **Pinned items.** Items the owner chooses are pinned at the head of the list, and the recipe fills the time they leave. Saving a list without asking for automatic filling makes the grant hand-made and clears its recipe; `POST /grants/{id}/automatic` unpins everything and lets the recipe program the whole window.
7. **Refill until a day before air.** The nightly run refills every automatic grant that is open, or locked or compiled and more than 24 hours from air (`REFRESH_UNTIL`), and recompiles a compiled grant whose answer changed. The lock still ends members' edits; it no longer freezes automatic picks.
8. **Recipes pick only imported videos** (with a file and a length). A placeholder without a file is something an owner pins by hand.

## Consequences

- An organization that does nothing gets a full window of its own content, and new uploads reach air within a day or two instead of after the lock.
- The feeds get a week's notice of who holds each window, but the exact automatic picks are only firm about a day ahead. ADR 0001's "list fixed at the lock" now holds only for hand-made lists.
- A new kind or setting is an ordinary model and migration in `programming`.
- Fill-rule and playlist endpoints are gone. Grants and slots read and write `recipe`; `PUT /grants/{id}/items` takes `automatic`, `recipe` and `wholeSlot`.
- Hash-ordered shuffling, per-grant seeds and the "least aired / latest / shuffled" choice from ADR 0001 were not built.

## Alternatives considered

- **Keep `FillRule` and add columns** (a series, a theme). Leaves every kind's settings on one table, mostly null.
- **JSON parameters on a recipe.** Rejected by the station: no constraints, no foreign keys, no migrations.
- **Jukebox in a member's leftover time.** Rejected: a member's slot airs only that member's content.
