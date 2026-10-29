from django.core.management.base import BaseCommand

from agenda.scheduling.jukebox import fill_agenda_with_jukebox


class Command(BaseCommand):
    args = ""
    help = "Fill empty airtime with jukebox fillers through the end of the open broadcast week"

    def handle(self, *args, **options):
        if 1 < int(options["verbosity"]):
            import logging

            logging.basicConfig(level=logging.INFO)
        fill_agenda_with_jukebox()
