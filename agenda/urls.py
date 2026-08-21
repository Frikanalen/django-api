# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.urls import re_path as url

from agenda import views

urlpatterns = [
    url(r"^xmltv/$", views.xmltv_home, name="xmltv-home"),
    url(r"^xmltv/upcoming/$", views.xmltv_upcoming, name="xmltv-feed-upcoming"),
    url(
        r"^xmltv/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/?$",
        views.xmltv_date,
        name="xmltv-feed",
    ),
]
