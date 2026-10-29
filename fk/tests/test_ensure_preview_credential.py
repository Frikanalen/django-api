import json
from io import StringIO

import pytest
from django.core.management import call_command

from fk.models import StreamCredential

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def ensure(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out)
    return json.loads(out.getvalue())


def test_preview_converter_is_provisioned_apart_from_playout():
    playout = ensure("ensure_playout_credential")
    preview = ensure("ensure_preview_credential")
    credential = StreamCredential.objects.get(key_id=preview["username"])
    assert credential.action == StreamCredential.Action.PREVIEW
    assert credential.source is None
    assert credential.check_secret(preview["password"])

    # Each keeps its own credential: provisioning one never revokes the other.
    assert ensure("ensure_preview_credential", "--keep", preview["username"])["changed"] is False
    assert ensure("ensure_playout_credential", "--keep", playout["username"])["changed"] is False
