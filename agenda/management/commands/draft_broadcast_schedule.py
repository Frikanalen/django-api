import logging

from django.core.management.base import BaseCommand

from agenda.scheduling.draft import draft_broadcast_schedule


class Command(BaseCommand):
    help = "Place weekly slots and then fill remaining airtime with the jukebox"

    def handle(self, *args, **options):
        if 1 < int(options["verbosity"]):
            logging.basicConfig(level=logging.INFO)
        draft_broadcast_schedule()
