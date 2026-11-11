"""Recompile grants when a listed video's file or length changes."""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from fk.models import LiveTransmission, Video


@receiver(post_save, sender=Video, dispatch_uid="scheduling_recompile_for_video")
def recompile_when_a_listed_video_changes(sender, instance: Video, created: bool, **kwargs) -> None:
    if created or kwargs.get("raw"):
        return
    from scheduling.services import recompile_for_video

    # After commit, so the compiler sees the saved duration and a failed
    # recompile cannot roll back the upload that triggered it.
    transaction.on_commit(lambda: recompile_for_video(instance))


@receiver(post_save, sender=LiveTransmission, dispatch_uid="scheduling_recompile_for_live")
@receiver(post_delete, sender=LiveTransmission, dispatch_uid="scheduling_recompile_for_unlive")
def recompile_when_a_video_goes_live_or_stops(sender, instance: LiveTransmission, **kwargs) -> None:
    if kwargs.get("raw"):
        return
    from scheduling.services import recompile_for_video

    video_id = instance.video_id

    def recompile() -> None:
        video = Video.objects.filter(pk=video_id).first()
        if video is not None:
            recompile_for_video(video)

    transaction.on_commit(recompile)
