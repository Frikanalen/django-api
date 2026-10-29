import json
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from fk.models import StreamCredential

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def ensure(*args):
    out = StringIO()
    call_command("ensure_playout_credential", *args, stdout=out)
    return json.loads(out.getvalue())


def test_issues_a_playout_reader_on_first_run():
    result = ensure()
    assert result["changed"]
    credential = StreamCredential.objects.get()
    assert credential.action == StreamCredential.Action.PLAYOUT
    assert credential.source is None
    assert str(credential.key_id) == result["username"]
    assert credential.check_secret(result["password"])


def test_keeps_a_valid_credential_without_revealing_anything():
    first = ensure()
    again = ensure("--keep", first["username"])
    assert again == {"changed": False, "username": first["username"]}
    assert StreamCredential.objects.count() == 1


@pytest.mark.parametrize("state", ["revoked", "expired", "unknown"])
def test_replaces_a_credential_that_no_longer_works(state):
    first = ensure()
    credential = StreamCredential.objects.get()
    keep = first["username"]
    if state == "revoked":
        credential.revoked_at = timezone.now()
    elif state == "expired":
        credential.expires_at = timezone.now()
    else:
        keep = str(uuid4())
    credential.save()

    result = ensure("--keep", keep)

    assert result["changed"]
    assert result["username"] != first["username"]
    live = StreamCredential.objects.filter(revoked_at__isnull=True)
    assert [str(c.key_id) for c in live] == [result["username"]]


def test_leaves_hand_issued_playout_credentials_alone():
    manual = StreamCredential(action=StreamCredential.Action.PLAYOUT, label="Spare")
    manual.generate_secret()
    manual.save()
    ensure()
    ensure()
    manual.refresh_from_db()
    assert manual.revoked_at is None


def test_rejects_a_malformed_key():
    with pytest.raises(CommandError):
        ensure("--keep", "not-a-uuid")
