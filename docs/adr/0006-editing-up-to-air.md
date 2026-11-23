# ADR 0006: Staff edit grants until they end, and late grants go on the schedule at once

- Date: 2026-11-16

## Status

Accepted. Decided in steps on 2026-11-11 (django-api #99), 2026-11-13 (#130, #132) and 2026-11-16 (#142, #144, #146, #148). In production since django 3.8.0. Amends [ADR 0001](0001-airtime-grants.md): its lifecycle ("staff still can until it's compiled") and the staff column of the permissions table.

## Context

Under ADR 0001 staff could edit a locked grant only until it was compiled. Right after the cut-over that froze the current and next week for everyone, staff included. Running a live station also needs changes in the moment: a live event overruns, a booking arrives at short notice, a programme on air has to come off. And a grant given or exempted late only reached the schedule at the next 00:05 run, sometimes after it should have aired.

## Decision

1. **Staff edit a grant's list in any state but cancelled, until the grant ends.** Saving a compiled grant recompiles it at once.
2. **What has started stays.** Rows that have aired, are on air or start within 30 seconds (`ON_AIR_MARGIN`) are kept untouched at the head of the list. Staff can take the programme on air off the air, which cuts its row at the current moment; the rest is laid out again from there.
3. **Staff move a grant's times.** The start can move until air. The end can be shortened while on air (dropping entries that no longer fit, or cutting the one on air), or lengthened into free time.
4. **Short-notice grants go on the schedule at once.** A grant created by staff or by approving a request, or a lock-exempt grant edited after its deadline, is filled from its recipe and compiled immediately instead of waiting for the nightly run. A grant created with a start in the past starts now.
5. **The database keeps a grant's schedule rows inside its window** (deferred constraint triggers), so no edit path can leave rows outside it.
6. **Members are unchanged**: they edit while the grant is open and before `locks_at`, or until air on a lock-exempt grant.

## Consequences

- The published schedule can change minutes before air on staff action. Playout follows changes pushed over `/api/playout/events`.
- Moving or cancelling a slot in its last seconds can still leave a moment of black; the frontend warns staff before changes under two minutes away.
- Staff edits after the lock bypass the week's notice the feeds otherwise get. That is accepted as an editorial decision, not a member right.
