# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.

from django.conf.urls import include
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import URLPattern, URLResolver
from django.urls import re_path as url

import agenda.urls
import api.urls
from api.auth.two_factor import AdminAuthenticationWithSecondFactorForm

admin.autodiscover()
# The admin login asks accounts with two-factor on for their code too.
admin.site.login_form = AdminAuthenticationWithSecondFactorForm
admin.site.login_template = "admin/login_otp.html"

urlpatterns: list[URLPattern | URLResolver] = [
    url(r"^admin/", admin.site.urls),
    url(r"^", include("django_prometheus.urls")),
]

urlpatterns += agenda.urls.urlpatterns
urlpatterns += api.urls.urlpatterns
urlpatterns += [
    url(
        r"^api/news/",
        include(
            (
                "news.urls",
                "news",
            )
        ),
    )
]

# Only used with DEBUG. Serves static content right from source
urlpatterns += staticfiles_urlpatterns()
