import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import ImageMediaType, ImageRole, Organization, ProgramImage, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="image-editor@example.test")


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(name="Image organization", editor=editor)
    organization.members.add(editor)
    return organization


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(name="Image programme", creator=editor, organization=organization)


@pytest.fixture
def editor_client(editor: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=editor)
    return client


@pytest.fixture
def staff_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create(email="image-ingest@example.test", is_superuser=True)
    )
    return client


def registration(video: Video, **changes) -> dict:
    return {
        "role": ImageRole.KEY_ART_TITLED,
        "filename": f"{video.pk}/images/2f92e90dbb444e67bdb0893b5fe1d697.png",
        "mediaType": ImageMediaType.PNG,
        "width": 1200,
        "height": 675,
        **changes,
    }


def test_ingest_can_register_an_archived_image(staff_client: APIClient, video: Video) -> None:
    response = staff_client.post(
        reverse("api-program-image-list", kwargs={"video_id": video.pk}),
        registration(video),
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED, response.content
    created = ProgramImage.objects.get()
    assert created.video == video
    assert created.role == ImageRole.KEY_ART_TITLED
    assert created.filename == registration(video)["filename"]
    assert (created.width, created.height) == (1200, 675)
    assert response.json()["url"] == (
        f"https://frikanalen.no/media/{video.pk}/images/2f92e90dbb444e67bdb0893b5fe1d697.png"
    )


def test_ingest_registration_is_idempotent(staff_client: APIClient, video: Video) -> None:
    url = reverse("api-program-image-list", kwargs={"video_id": video.pk})
    first = staff_client.post(url, registration(video), format="json")
    second = staff_client.post(
        url,
        registration(video, role=ImageRole.SHOW_STILL),
        format="json",
    )

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert ProgramImage.objects.count() == 1
    assert ProgramImage.objects.get().role == ImageRole.SHOW_STILL


def test_member_cannot_bypass_ingest_and_register_archive_metadata(
    editor_client: APIClient, video: Video
) -> None:
    response = editor_client.post(
        reverse("api-program-image-list", kwargs={"video_id": video.pk}),
        registration(video),
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not ProgramImage.objects.exists()


def test_registration_rejects_a_path_outside_the_videos_image_directory(
    staff_client: APIClient, video: Video
) -> None:
    response = staff_client.post(
        reverse("api-program-image-list", kwargs={"video_id": video.pk}),
        registration(video, filename="../another-video/images/stolen.png"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not ProgramImage.objects.exists()


def test_registration_rejects_invalid_dimensions(staff_client: APIClient, video: Video) -> None:
    response = staff_client.post(
        reverse("api-program-image-list", kwargs={"video_id": video.pk}),
        registration(video, width=0, height=65_536),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not ProgramImage.objects.exists()


def test_registration_rejects_a_suffix_that_disagrees_with_the_media_type(
    staff_client: APIClient, video: Video
) -> None:
    response = staff_client.post(
        reverse("api-program-image-list", kwargs={"video_id": video.pk}),
        registration(video, mediaType=ImageMediaType.JPEG),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not ProgramImage.objects.exists()


def test_image_collection_is_scoped_by_video(
    editor_client: APIClient, video: Video, organization: Organization
) -> None:
    other = Video.objects.create(
        name="Other image programme", creator=video.creator, organization=organization
    )
    for target in (video, other):
        ProgramImage.objects.create(
            video=target,
            role=ImageRole.SHOW_STILL,
            filename=f"{target.pk}/images/show.png",
            media_type=ImageMediaType.PNG,
            width=640,
            height=360,
        )

    response = editor_client.get(reverse("api-program-image-list", kwargs={"video_id": video.pk}))

    assert response.status_code == status.HTTP_200_OK
    assert [item["video"] for item in response.json()["results"]] == [video.pk]


def test_member_can_reclassify_and_unpublish_their_image(
    editor_client: APIClient, video: Video
) -> None:
    image = ProgramImage.objects.create(
        video=video,
        role=ImageRole.SHOW_STILL,
        filename=f"{video.pk}/images/show.png",
        media_type=ImageMediaType.PNG,
        width=640,
        height=360,
    )
    url = reverse("api-program-image-detail", kwargs={"video_id": video.pk, "pk": image.pk})

    changed = editor_client.patch(url, {"role": ImageRole.EPISODE_STILL}, format="json")
    deleted = editor_client.delete(url)

    assert changed.status_code == status.HTTP_200_OK
    assert changed.json()["role"] == ImageRole.EPISODE_STILL
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert not ProgramImage.objects.exists()


def test_image_detail_is_scoped_by_video(
    editor_client: APIClient, video: Video, organization: Organization
) -> None:
    other = Video.objects.create(
        name="Other image programme", creator=video.creator, organization=organization
    )
    image = ProgramImage.objects.create(
        video=other,
        role=ImageRole.SHOW_STILL,
        filename=f"{other.pk}/images/show.png",
        media_type=ImageMediaType.PNG,
        width=640,
        height=360,
    )

    response = editor_client.get(
        reverse(
            "api-program-image-detail",
            kwargs={"video_id": video.pk, "pk": image.pk},
        )
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
