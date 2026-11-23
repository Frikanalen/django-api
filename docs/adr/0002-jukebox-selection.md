# ADR 0002: The jukebox airs members' new videos, not the filler archive

- Date: 2026-11-23

## Status

Proposed. Describes the implementation in django-api 3.12.0 (#175, #176) as it stands. Changes the jukebox pool described in [ADR 0001](0001-airtime-grants.md#recipes).

## Context

The jukebox is the station's own programming. It fills the grants the station gives itself with a recipe of kind `jukebox` (see `programming/`). Unlike a member's grant, nobody picks these videos by hand, so the selection rule is an editorial policy: it decides which members are seen on the station's airtime, and how often.

Up to 3.11.0 the rule was:

- **Pool: `is_filler` videos only.** That flag is an opt-in set long ago. In practice almost only very old videos carry it. On staging data, 114 videos were in the pool, and a planned week's median pick was about eight years old. 1% of picks were from the last year, and three organizations made up the whole week.
- **Weighting: mild freshness.** A video's weight halved per year of age, with a floor of 0.2. That doesn't help when there is almost nothing new in the pool. Even with new videos in it, a large archive at weight 0.2 outweighs a handful of new ones.

So members who upload today were not seen on the jukebox. The station's own airtime mostly showed what was uploaded a decade ago.

## Decision

The jukebox programmes what members are making now. New uploads get a guaranteed share, and the archive fills the rest.

1. **Pool: every member video that may air and is meant to be seen.** No `is_filler` requirement. A video is in the pool when:
   - its organization is a member (`fkmember`), has an active responsible editor, and is not suspended (`is_rogue`);
   - staff have not removed it (`is_banned`);
   - its file is processed (`proper_import`) and it has a length;
   - it is published on the website (`publish_on_web`);
   - it has no TONO-registered music (`has_tono_records`).

   A jukebox theme (`JukeboxSettings`: categories, length, age) narrows the pool as before.
2. **New uploads air about twice a week for two weeks.** A video uploaded within 14 days of a window's start (`NEW_FOR`) is *due* in that window if it doesn't air within 3 days either side of it (`REPEAT_GAP`). That counts past airings, the compiled schedule, other grants (members' too) and the picks the run has made so far. Each window takes due videos first, the fewest-aired and newest leading. New videos air **only** on this quota, so they come round about every three days (2–3 times a week), not whenever the draw favours them.
3. **Steep freshness for everything else.** The weighted draw fills the rest of the window from material older than two weeks. Weight halves every 90 days (was 365), with a floor of 0.01 (was 0.2), so last season's videos clearly lead the archive. The existing rules stay: no organization dominates a day (`OrganizationDiversity`), and no video twice in a row, with each earlier play halving its weight (`RepeatAvoidance`).

On staging data, with seven planned 3-hour windows:

| | Before (3.11.0) | After (3.12.0, without the quota) |
|---|---|---|
| Videos in the pool | 114 | 1035 |
| Median age of picks | about 8 years | 158 days |
| Picks from the last 90 days | 0% | 47% |
| Picks from the last year | 1% | 64% |
| Organizations in the week | 3 | 6 |

Staging had only one upload from the last two weeks. It aired on days 0, 3 and 6 of a planned week.

## Consequences

- **Members are seen on station airtime without asking.** Publishing a video is now enough to be in the jukebox. That is the point of the change, but it is a change of policy: before, being in the jukebox was opt-in (`is_filler`).
- **No way to opt out of the jukebox alone.** A member who doesn't want a video in the jukebox can only hide it from the website. Staff can remove a video (`is_banned`), but that takes it off everything. See [Open questions](#open-questions).
- **`is_filler` no longer does anything** for the jukebox. It is kept on `Video` and in the admin for now.
- **Long videos can enter the jukebox.** Before, the opt-in kept the pool to filler-length material. A theme with `longest` set limits this.
- **Quota capacity is bounded by jukebox time.** The quota acts only inside jukebox grants. In a week with little jukebox airtime and many new uploads, some new videos will air less than twice. Nothing reports this today.
- **More repeats.** New videos come round every three days by design, and steep freshness concentrates the draw on the newer part of the archive. On staging: 59 different videos in 99 picks.
- **When it takes effect.** Automatic grants refill nightly at 00:05 until 24 hours before air, so the next day's jukebox keeps its earlier picks after a release.
- **Members' own slots are unchanged.** They still air the member's own content (uploads, series or playlist recipes); only the station's jukebox grants follow this policy.

## Alternatives considered

- **Keep `is_filler` and ask members to opt in.** Fair to members, but the flag is rarely set. The jukebox would have kept airing the archive until members acted, and most never would.
- **Steeper freshness only, no quota.** Simpler, but a weighted draw can't promise a new video airs at all in a given week. And among few new videos it tends to air the same ones every day.
- **A per-video selection model** (for example `VideoSelection(video, jukebox: allow/prefer/exclude, weight, not_before, not_after)`, one-to-one with `Video`). It lets members and staff steer single videos instead of a global flag. Left as a follow-up: the urgent problem was the pool, not per-video control.

## Open questions

- Should members be able to keep a published video out of the jukebox (an opt-out), and should that come with the per-video selection model above?
- Should members be told, in Studio or in the terms, that published videos can air in the station's jukebox?
- Are 14 days and twice a week the right numbers, and should they be settings staff can change instead of constants?
- Should staff see when the quota can't be met (too many new videos for the jukebox time)?
