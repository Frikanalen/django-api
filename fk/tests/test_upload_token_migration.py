import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

OLD_MIGRATION = ("fk", "0064_invitation_link_without_email")
NEW_MIGRATION = ("fk", "0065_upload_token_model")


@pytest.fixture
def back_to_latest():
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("back_to_latest")
def test_only_tokens_of_videos_awaiting_their_original_are_kept() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([OLD_MIGRATION])
    old_apps = executor.loader.project_state([OLD_MIGRATION]).apps

    User = old_apps.get_model("fk", "User")
    Organization = old_apps.get_model("fk", "Organization")
    Video = old_apps.get_model("fk", "Video")

    editor = User.objects.create(email="token-migration@example.test")
    organization = Organization.objects.create(name="Token migration", editor=editor)
    common = {"creator": editor, "organization": organization}

    awaiting = Video.objects.create(
        name="Awaiting", upload_token="a" * 32, proper_import=False, **common
    )
    Video.objects.create(name="Ingested", upload_token="b" * 32, proper_import=True, **common)
    Video.objects.create(name="Legacy", upload_token="", proper_import=False, **common)

    executor = MigrationExecutor(connection)
    executor.migrate([NEW_MIGRATION])
    new_apps = executor.loader.project_state([NEW_MIGRATION]).apps
    UploadToken = new_apps.get_model("fk", "UploadToken")

    assert list(UploadToken.objects.values_list("video_id", "token")) == [(awaiting.pk, "a" * 32)]
