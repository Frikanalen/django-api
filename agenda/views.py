# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
import datetime
import logging

from django.conf import settings
from django.core.paginator import Paginator
from django.urls import reverse
from django.forms import ModelForm, ModelChoiceField
from django.http import HttpResponseForbidden, HttpResponse, HttpRequest
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from fk.models import Organization
from fk.models import Scheduleitem
from fk.models import Video
from fk.models import VideoFile
from fk.models import WeeklySlot


logger = logging.getLogger(__name__)


class ProgramguideView(TemplateView):
    """Simple Programguide

    It's quite slow.

    Improvement would be to give out days presorted as days to facilitate
    flowing formatting.
    """

    template_name = "agenda/events.html"
    title = "Program guide - this week"

    def get_context_data(self, **kwargs):
        context = super(ProgramguideView, self).get_context_data(**kwargs)

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


class ProgramplannerView(TemplateView):
    def get(self, request: HttpRequest, *_args, **_kwargs) -> HttpResponse:
        context = {
            #'events': events,
            "title": _("Schedule planner")
        }
        return render(request, "agenda/planner.html", context)


class ManageVideoList(TemplateView):
    template_name = "agenda/manage_video_list.html"
    VIDEOS_PER_PAGE = 20

    def get(self, request: HttpRequest, *_args, **_kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect("/login/?next=%s" % request.path)
        videos = Video.objects.filter(creator=request.user).order_by("name")

        paginator = Paginator(videos, self.VIDEOS_PER_PAGE)
        requested_page = request.GET.get("page")

        page = paginator.page(int(requested_page) if str(requested_page).isdigit() else 1)
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
            return redirect("/login/?next=%s" % request.path)
        initial = {}
        form = self.get_form(request, initial=initial, form=kwargs.get("form"))
        context = {"form": form, "title": _("New Video")}
        return render(request, "agenda/manage_video_new.html", context)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated or not request.user.is_superuser:
            return redirect("/login/?next=%s" % request.path)
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
        return self.get(request, form=form, *args, **kwargs)


def allowed_to_edit(video, user):
    return user.is_authenticated and (
        (video.organization and video.organization.members.filter(pk=user.id).exists())
        or user.is_superuser
    )


class ManageVideoEdit(AbstractVideoFormView):
    Form = VideoFormForUsers

    def get(self, request, id=None, form=None):
        if not request.user.is_authenticated:
            return redirect("/login/?next=%s" % request.path)
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
            return redirect("/login/?next=%s" % request.path)
        video = Video.objects.get(id=id)
        if not allowed_to_edit(video, request.user):
            return HttpResponseForbidden(
                _("You are not a member of the organization that owns this videos.")
            )
        form = self.get_form(request, data=request.POST, instance=video)
        if form.is_valid():
            form.save()
        return self.get(request, id=id, form=form)


def fill_weekly_slots():
    slots = WeeklySlot.objects.all()

    if len(slots) == 0:
        logger.warning("No WeeklySlots defined; exiting")
        return

    for slot in slots:
        if not slot.purpose:
            logger.info("No purpose connected, so nothing to fill")
            continue
        video = slot.purpose.single_video(slot.duration)
        if not video:
            logger.info("Couldn't get a video to use in slot!")
            continue
        next_datetime = slot.next_datetime()
        end_next_datetime = next_datetime + slot.duration

        if Scheduleitem.objects.filter(
            starttime__gte=next_datetime, starttime__lt=end_next_datetime
        ).exists():
            # Ouch we have already scheduled something in the slot
            logger.debug("Already something scheduled in this slot")
            continue
        item = Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_AUTO,
            starttime=next_datetime,
            duration=video.duration,
        )
        item.save()


def xmltv_home(request):
    """Information about the XMLTV schedule presentation."""
    now = timezone.now()
    today_url = reverse(
        "xmltv-feed", args=(now.year, "{:02}".format(now.month), "{:02}".format(now.day))
    )
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
    date = datetime.datetime(year=int(year), month=int(month), day=int(day)).replace(
        tzinfo=datetime.timezone.utc
    )
    events = Scheduleitem.objects.by_day(date, days=1).order_by("starttime")
    return _xmltv(request, events)
