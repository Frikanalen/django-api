import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from fk.models import StreamCredential

LABEL = "Playout (provisioned)"


class Command(BaseCommand):
    help = (
        "Issue playout's reader credential unless the one it holds is still valid. "
        "Prints JSON; the password appears only when a new credential is issued."
    )

    def add_arguments(self, parser):
        # Only the key UUID, never the password: it is not a secret, so it can
        # go on a command line, and a key that still exists unrevoked was
        # issued together with the password playout already holds.
        parser.add_argument("--keep", help="Key UUID of the credential playout holds now.")

    @transaction.atomic
    def handle(self, *args, keep=None, **options):
        provisioned = StreamCredential.objects.select_for_update().filter(
            action=StreamCredential.Action.PLAYOUT, label=LABEL, revoked_at__isnull=True
        )
        if keep:
            try:
                keep = UUID(keep)
            except ValueError as error:
                raise CommandError(f"--keep is not a UUID: {keep}") from error
            current = provisioned.filter(key_id=keep).first()
            if current and (current.expires_at is None or current.expires_at > timezone.now()):
                self.stdout.write(json.dumps({"changed": False, "username": str(keep)}))
                return

        # Whatever playout held is gone (rotated, revoked, or replaced by a
        # database copied from another environment). Retire the rest, so only
        # the credential in the new Secret stays usable.
        provisioned.update(revoked_at=timezone.now())
        credential = StreamCredential(action=StreamCredential.Action.PLAYOUT, label=LABEL)
        secret = credential.generate_secret()
        credential.save()
        self.stdout.write(
            json.dumps({"changed": True, "username": str(credential.key_id), "password": secret})
        )
