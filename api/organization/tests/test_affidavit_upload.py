import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.organization import affidavit
from fk.models import AffidavitStatus, AffidavitSubmission, Organization, User

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n% a signed affidavit\n"


@pytest.fixture(autouse=True)
def mail_inline(monkeypatch):
    monkeypatch.setattr(affidavit, "run_in_background", lambda f, *a, **kw: f(*a, **kw))


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="staff@example.test", is_superuser=True)


@pytest.fixture
def applicant(editor: User) -> Organization:
    return Organization.objects.create(name="Søkerlaget", orgnr="971526920", editor=editor)


def url(organization: Organization) -> str:
    return reverse("api-organization-affidavit", args=[organization.pk])


def upload(client: APIClient, organization: Organization, content=PDF, name="erklaering.pdf"):
    return client.post(
        url(organization), {"file": SimpleUploadedFile(name, content)}, format="multipart"
    )


def test_the_editor_uploads_a_scan_and_staff_hear_of_it(
    editor_client: APIClient, applicant: Organization, staff: User
) -> None:
    response = upload(editor_client, applicant)

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["status"] == "pending"
    submission = AffidavitSubmission.objects.get()
    assert bytes(submission.content) == PDF
    assert submission.content_type == "application/pdf"
    assert [m.to for m in mail.outbox] == [[staff.email]]
    assert f"/admin/fk/affidavitsubmission/{submission.pk}/change/" in mail.outbox[0].body


def test_a_new_upload_replaces_the_one_still_waiting(
    editor_client: APIClient, applicant: Organization
) -> None:
    upload(editor_client, applicant)
    upload(editor_client, applicant, name="ny.pdf")

    assert list(AffidavitSubmission.objects.values_list("file_name", flat=True)) == ["ny.pdf"]


def test_members_see_the_status_but_only_the_editor_uploads(
    applicant: Organization,
) -> None:
    member = User.objects.create(email="member@example.test")
    applicant.members.add(member)
    client = APIClient()
    client.force_authenticate(member)

    assert client.get(url(applicant)).status_code == status.HTTP_200_OK
    assert upload(client, applicant).status_code == status.HTTP_403_FORBIDDEN


def test_outsiders_see_nothing(applicant: Organization) -> None:
    client = APIClient()
    client.force_authenticate(User.objects.create(email="outsider@example.test"))

    assert client.get(url(applicant)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize("content", [b"<html><script>alert(1)</script>", b"GIF89a..."])
def test_only_pdf_jpeg_and_png_are_taken(
    editor_client: APIClient, applicant: Organization, content: bytes
) -> None:
    response = upload(editor_client, applicant, content=content, name="scan.pdf")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not AffidavitSubmission.objects.exists()


def test_too_large_a_scan_is_refused(
    editor_client: APIClient, applicant: Organization, monkeypatch
) -> None:
    monkeypatch.setattr(affidavit, "AFFIDAVIT_MAX_BYTES", 10)

    assert upload(editor_client, applicant).status_code == status.HTTP_400_BAD_REQUEST


def test_a_member_organization_needs_no_scan(
    editor_client: APIClient, organization: Organization
) -> None:
    assert upload(editor_client, organization).status_code == status.HTTP_400_BAD_REQUEST


def test_only_staff_fetch_the_scan(
    editor_client: APIClient, applicant: Organization, staff: User
) -> None:
    upload(editor_client, applicant)
    submission = AffidavitSubmission.objects.get()
    file_url = reverse("api-organization-affidavit-file", args=[applicant.pk, submission.pk])

    assert editor_client.get(file_url).status_code == status.HTTP_403_FORBIDDEN
    staff_client = APIClient()
    staff_client.force_authenticate(staff)
    response = staff_client.get(file_url)
    assert response.status_code == status.HTTP_200_OK
    assert response.content == PDF
    assert response["Content-Security-Policy"] == "sandbox"


def test_approving_makes_a_member_confirms_the_editor_and_tells_them(
    editor_client: APIClient, applicant: Organization, editor: User, staff: User
) -> None:
    upload(editor_client, applicant)
    submission = AffidavitSubmission.objects.get()
    mail.outbox.clear()
    admin = Client()
    admin.force_login(staff)
    page = admin.get(reverse("admin:fk_affidavitsubmission_change", args=[submission.pk]))
    assert page.status_code == 200
    assert b'name="_approve"' in page.content
    assert b"971526920" in page.content

    response = admin.post(
        reverse("admin:fk_affidavitsubmission_change", args=[submission.pk]),
        {"review_note": "", "_approve": "1"},
    )

    assert response.status_code == 302
    submission.refresh_from_db()
    applicant.refresh_from_db()
    editor.refresh_from_db()
    assert submission.status == AffidavitStatus.APPROVED
    assert submission.reviewed_by == staff
    assert applicant.fkmember
    assert editor.identity_confirmed
    assert [m.to for m in mail.outbox] == [[editor.email]]
    assert "godkjent" in mail.outbox[0].subject


def test_rejecting_needs_a_note_and_passes_it_on(
    editor_client: APIClient, applicant: Organization, editor: User, staff: User
) -> None:
    upload(editor_client, applicant)
    submission = AffidavitSubmission.objects.get()
    mail.outbox.clear()
    admin = Client()
    admin.force_login(staff)
    change = reverse("admin:fk_affidavitsubmission_change", args=[submission.pk])

    admin.post(change, {"review_note": "", "_reject": "1"})
    submission.refresh_from_db()
    assert submission.status == AffidavitStatus.PENDING

    admin.post(change, {"review_note": "Signaturen mangler.", "_reject": "1"})
    submission.refresh_from_db()
    applicant.refresh_from_db()
    assert submission.status == AffidavitStatus.REJECTED
    assert not applicant.fkmember
    assert "Signaturen mangler." in mail.outbox[0].body
    response = editor_client.get(url(applicant))
    assert response.data[0]["review_note"] == "Signaturen mangler."


def test_the_queue_opens_on_pending_scans(staff: User) -> None:
    admin = Client()
    admin.force_login(staff)

    response = admin.get(reverse("admin:fk_affidavitsubmission_changelist"))

    assert response.status_code == 302
    assert response["Location"].endswith("?status__exact=pending")
    assert admin.get(response["Location"]).status_code == 200
