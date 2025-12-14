from django.core.management.base import BaseCommand

from agenda.views import fill_weekly_slots


class Command(BaseCommand):
    args = ""
    help = "Schedule videos according to predefined WeeklySlot criteria"

    def handle(self, *args, **options):
        if 1 < int(options["verbosity"]):
            import logging

            logging.basicConfig(level=logging.DEBUG)

        fill_weekly_slots()
