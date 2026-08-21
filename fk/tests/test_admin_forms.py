"""
The admin pages Django's own checks never look at.

`manage.py check` validates a ModelAdmin's own `fields` against its
model, but not an inline's: `admin.E035` and friends stop at the inline
class itself. So a column renamed by a migration can leave an inline
naming a field that no longer exists, and nothing says so until an
operator opens the page and gets a FieldError -- in production, on the
change page of every video. These tests build the same forms the change
and add pages build, so the next rename fails here instead.
"""

from datetime import timedelta

import pytest
from django.contrib import admin
from django.http import HttpRequest

from fk.admin import VideoAdmin
from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="admin-form-editor@example.test")
    organization = Organization.objects.create(name="Admin form org", editor=editor)
    video = Video.objects.create(
        name="Video with a file attached",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=5),
    )
    # A row for the inline to bind, so the test covers rendering an
    # existing file and not just the empty formset.
    VideoFile.objects.create(video=video, variant=VideoFileVariant.ORIGINAL, filename="upload.mp4")
    return video


@pytest.fixture
def operator_request(rf) -> HttpRequest:
    """A GET from a logged-in superuser, which is who the admin is for.
    Unsaved on purpose: the forms only ever ask it about permissions."""
    request = rf.get("/admin/fk/video/")
    request.user = User(email="admin-form-operator@example.test", is_superuser=True)
    return request


def test_the_change_page_builds_every_form_it_shows(video: Video, operator_request) -> None:
    video_admin = VideoAdmin(Video, admin.site)

    video_admin.get_form(operator_request, video, change=True)(instance=video)

    inline_fields = {
        name
        for formset_class, _inline in video_admin.get_formsets_with_inlines(operator_request, video)
        for name in formset_class.form.base_fields
        # Reached through the class rather than an instance: an inline's
        # form class is built by get_formset(), which is where an unknown
        # field name raises.
    }
    # What the file inline exists to edit. It was `format` -- a foreign
    # key to a lookup table -- until migration 0025 replaced it.
    assert {"variant", "filename"} <= inline_fields


def test_the_add_page_builds_every_form_it_shows(operator_request) -> None:
    video_admin = VideoAdmin(Video, admin.site)

    video_admin.get_form(operator_request, None)(instance=None)

    for formset_class, _inline in video_admin.get_formsets_with_inlines(operator_request, None):
        formset_class(instance=None)
