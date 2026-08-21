# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
import datetime

from django.conf import settings
from django.core.paginator import Paginator
from django.forms import ModelChoiceField, ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from fk.models import Organization, Scheduleitem, User, Video, VideoFile


class ProgramguideView(TemplateView):
    """Simple Programguide

    It's quite slow.

    Improvement would be to give out days presorted as days to facilitate
    flowing formatting.
    """

    template_name = "agenda/events.html"
    title = "Program guide - this week"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if "date" in self.request.GET:
            starttime = parse_datetime(self.request.GET["date"] + " 00:00")
        else:
            starttime = timezone.now()
        events = Scheduleitem.objects.by_day(starttime.date(), days=7).order_by("starttime")
        context.update(
            events=events,
            starttime=starttime,
            title=self.title,
        )
        return context


class ProgramguideCalendarView(ProgramguideView):
    template_name = "agenda/calendar.html"
    title = _("Calendar - this week")


class ManageVideoList(TemplateView):
    template_name = "agenda/manage_video_list.html"
    VIDEOS_PER_PAGE = 20

    def get(self, request: HttpRequest, *_args, **_kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        videos = Video.objects.filter(creator=request.user).order_by("name")

        paginator = Paginator(videos, self.VIDEOS_PER_PAGE)
        # get_page() absorbs whatever the query string carries: a missing
        # or unparseable ?page= gives the first page, one past the end
        # gives the last. page() raises on all of those, which is a 500
        # for a URL a user can type.
        page = paginator.get_page(request.GET.get("page"))
        context = {"title": _("My videos"), "videos": page.object_list, "page": page}

        return render(
            request,
            self.template_name,
            context,
        )


class VideoFormForUsers(ModelForm):
    class Meta:
        model = Video
        fields = (
            "name",
            "categories",
            "organization",
            "has_tono_records",
            "is_filler",
            "publish_on_web",
            "header",
            "ref_url",
            "duration",
        )


class VideoFormForAdmin(ModelForm):
    class Meta:
        model = Video
        fields = (
            "name",
            "categories",
            "creator",
            "organization",
            "has_tono_records",
            "is_filler",
            "publish_on_web",
            "header",
            "ref_url",
            "duration",
        )


class AbstractVideoFormView(TemplateView):
    UserForm = VideoFormForUsers
    AdminForm = VideoFormForAdmin

    def get_form(self, request, data=None, initial=None, form=None, instance=None):
        # I suspect this stuff should be moved to the VideoForm-class
        if initial is None:
            initial = {}
        organizations = Organization.objects.filter(members=request.user.id)
        if not form:
            if request.user.is_superuser:
                form_class = self.AdminForm
                initial["creator"] = request.user.id
            else:
                form_class = self.UserForm

            if not instance:
                if organizations:
                    initial["organization"] = organizations[0].id
                initial["publish_on_web"] = True

                # Request manual intervention before the video end in rotation
                initial["is_filler"] = False
                form = form_class(initial=initial)
            else:
                form = form_class(data, instance=instance)

        if not request.user.is_superuser:
            org_field = form.fields["organization"]
            if "organization" in form.fields and isinstance(org_field, ModelChoiceField):
                org_field.queryset = organizations

        return form


class ManageVideoNew(AbstractVideoFormView):
    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated or not request.user.is_superuser:
            return redirect(f"/login/?next={request.path}")
        form = self.get_form(request, initial={}, form=kwargs.get("form"))
        context = {"form": form, "title": _("New Video")}
        return render(request, "agenda/manage_video_new.html", context)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated or not request.user.is_superuser:
            return redirect(f"/login/?next={request.path}")
        if request.user.is_superuser:
            video = Video()
        else:
            video = Video(creator=request.user)
        # Since this is not an import we set this to True
        video.proper_import = True

        form = self.get_form(request, data=request.POST, instance=video)
        if form.is_valid():
            video = form.save()
            # Success, send to edit page
            return redirect("manage-video-edit", video.id)
        return self.get(request, *args, form=form, **kwargs)


def allowed_to_edit(video: Video, user: User) -> bool:
    """Who may edit a video through the members' pages: staff, and the
    members of the organization that owns it.

    Narrower than the API's can_administer_organization(), which also
    admits the organization's editor. Nothing chose that difference --
    the two checks were written apart -- and it is pinned by
    test_the_organizations_editor_is_not_admitted so that closing it is
    a decision rather than an accident.
    """
    if not user.is_authenticated:
        return False

    if user.is_staff:
        return True
    # Asked from the user's side so the organization row itself never
    # has to be fetched -- the video already carries its id.
    return user.organization_set.filter(pk=video.organization_id).exists()


class ManageVideoEdit(AbstractVideoFormView):
    Form = VideoFormForUsers

    def get(self, request, id=None, form=None):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        video = Video.objects.get(id=id)
        if not allowed_to_edit(video, request.user):
            return HttpResponseForbidden(
                _("You are not a member of the organization that owns this videos.")
            )
        form = self.get_form(request, form=form, instance=video)
        videofiles = VideoFile.objects.filter(video=video)
        context = {"form": form, "videofiles": videofiles, "title": _("Edit video")}
        return render(request, "agenda/manage_video_new.html", context)

    def post(self, request, id):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        video = Video.objects.get(id=id)
        if not allowed_to_edit(video, request.user):
            return HttpResponseForbidden(
                _("You are not a member of the organization that owns this videos.")
            )
        form = self.get_form(request, data=request.POST, instance=video)
        if form.is_valid():
            form.save()
        return self.get(request, id=id, form=form)


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
    """Program guide as XMLTV"""

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
