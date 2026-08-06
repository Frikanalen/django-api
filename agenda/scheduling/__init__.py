# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Filling the broadcast schedule.

Neither half of this runs on the web request path; both are invoked from
management commands, and thus from the nightly CronJobs, in this order:

1. :mod:`~agenda.scheduling.weekly_slots` places one video per WeeklySlot.
2. :mod:`~agenda.scheduling.jukebox` fills the airtime those slots leave over.

They share no code -- they disagree, in particular, about what counts as
airtime already taken. Import the entry point you want from its own module
rather than from this package, so it stays obvious which half you are calling.
"""
