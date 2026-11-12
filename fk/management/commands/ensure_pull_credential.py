from fk.management.commands import ensure_playout_credential
from fk.models import StreamCredential


class Command(ensure_playout_credential.Command):
    """The same, for the credential MediaMTX's pull relay holds."""

    action = StreamCredential.Action.PULL
    label = "Pull relay (provisioned)"
    help = (
        "Issue the pull relay's credential unless the one it holds is still valid. "
        "Prints JSON; the password appears only when a new credential is issued."
    )
