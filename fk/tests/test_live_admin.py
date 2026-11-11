from datetime import timedelta

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking():
    user = User.objects.create_user(email="live-admin@example.test")
    user.is_superuser = True
    user.save()
    org = Organization.objects.create(name="Studio", editor=user)
    video = Video.objects.create(
        name="Live programme", creator=user, organization=org, duration=timedelta(hours=1)
    )
    source = LiveSource.objects.create(name="Studio input", organization=org)
    entry = Scheduleitem.objects.create(
        video=video,
        starttime=timezone.now() + timedelta(days=2),
        duration=timedelta(hours=1),
        schedulereason=Scheduleitem.REASON_ADMIN,
    )
    return user, source, entry


@pytest.mark.parametrize("problem", ["disabled", "other_org"])
def test_a_live_transmission_needs_an_enabled_source_of_the_videos_own(booking, problem):
    _, source, entry = booking
    if problem == "disabled":
        source.enabled = False
    else:
        source.organization = Organization.objects.create(name="Another organization")
    source.save()

    with pytest.raises(ValidationError):
        LiveTransmission(video=entry.video, source=source).full_clean()


def test_video_admin_makes_a_video_live(client, booking):
    user, _, entry = booking
    client.force_login(user)
    video = entry.video
    url = reverse("admin:fk_video_change", args=[video.pk])
    page = client.get(url)
    assert page.status_code == 200
    assert "live_transmission-TOTAL_FORMS" in page.content.decode()


def test_schedule_admin_derives_live_from_the_video(client, booking):
    user, source, entry = booking
    LiveTransmission.objects.create(video=entry.video, source=source)
    client.force_login(user)
    local_start = timezone.localtime(entry.starttime)
    data = {
        "video": entry.video_id,
        "starttime_0": local_start.strftime("%Y-%m-%d"),
        "starttime_1": local_start.strftime("%H:%M:%S"),
        "duration": "01:00:00",
    }
    url = reverse("admin:fk_scheduleitem_change", args=[entry.pk])
    assert client.post(url, data).status_code == 302
    entry.refresh_from_db()
    assert entry.is_live

    Video.objects.filter(pk=entry.video_id).update(proper_import=True)
    assert client.post(url, data).status_code == 302
    entry.refresh_from_db()
    assert not entry.is_live


def test_source_admin_and_schedule_add_page_render(client, booking):
    user, source, _ = booking
    client.force_login(user)
    source_admin = admin.site._registry[LiveSource]
    assert "stream_id" in source_admin.readonly_fields
    for url in [
        reverse("admin:fk_livesource_add"),
        reverse("admin:fk_livesource_change", args=[source.pk]),
        reverse("admin:fk_scheduleitem_add"),
    ]:
        assert client.get(url).status_code == 200


def test_schedule_supports_creating_video_in_admin_popup(client, booking):
    user, _, entry = booking
    client.force_login(user)
    response = client.get(reverse("admin:fk_scheduleitem_change", args=[entry.pk]))
    video_widget = response.context["adminform"].form.fields["video"].widget
    assert video_widget.can_add_related
    assert client.get(reverse("admin:fk_video_add"), {"_popup": "1"}).status_code == 200
