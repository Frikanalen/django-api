"""
Handing ingest work to workers, one job to one worker.

The endpoint exists so that a pool of workers can drain a shared queue,
which makes the interesting cases the concurrent ones: two workers must
never be given the same video, and a worker that dies holding a job must
not take that job to the grave with it.
"""

import threading
from datetime import timedelta

import pytest
from django.db import connection, connections
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import IngestJob, IngestKind, IngestState, Organization, User, Video

pytestmark = pytest.mark.django_db

CLAIM_URL = reverse("api-ingest-claim")


@pytest.fixture
def ingest_client() -> APIClient:
    """The service account, which is a superuser -- see IngestJobPermission."""
    service = User.objects.create(email="ingest-claimer@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=service)
    return client


def make_video(editor: User, organization: Organization, name: str = "Claimable") -> Video:
    return Video.objects.create(
        name=name,
        creator=editor,
        organization=organization,
        proper_import=False,
    )


def enqueue(video: Video, **fields) -> IngestJob:
    fields.setdefault("state", IngestState.PENDING)
    return IngestJob.objects.create(video=video, **fields)


def age(job: IngestJob, by: timedelta) -> None:
    """Backdate a job's last report.

    Through a queryset update rather than save(): `updated_time` is
    auto_now, so saving would stamp it with the present and undo exactly
    what the test is trying to arrange.
    """
    IngestJob.objects.filter(pk=job.pk).update(updated_time=timezone.now() - by)


def claim(client: APIClient, **body):
    return client.post(CLAIM_URL, body, format="json")


# --------------------------------------------------------------------------
# The ordinary cases
# --------------------------------------------------------------------------


def test_an_idle_queue_answers_no_content(ingest_client: APIClient) -> None:
    """Not 404, and not an empty list. A polling worker asking an empty
    queue is the normal state of the system, not a mistake it made."""
    response = claim(ingest_client)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not response.content


def test_claiming_stamps_the_worker_and_moves_the_job_out_of_pending(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    video = make_video(editor, organization)
    enqueue(video)

    response = claim(ingest_client, worker="ingest-workers-7f9c-x2k4")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["video"] == video.pk
    assert response.json()["claimedBy"] == "ingest-workers-7f9c-x2k4"
    assert response.json()["state"] == IngestState.PROBING

    job = IngestJob.objects.get(pk=video.pk)
    assert job.state == IngestState.PROBING
    assert job.claimed_by == "ingest-workers-7f9c-x2k4"


def test_a_claimed_job_is_not_handed_out_twice(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    enqueue(make_video(editor, organization))

    assert claim(ingest_client).status_code == status.HTTP_200_OK
    assert claim(ingest_client).status_code == status.HTTP_204_NO_CONTENT


def test_the_operator_facing_status_text_never_reaches_the_claimer(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    enqueue(make_video(editor, organization), status_text="ffmpeg said something rude")

    assert "statusText" not in claim(ingest_client).json()


def test_claiming_is_the_service_account_s_alone(
    editor_client: APIClient, editor: User, organization: Organization
) -> None:
    enqueue(make_video(editor, organization))

    assert claim(editor_client).status_code == status.HTTP_403_FORBIDDEN
    assert IngestJob.objects.get().state == IngestState.PENDING


# --------------------------------------------------------------------------
# Which job comes out
# --------------------------------------------------------------------------


def test_higher_priority_is_claimed_first(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    """A member watching their own upload must not queue behind a
    catalogue-wide backfill, however long that backfill has been waiting."""
    backfill = make_video(editor, organization, "Backfill")
    upload = make_video(editor, organization, "Upload")
    age(enqueue(backfill, kind=IngestKind.BACKFILL, priority=0), timedelta(days=7))
    enqueue(upload, kind=IngestKind.UPLOAD, priority=10)

    assert claim(ingest_client).json()["video"] == upload.pk


def test_at_equal_priority_the_longest_wait_is_claimed_first(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    """Otherwise a busy queue could starve a job indefinitely."""
    newer = make_video(editor, organization, "Newer")
    older = make_video(editor, organization, "Older")
    age(enqueue(newer), timedelta(minutes=1))
    age(enqueue(older), timedelta(hours=3))

    assert claim(ingest_client).json()["video"] == older.pk


def test_a_backfill_claim_never_returns_an_upload(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    """The kinds say where the source file is. An upload's source is in
    the ReadWriteOnce tusd volume, so a worker that cannot mount it
    cannot do the job, whatever its priority."""
    enqueue(make_video(editor, organization, "Upload"), kind=IngestKind.UPLOAD, priority=100)

    assert claim(ingest_client, kind="backfill").status_code == status.HTTP_204_NO_CONTENT


def test_a_backfill_claim_returns_a_backfill(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    enqueue(make_video(editor, organization, "Upload"), kind=IngestKind.UPLOAD, priority=100)
    backfill = make_video(editor, organization, "Backfill")
    enqueue(backfill, kind=IngestKind.BACKFILL)

    assert claim(ingest_client, kind="backfill").json()["video"] == backfill.pk


def test_a_claim_naming_no_kind_takes_whatever_is_there(
    ingest_client: APIClient, editor: User, organization: Organization
) -> None:
    backfill = make_video(editor, organization, "Backfill")
    enqueue(backfill, kind=IngestKind.BACKFILL)

    assert claim(ingest_client).json()["video"] == backfill.pk


def test_an_unknown_kind_is_rejected_rather_than_ignored(ingest_client: APIClient) -> None:
    """Silently widening a narrow claim would hand a worker a job whose
    source it cannot reach."""
    assert claim(ingest_client, kind="sideways").status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize("state", sorted(IngestState.terminal()))
def test_a_finished_job_is_never_reclaimed(
    ingest_client: APIClient, editor: User, organization: Organization, state: str
) -> None:
    """`done` and `failed` are terminal. Silence there means finished,
    not abandoned, however old the row is."""
    job = enqueue(
        make_video(editor, organization),
        state=state,
        error_code="probe_failed" if state == IngestState.FAILED else "",
    )
    age(job, timedelta(days=30))

    assert claim(ingest_client).status_code == status.HTTP_204_NO_CONTENT


# --------------------------------------------------------------------------
# The lease: recovering work whose worker died
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(IngestState.in_progress()))
def test_a_job_silent_for_longer_than_the_lease_is_claimable(
    ingest_client: APIClient, editor: User, organization: Organization, settings, state: str
) -> None:
    """Before this, an ingest interrupted by a pod restart was lost for
    good: no handler runs when the process is killed, so the row kept its
    last state and nothing ever looked at it again."""
    settings.FK_INGEST_LEASE = timedelta(minutes=60)
    video = make_video(editor, organization)
    age(enqueue(video, state=state, claimed_by="the-departed"), timedelta(minutes=61))

    response = claim(ingest_client, worker="the-successor")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["video"] == video.pk
    assert response.json()["claimedBy"] == "the-successor"


@pytest.mark.parametrize("state", sorted(IngestState.in_progress()))
def test_a_job_within_its_lease_is_left_alone(
    ingest_client: APIClient, editor: User, organization: Organization, settings, state: str
) -> None:
    """The expensive mistake is not failing to recover abandoned work but
    duplicating work that is still running -- archiving a multi-gigabyte
    original reports nothing for a long time and is not stuck."""
    settings.FK_INGEST_LEASE = timedelta(minutes=60)
    age(enqueue(make_video(editor, organization), state=state), timedelta(minutes=59))

    assert claim(ingest_client).status_code == status.HTTP_204_NO_CONTENT


def test_reclaiming_discards_the_dead_worker_s_progress(
    ingest_client: APIClient, editor: User, organization: Organization, settings
) -> None:
    """The work starts over from probing, which has nothing to count. A
    bar left standing at the abandoned worker's last figure would tell
    the uploader something false."""
    settings.FK_INGEST_LEASE = timedelta(minutes=60)
    video = make_video(editor, organization)
    age(
        enqueue(video, state=IngestState.TRANSCODING, percentage_done=73),
        timedelta(minutes=61),
    )

    assert claim(ingest_client).json()["percentageDone"] is None
    assert IngestJob.objects.get(pk=video.pk).percentage_done is None


def test_claiming_restarts_the_lease(
    ingest_client: APIClient, editor: User, organization: Organization, settings
) -> None:
    settings.FK_INGEST_LEASE = timedelta(minutes=60)
    video = make_video(editor, organization)
    age(enqueue(video, state=IngestState.TRANSCODING), timedelta(minutes=61))

    claim(ingest_client, worker="first")
    # The successor now holds it, and is inside its own fresh lease.
    assert claim(ingest_client, worker="second").status_code == status.HTTP_204_NO_CONTENT
    assert IngestJob.objects.get(pk=video.pk).claimed_by == "first"


def test_the_lease_length_is_configurable(
    ingest_client: APIClient, editor: User, organization: Organization, settings
) -> None:
    age(
        enqueue(make_video(editor, organization), state=IngestState.ARCHIVING),
        timedelta(minutes=30),
    )

    settings.FK_INGEST_LEASE = timedelta(minutes=60)
    assert claim(ingest_client).status_code == status.HTTP_204_NO_CONTENT

    settings.FK_INGEST_LEASE = timedelta(minutes=10)
    assert claim(ingest_client).status_code == status.HTTP_200_OK


# --------------------------------------------------------------------------
# Concurrency, which is the whole reason the endpoint exists
# --------------------------------------------------------------------------


def committed_fixtures() -> tuple[User, Organization, User]:
    """Editor, organization and service account, built in-test.

    The transactional tests below cannot use the module fixtures: those
    are created inside the test's own transaction, and a second
    connection cannot see -- or lock -- a row that has not been
    committed. On a transactional database the test's writes are visible
    to every connection, and pytest-django truncates the tables
    afterwards.
    """
    editor = User.objects.create(email="claim-concurrency-editor@example.test")
    organization = Organization.objects.create(name="Claim concurrency org", editor=editor)
    service = User.objects.create(email="claim-concurrency-ingest@example.test", is_superuser=True)
    return editor, organization, service


@pytest.mark.django_db(transaction=True)
def test_a_row_another_transaction_holds_is_skipped_rather_than_waited_for() -> None:
    """`SKIP LOCKED` in isolation.

    A second connection holds the only claimable row. Without SKIP
    LOCKED this claim would block until that transaction ended and then
    hand out a row that had already been given away; instead the claim
    steps over it and reports an empty queue.
    """
    editor, organization, _ = committed_fixtures()
    video = make_video(editor, organization)
    enqueue(video)

    holder = connections.create_connection("default")
    try:
        with holder.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SELECT 1 FROM fk_ingestjob WHERE video_id = %s FOR UPDATE", [video.pk])

            # So that an implementation which waits on the lock instead of
            # skipping it fails the test rather than hanging the suite.
            with connection.cursor() as claimer:
                claimer.execute("SET lock_timeout = '5s'")
            try:
                assert IngestJob.claim() is None
            finally:
                with connection.cursor() as claimer:
                    claimer.execute("RESET lock_timeout")

            cursor.execute("ROLLBACK")
    finally:
        holder.close()

    # And once the holder lets go, the row is claimable again -- the
    # claim skipped it, it did not consume it.
    assert IngestJob.claim() is not None


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_never_return_the_same_video() -> None:
    """Acceptance criterion 1, end to end.

    More workers than jobs, all released at once, so that several are
    inside the claim query at the same moment. Every worker handed a job
    must have been handed a different one.
    """
    editor, organization, service = committed_fixtures()

    worker_count = 8
    job_count = 4
    for index in range(job_count):
        enqueue(make_video(editor, organization, f"Concurrent {index}"))

    claimed: list[int] = []
    claimed_lock = threading.Lock()
    start = threading.Barrier(worker_count)

    def worker(name: str) -> None:
        try:
            client = APIClient()
            client.force_authenticate(user=service)
            start.wait(timeout=30)
            response = claim(client, worker=name)
            if response.status_code == status.HTTP_200_OK:
                with claimed_lock:
                    claimed.append(response.json()["video"])
        finally:
            # Each thread opened its own connection; leaving them open
            # would stall the test database teardown.
            connections.close_all()

    threads = [
        threading.Thread(target=worker, args=(f"worker-{index}",)) for index in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(claimed) == len(set(claimed)), "the same video was claimed twice"
    # Nothing is left behind either: a worker that skipped a locked row
    # still saw the rest of the queue, so all four jobs found a taker.
    assert sorted(claimed) == sorted(IngestJob.objects.values_list("video_id", flat=True))
    assert not IngestJob.objects.filter(state=IngestState.PENDING).exists()
