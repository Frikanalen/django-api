import logging

from django.core.management.base import BaseCommand

from scheduling.services import run_nightly


class Command(BaseCommand):
    help = "Issue, fill, lock and compile airtime grants (the nightly pipeline)"

    def handle(self, *args, **options):
        if 1 < int(options["verbosity"]):
            logging.basicConfig(level=logging.INFO)
        run_nightly()
