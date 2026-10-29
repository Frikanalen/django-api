from fk.management.commands.ensure_playout_credential import ServiceCredentialCommand
from fk.models import StreamCredential


class Command(ServiceCredentialCommand):
    help = (
        "Issue MediaMTX's preview converter credential unless the one it holds is "
        "still valid. Prints JSON; the password appears only when a new one is issued."
    )
    action = StreamCredential.Action.PREVIEW
    label = "Preview converter (provisioned)"
