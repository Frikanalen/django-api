import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

OLD_MIGRATION = ("fk", "0037_ingest_job_claiming")
NEW_MIGRATION = ("fk", "0038_merge_video_header_into_description")


@pytest.mark.django_db(transaction=True)
def test_video_headers_are_merged_into_descriptions() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate([OLD_MIGRATION])
    old_apps = executor.loader.project_state([OLD_MIGRATION]).apps

    User = old_apps.get_model("fk", "User")
    Organization = old_apps.get_model("fk", "Organization")
    Video = old_apps.get_model("fk", "Video")

    editor = User.objects.create(email="header-migration@example.test")
    organization = Organization.objects.create(name="Migration test", editor=editor)
    common = {"creator": editor, "organization": organization}

    header_only = Video.objects.create(
        name="Header only", header="Short synopsis", description=None, **common
    )
    empty_description = Video.objects.create(
        name="Empty description", header="Another synopsis", description="", **common
    )
    description_only = Video.objects.create(
        name="Description only", header=None, description="Long synopsis", **common
    )
    both = Video.objects.create(
        name="Both",
        header="H" * 2048,
        description="D" * 2048,
        **common,
    )
    empty_header = Video.objects.create(
        name="Empty header", header="", description="Existing description", **common
    )

    executor = MigrationExecutor(connection)
    executor.migrate([NEW_MIGRATION])
    new_apps = executor.loader.project_state([NEW_MIGRATION]).apps
    MigratedVideo = new_apps.get_model("fk", "Video")

    assert MigratedVideo.objects.get(pk=header_only.pk).description == "Short synopsis"
    assert MigratedVideo.objects.get(pk=empty_description.pk).description == "Another synopsis"
    assert MigratedVideo.objects.get(pk=description_only.pk).description == "Long synopsis"
    assert MigratedVideo.objects.get(pk=both.pk).description == ("H" * 2048 + "\n\n" + "D" * 2048)
    assert MigratedVideo.objects.get(pk=empty_header.pk).description == "Existing description"
    assert "header" not in {field.name for field in MigratedVideo._meta.get_fields()}
