import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from agenda.jukebox import plan_iso_week
from fk.models import Scheduleitem


class Command(BaseCommand):
    args = ""
    help = "Plan jukebox content for a specific ISO week (defaults to week after next)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--year", type=int, help="ISO year (defaults to year of week after next)"
        )
        parser.add_argument(
            "--week", type=int, help="ISO week number (defaults to week after next)"
        )

    def handle(self, *args, **options):
        if 1 < int(options["verbosity"]):
            import logging

            logging.basicConfig(level=logging.INFO)

        # Get the week after next
        now = timezone.now()
        # Add 14 days to get to the week after next
        target_date = now + datetime.timedelta(days=14)
        iso_year, iso_week, _ = target_date.isocalendar()

        # Allow override from command line
        iso_year = options.get("year") or iso_year
        iso_week = options.get("week") or iso_week

        self.stdout.write(f"Planning ISO week {iso_year}-W{iso_week:02d}")

        placements = plan_iso_week(
            iso_year,
            iso_week,
        )

        Scheduleitem.objects.bulk_create(placements)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {len(placements)} jukebox placements for week {iso_year}-W{iso_week:02d}"
            )
        )
