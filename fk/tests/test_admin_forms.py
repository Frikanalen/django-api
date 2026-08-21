"""
The admin pages nothing else validates.

Django's admin checks never compare a `fields` or `fieldsets` entry
against the model -- not for a ModelAdmin, and not for an inline.
They cannot: `_check_field_spec_item` swallows FieldDoesNotExist and
returns no error, on the grounds that the name might legitimately be an
extra field declared on the form. So `manage.py check` stays silent
about an admin naming a column no longer there, and the FieldError --
raised when the form class is built and the name turns out to be neither
a model field nor a form field -- waits for the first operator to open
the page.

That is how VideoFileInline went on naming `format` after migration 0025
renamed the column to `variant`: every video change and add page raised,
while CI stayed green.

So these tests build the forms. The two below cover the Video page that
broke, down to the field names it has to offer; the sweep after them
covers every registered admin, which protects a page from the moment it
is registered rather than from the moment someone remembers to test it.
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


# Every ModelAdmin the site serves a page for. `_registry` is private,
# but it is the only way to ask the question -- the public API is
# get_model_admin(model), which answers only for a model you already
# thought to name. Reading the registry keeps this from being a
# hand-maintained list that the next register() call leaves stale.
REGISTERED_ADMINS = sorted(admin.site._registry.items(), key=lambda pair: pair[0]._meta.label)


@pytest.mark.parametrize(
    ("model", "model_admin"),
    REGISTERED_ADMINS,
    ids=[model._meta.label for model, _ in REGISTERED_ADMINS],
)
def test_every_registered_admin_builds_its_forms(model, model_admin, operator_request) -> None:
    """The same gap, swept across the site: no admin page may name a
    field that neither its model nor its form has -- in the admin's own
    fieldsets or in any of its inlines.

    Generically there is nothing to assert beyond "builds without
    raising", because what the right fields are is a different question
    for every page. But building is the step that was never happening,
    and this now runs for a model from the moment it is registered.
    """
    # An unsaved instance stands in for a saved one. It reaches the
    # change-page path -- change=True, `fieldsets` rather than
    # `add_fieldsets` -- without this test having to build a valid object
    # graph for every model on the site.
    for obj in (None, model()):
        form_class = model_admin.get_form(operator_request, obj, change=obj is not None)
        form_class(instance=obj)

        for formset_class, _inline in model_admin.get_formsets_with_inlines(operator_request, obj):
            formset_class(instance=obj)
