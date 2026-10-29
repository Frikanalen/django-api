from datetime import timedelta

import pytest
from django.contrib import admin
from django.forms.models import inlineformset_factory
from django.urls import reverse
from django.utils import timezone

from fk.admin import LiveTransmissionFormSet
from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking():
    user = User.objects.create_user(email="live-admin@example.test")
    user.is_superuser = True
    user.save()
    org = Organization.objects.create(name="Studio", editor=user)
    video = Video.objects.create(name="Live programme", creator=user, organization=org)
    source = LiveSource.objects.create(name="Studio input", organization=org)
    entry = Scheduleitem.objects.create(
        video=video,
        starttime=timezone.now() + timedelta(days=2),
        duration=timedelta(hours=1),
        schedulereason=Scheduleitem.REASON_ADMIN,
    )
    return user, source, entry


def inline_data(source, **extra):
    return {
        "live_transmission-TOTAL_FORMS": "1",
        "live_transmission-INITIAL_FORMS": "0",
        "live_transmission-0-source": str(source.pk),
        **extra,
    }


def formset(entry, data):
    factory = inlineformset_factory(
        Scheduleitem,
        LiveTransmission,
        formset=LiveTransmissionFormSet,
        fields=("source",),
        extra=0,
    )
    return factory(data=data, instance=entry)


@pytest.mark.parametrize("problem", ["missing_video", "disabled", "other_org"])
def test_inline_rejects_invalid_booking(booking, problem):
    _, source, entry = booking
    if problem == "missing_video":
        entry.video = None
    elif problem == "disabled":
        source.enabled = False
        source.save()
    else:
        source.organization = Organization.objects.create(name="Another organization")
        source.save()
    forms = formset(entry, inline_data(source))
    assert not forms.is_valid()
    assert forms.non_form_errors() or forms.errors[0].get("source")


def test_empty_or_deleted_inline_does_not_validate_live_policy(booking):
    _, source, entry = booking
    entry.video = None
    assert formset(entry, inline_data(source, **{"live_transmission-0-source": ""})).is_valid()
    assert formset(entry, inline_data(source, **{"live_transmission-0-DELETE": "on"})).is_valid()


def test_admin_books_live_and_removes_attachment(client, booking):
    user, source, entry = booking
    client.force_login(user)
    local_start = timezone.localtime(entry.starttime)
    data = {
        "video": entry.video_id,
        "schedulereason": entry.schedulereason,
        "starttime_0": local_start.strftime("%Y-%m-%d"),
        "starttime_1": local_start.strftime("%H:%M:%S"),
        "duration": "01:00:00",
        **inline_data(source),
    }
    url = reverse("admin:fk_scheduleitem_change", args=[entry.pk])
    response = client.post(url, data)
    assert response.status_code == 302
    transmission = LiveTransmission.objects.get(schedule_item=entry)
    entry.refresh_from_db()
    assert entry.is_live
    assert not entry.video.proper_import

    data.update(
        {
            "live_transmission-INITIAL_FORMS": "1",
            "live_transmission-0-id": transmission.pk,
            "live_transmission-0-DELETE": "on",
        }
    )
    assert client.post(url, data).status_code == 302
    entry.refresh_from_db()
    assert not entry.is_live
    assert not LiveTransmission.objects.filter(schedule_item=entry).exists()


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
