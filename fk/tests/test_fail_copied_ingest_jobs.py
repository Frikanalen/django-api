"""Staging fails the ingest queue it copies from production, so its own
workers never take production's jobs."""

from io import StringIO

import pytest
from django.core.management import call_command

from fk.models import IngestJob, IngestKind, IngestState, Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_job():
    editor = User.objects.create(email="editor@example.test")
    organization = Organization.objects.create(name="Org", editor=editor)
    count = 0

    def make(state: str, **fields) -> IngestJob:
        nonlocal count
        count += 1
        video = Video.objects.create(
            name=f"Video {count}", creator=editor, organization=organization
        )
        error = {"error_code": "boom"} if state == IngestState.FAILED else {}
        return IngestJob.objects.create(video=video, state=state, **error, **fields)

    return make


def run(*args) -> str:
    out = StringIO()
    call_command("fail_copied_ingest_jobs", *args, stdout=out)
    return out.getvalue()


def test_fails_waiting_and_in_progress_jobs(make_job) -> None:
    upload = make_job(IngestState.PENDING, priority=100)
    backfill = make_job(IngestState.PENDING, kind=IngestKind.BACKFILL)
    running = make_job(IngestState.TRANSCODING, percentage_done=40)

    assert "Failed 3" in run("--yes")

    for job in (upload, backfill, running):
        job.refresh_from_db()
        assert job.state == IngestState.FAILED
        assert job.error_code == "copied_from_production"
        assert job.percentage_done is None
    assert IngestJob.claimable().count() == 0


def test_leaves_finished_jobs_alone(make_job) -> None:
    done = make_job(IngestState.DONE, percentage_done=100)
    failed = make_job(IngestState.FAILED)

    run("--yes")

    done.refresh_from_db()
    failed.refresh_from_db()
    assert (done.state, done.error_code) == (IngestState.DONE, "")
    assert (failed.state, failed.error_code) == (IngestState.FAILED, "boom")


def test_only_reports_without_yes(make_job) -> None:
    job = make_job(IngestState.PENDING)

    assert "1 unfinished" in run()

    job.refresh_from_db()
    assert job.state == IngestState.PENDING
