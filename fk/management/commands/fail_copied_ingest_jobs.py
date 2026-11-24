from django.core.management.base import BaseCommand, CommandError

from fk.models.ingest import IngestJob, IngestState

ERROR_CODE = "copied_from_production"


class Command(BaseCommand):
    """Mark every unfinished ingest job failed, so no worker claims it.

    For staging right after it restores production's database: the copy
    brings production's ingest queue with it (uploads waiting, backfill
    re-encodes), and staging's workers would otherwise take those jobs as
    their own. Their sources are production's, not staging's: an upload's
    file is in production's tusd volume, and a re-encode would rewrite
    staging's copy of the archive for nothing. Failing them, rather than
    deleting the rows, keeps the video pages honest about why there is no
    file, and anyone who wants one ingested on staging can re-queue it.
    """

    help = "Fail every pending or in-progress ingest job (staging after a prod restore)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Really do it. Without it, only report how many jobs would be failed.",
        )

    def handle(self, *args, yes: bool, **options):
        unfinished = IngestJob.objects.exclude(state__in=IngestState.terminal())
        if not yes:
            count = unfinished.count()
            self.stdout.write(f"{count} unfinished ingest jobs; pass --yes to fail them.")
            return
        failed = unfinished.update(
            state=IngestState.FAILED,
            error_code=ERROR_CODE,
            percentage_done=None,
            status_text="Copied from production with the database; not ingested here.",
        )
        self.stdout.write(f"Failed {failed} unfinished ingest jobs copied from production.")
        if IngestJob.objects.exclude(state__in=IngestState.terminal()).exists():
            raise CommandError("Some ingest jobs are still unfinished.")
