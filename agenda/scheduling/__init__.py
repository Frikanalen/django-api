# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Filling the broadcast schedule.

When and how far ahead any of this happens is
:mod:`~agenda.scheduling.policy`'s single say: weeks are drafted two
Mondays before they air, open to member picks for one week, and frozen
from the Monday before airing.

Neither half of this runs on the web request path; both are invoked from
management commands, and thus from the nightly CronJobs, in this order:

1. :mod:`~agenda.scheduling.weekly_slots` places one video per WeeklySlot.
2. :mod:`~agenda.scheduling.jukebox` fills the airtime those slots leave over,
   choosing fillers by the weighted rules in
   :mod:`~agenda.scheduling.selection` -- which see the slots' placements,
   so an organization the slots favor gets its fillers downplayed.

The two entry points still disagree about what counts as airtime already
taken (the slot filler asks ``overlapping()``, the jukebox walks the
window minute-aligned). Import the entry point you want from its own
module rather than from this package, so it stays obvious which half you
are calling.
"""
