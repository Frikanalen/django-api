"""Recompile grants when a listed video's file or length changes."""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from fk.models import Video


@receiver(post_save, sender=Video, dispatch_uid="scheduling_recompile_for_video")
def recompile_when_a_listed_video_changes(sender, instance: Video, created: bool, **kwargs) -> None:
    if created or kwargs.get("raw"):
        return
    from scheduling.services import recompile_for_video

    # After commit, so the compiler sees the saved duration and a failed
    # recompile cannot roll back the upload that triggered it.
    transaction.on_commit(lambda: recompile_for_video(instance))
