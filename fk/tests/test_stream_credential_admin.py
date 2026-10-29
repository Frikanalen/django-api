import pytest
from django.urls import reverse

from fk.models import LiveSource, Organization, StreamCredential, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def source():
    org = Organization.objects.create(name="Studio")
    return LiveSource.objects.create(name="Contribution", organization=org)


@pytest.fixture
def operator(client, settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    user = User.objects.create_user(email="stream-operator@example.test")
    user.is_superuser = True
    user.save()
    client.force_login(user)
    return client


def test_issue_displays_password_once_and_never_stores_it_in_session(operator, source):
    url = reverse("admin:fk_streamcredential_add")
    response = operator.post(url, {"source": source.pk, "label": "Studio OBS", "action": "publish"})
    assert response.status_code == 200
    secret = response.context["secret"]
    credential = StreamCredential.objects.get()
    assert credential.check_secret(secret)
    assert credential.secret_hash != secret
    assert secret.encode() in response.content
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert secret not in str(dict(operator.session))
    change_url = reverse("admin:fk_streamcredential_change", args=[credential.pk])
    for page in [change_url, reverse("admin:fk_streamcredential_changelist"), url]:
        later = operator.get(page)
        assert later.status_code == 200
        assert secret.encode() not in later.content
        assert credential.secret_hash.encode() not in later.content


def test_revoke_rejects_new_connections_and_cannot_be_undone_by_edit(operator, source):
    credential = StreamCredential(source=source, label="Encoder")
    secret = credential.generate_secret()
    credential.save()
    response = operator.post(
        reverse("admin:fk_streamcredential_changelist"),
        {
            "action": "revoke_credentials",
            "_selected_action": [credential.pk],
        },
    )
    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.revoked_at is not None
    revoked_at = credential.revoked_at
    response = operator.post(
        reverse("admin:fk_streamcredential_change", args=[credential.pk]),
        {
            "label": "Renamed",
            "expires_at_0": "",
            "expires_at_1": "",
            "revoked_at": "",
            "source": source.pk,
            "action": "read",
        },
    )
    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.label == "Renamed"
    assert credential.revoked_at == revoked_at
    assert credential.action == "publish"
    response = operator.post(
        reverse("api-mediamtx-auth"),
        {
            "user": str(credential.key_id),
            "password": secret,
            "action": "publish",
            "path": f"live/{source.stream_id}",
        },
        content_type="application/json",
    )
    assert response.status_code == 401


def test_non_admin_cannot_issue_credentials(client, source):
    user = User.objects.create_user(email="contributor@example.test")
    source.organization.members.add(user)
    client.force_login(user)
    response = client.post(
        reverse("admin:fk_streamcredential_add"),
        {
            "source": source.pk,
            "label": "Unauthorized",
            "action": "publish",
        },
    )
    assert response.status_code == 302
    assert not StreamCredential.objects.exists()
