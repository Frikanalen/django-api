"""
Who may report an ingest's progress, and who may read it.

The endpoint has two audiences facing opposite directions: the ingest
service writes the state, and the organization behind the video reads it.
Neither may do the other's half, and the operator-facing `statusText`
must not reach a reader at all.
"""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import IngestJob, IngestState, Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Ingest job video",
        creator=editor,
        organization=organization,
        proper_import=False,
    )


@pytest.fixture
def ingest_client() -> APIClient:
    """The service account, which is a superuser -- see IngestJobPermission."""
    service = User.objects.create(email="ingest@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=service)
    return client


def url(video: Video) -> str:
    return reverse("api-video-ingest-job-detail", args=[video.pk])


def report(client: APIClient, video: Video, **fields):
    return client.put(url(video), fields, format="json")


def test_a_video_nothing_has_uploaded_to_is_pending(editor_client: APIClient, video: Video) -> None:
    response = editor_client.get(url(video))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "video": video.pk,
        "state": "pending",
        "percentageDone": None,
        "errorCode": "",
        "updatedTime": None,
    }


def test_a_state_with_nothing_to_count_reports_no_percentage(
    ingest_client: APIClient, editor_client: APIClient, video: Video
) -> None:
    # Probing knows only that it is running. Zero would read as a stuck
    # upload where "no estimate" reads as work in progress.
    report(ingest_client, video, state="probing")

    assert editor_client.get(url(video)).json()["percentageDone"] is None


def test_reading_does_not_create_a_row(editor_client: APIClient, video: Video) -> None:
    editor_client.get(url(video))

    assert not IngestJob.objects.exists()


def test_a_video_ingested_before_this_endpoint_existed_reports_done(
    editor_client: APIClient, video: Video
) -> None:
    # The whole back catalogue predates the table being written to. Its
    # proper_import flag is the only record that ingest ever finished.
    video.proper_import = True
    video.save()

    response = editor_client.get(url(video))

    assert response.json()["state"] == "done"
    assert response.json()["percentageDone"] == 100


def test_ingest_can_report_progress(
    ingest_client: APIClient, editor_client: APIClient, video: Video
) -> None:
    response = report(ingest_client, video, state="transcoding", percentageDone=40)

    assert response.status_code == status.HTTP_200_OK
    assert editor_client.get(url(video)).json()["state"] == "transcoding"
    assert IngestJob.objects.get(video=video).percentage_done == 40


def test_repeated_reports_replace_rather_than_accumulate(
    ingest_client: APIClient, video: Video
) -> None:
    report(ingest_client, video, state="archiving")
    report(ingest_client, video, state="transcoding", percentageDone=10)
    report(ingest_client, video, state="transcoding", percentageDone=90)

    assert IngestJob.objects.count() == 1
    assert IngestJob.objects.get(video=video).state == IngestState.TRANSCODING


def test_a_report_must_say_what_state_it_describes(ingest_client: APIClient, video: Video) -> None:
    response = report(ingest_client, video, percentageDone=50)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_percentage_outside_the_range_is_rejected(ingest_client: APIClient, video: Video) -> None:
    response = report(ingest_client, video, state="transcoding", percentageDone=140)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_only_a_failure_may_carry_an_error_code(ingest_client: APIClient, video: Video) -> None:
    response = report(ingest_client, video, state="transcoding", errorCode="unsupported_codec")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not IngestJob.objects.exists()


def test_a_failure_reaches_the_uploader_as_a_code(
    ingest_client: APIClient, editor_client: APIClient, video: Video
) -> None:
    report(
        ingest_client,
        video,
        state="failed",
        errorCode="unsupported_codec",
        statusText="ffmpeg: Unknown encoder 'hevc_nvenc' while reading /archive/media/1/original/x.mkv",
    )

    body = editor_client.get(url(video)).json()

    assert body["state"] == "failed"
    assert body["errorCode"] == "unsupported_codec"


def test_operator_detail_is_never_served_to_a_reader(
    ingest_client: APIClient, editor_client: APIClient, video: Video
) -> None:
    leaky = "ffmpeg died reading /archive/media/1/original/secret-working-title.mkv"
    report(ingest_client, video, state="failed", errorCode="transcode_failed", statusText=leaky)

    # Both audiences: the uploader, and the service that wrote it.
    for client in (editor_client, ingest_client):
        response = client.get(url(video))

        assert "statusText" not in response.json()
        assert leaky not in response.content.decode()

    # It is stored, though -- the admin is where an operator reads it.
    assert IngestJob.objects.get(video=video).status_text == leaky


def test_outsiders_may_not_read(video: Video) -> None:
    outsider = User.objects.create(email="ingest-outsider@example.test")
    client = APIClient()
    client.force_authenticate(user=outsider)

    response = client.get(url(video))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_anonymous_readers_are_asked_to_authenticate(video: Video) -> None:
    response = APIClient().get(url(video))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_the_uploader_may_not_report_their_own_ingest(
    editor_client: APIClient, video: Video
) -> None:
    response = report(editor_client, video, state="done", percentageDone=100)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not IngestJob.objects.exists()


def test_an_unknown_video_has_no_ingest_state(editor_client: APIClient, video: Video) -> None:
    response = editor_client.get(reverse("api-video-ingest-job-detail", args=[video.pk + 1]))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_patch_is_not_offered(ingest_client: APIClient, video: Video) -> None:
    response = ingest_client.patch(url(video), {"percentageDone": 50}, format="json")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
