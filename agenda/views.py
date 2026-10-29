# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
import datetime

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from fk.models import Scheduleitem


def xmltv_home(request):
    """Information about the XMLTV schedule presentation."""
    now = timezone.now()
    today_url = reverse("xmltv-feed", args=(now.year, f"{now.month:02}", f"{now.day:02}"))
    return render(
        request,
        "agenda/xmltv_home.html",
        {
            "channel_display_names": settings.CHANNEL_DISPLAY_NAMES,
            "today_url": today_url,
            "site_url": settings.SITE_URL,
        },
    )


def _xmltv(request, events):
    """Program guide as XMLTV."""
    return render(
        request,
        "agenda/xmltv.xml",
        {
            "channel_id": settings.CHANNEL_ID,
            "channel_display_names": settings.CHANNEL_DISPLAY_NAMES,
            "events": events,
            "site_url": settings.SITE_URL,
        },
        content_type="application/xml",
    )


def xmltv_upcoming(request):
    events = Scheduleitem.objects.by_day(days=7).order_by("starttime")
    return _xmltv(request, events)


def xmltv_date(request, year, month, day):
    date = datetime.datetime(year=int(year), month=int(month), day=int(day), tzinfo=datetime.UTC)
    events = Scheduleitem.objects.by_day(date, days=1).order_by("starttime")
    return _xmltv(request, events)
