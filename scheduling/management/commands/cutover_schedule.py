import logging
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from scheduling.services import cut_over


class Command(BaseCommand):
    help = (
        "Start the grant-based schedule: delete every pre-cut-over schedule item "
        "starting at or after --at, then issue, fill, lock and compile grants as of "
        "then. Rows written under the new rules are kept, so it is safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--at",
            help="The cut-over instant, ISO 8601 with an offset. Defaults to now.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm. Without it, nothing is deleted.",
        )

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        at = self._instant(options["at"])
        if not options["yes"]:
            raise CommandError(
                f"This deletes every pre-cut-over schedule item from {at.isoformat()}. "
                "Re-run with --yes to go ahead."
            )
        deleted = cut_over(at)
        self.stdout.write(f"Deleted {deleted} pre-cut-over schedule items from {at.isoformat()}.")

    @staticmethod
    def _instant(value: str | None) -> datetime:
        if value is None:
            return timezone.now()
        parsed = parse_datetime(value)
        if parsed is None or timezone.is_naive(parsed):
            raise CommandError("--at must be an ISO 8601 datetime with an offset.")
        return parsed
