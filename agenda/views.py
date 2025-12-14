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


def fill_with_jukebox(start=None, days=1):
    start = start or timezone.now()
    end = start + datetime.timedelta(days=days)

    candidates = Video.objects.fillers().order_by("?")

    jukebox_choices = _items_for_gap(start, end, candidates)
    for schedobj in jukebox_choices:
        video = schedobj["video"]
        item = Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_JUKEBOX,
            starttime=schedobj["starttime"],
            duration=video.duration,
        )
        item.save()

    return jukebox_choices


def ceil_minute(dt):
    return floor_minute(dt) + datetime.timedelta(minutes=1)


def floor_minute(dt):
    """Returns the datetime with seconds and microseconds cleared"""
    return dt.replace(second=0, microsecond=0)


def _items_for_gap(start, end, candidates):
    logger.info("Being asked to fill gap from {} to {}".format(start, end))
    # The smallest gap this function will try to fill
    MINIMUM_GAP_SECONDS = 300

    # Get a list of previously scheduled videos
    startdt, enddt = Scheduleitem.objects.expand_to_surrounding(start, end)
    already_scheduled = list(
        Scheduleitem.objects.filter(starttime__gte=startdt, starttime__lte=enddt).order_by(
            "starttime"
        )
    )

    start_of_gap = ceil_minute(start)
    end = floor_minute(end)

    pool = None
    full_items = []
    while True:
        end_of_gap = end

        # Get the first video already existing in schedule
        # that falls within the current time range
        if len(already_scheduled):
            extant_video = already_scheduled.pop(0)

            # Keep trying until we find one that ends
            # inside the range we are working with
            if extant_video.endtime() < start_of_gap:
                continue

            # If it doesn't begin until after the
            # end of our window, the window is
            # empty; otherwise this video is now
            # the end of our gap
            if extant_video.starttime > end:
                extant_video = None
            else:
                end_of_gap = floor_minute(extant_video.starttime)

        gap = (end_of_gap - start_of_gap).total_seconds()

        if gap > MINIMUM_GAP_SECONDS:
            (items, pool) = _fill_time_with_jukebox(
                start_of_gap, end_of_gap, candidates, current_pool=pool
            )
            full_items.extend(items)
        else:
            logging.info("Not filling %d second gap" % gap)

        if end_of_gap >= end:
            break

        start_of_gap = ceil_minute(extant_video.endtime())
    return full_items


def _fill_time_with_jukebox(start, end, videos, current_pool=None):
    current_time = start
    video_pool = current_pool or list(videos)
    logger.info("Filling jukebox from %s to %s - %d in pool" % (start, end, len(video_pool)))
    rejected_videos = []
    new_items = []

    def plist(l):
        return "[" + " ".join(str(v.id) for v in l) + "]"

    def next_vid(first=False):
        logger.debug(Video.objects.all())
        logger.debug(
            "next vid %s rej %s pool %s" % (first, plist(rejected_videos), plist(video_pool))
        )
        if len(video_pool) < len(videos) and first:
            video_pool.extend(list(videos))
        if len(rejected_videos):
            return rejected_videos.pop(0)
        if not len(video_pool):
            return None
        return video_pool.pop(0)

    while current_time < end:
        video = next_vid(True)
        new_rejects = []

        while current_time + video.duration > end:
            logger.debug("end overshoots time", current_time + video.duration)
            if video not in rejected_videos and video not in new_rejects:
                new_rejects.append(video)
            video = next_vid()
            logger.debug(
                "next vid is %s rejected %s new_rej %s",
                video,
                plist(rejected_videos),
                plist(new_rejects),
            )
            if not video:
                return new_items, rejected_videos + video_pool
        rejected_videos.extend(new_rejects)
        new_items.append({"id": video.id, "starttime": current_time, "video": video})
        logger.info("Added video %s at curr time %s", video.id, current_time.strftime("%H:%M:%S"))
        current_time = ceil_minute(current_time + video.duration)

    return new_items, rejected_videos + video_pool


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
