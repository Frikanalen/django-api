"""Keep the shared page cache from serving a schedule that has changed.

Anonymous callers -- playout and the on-air graphics among them -- get the
schedule from the site-wide page cache, which would otherwise keep handing
out the old one for up to CACHE_MIDDLEWARE_SECONDS. Clearing the whole cache
is blunt but cheap: it holds nothing that is expensive to rebuild, and the
schedule does not change often enough for the misses to matter.

The clear waits for the commit. Done any earlier, a request arriving between
the clear and the commit would read the old rows and cache them again.
"""

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from fk.models import LiveTransmission, Scheduleitem, Video


@receiver(post_save, sender=Scheduleitem)
@receiver(post_delete, sender=Scheduleitem)
@receiver(post_save, sender=LiveTransmission)
@receiver(post_delete, sender=LiveTransmission)
def clear_page_cache_on_schedule_change(sender, **kwargs) -> None:
    transaction.on_commit(cache.clear)


@receiver(pre_save, sender=Video, dispatch_uid="fk_note_removal_change")
def note_whether_removal_changes(sender, instance: Video, raw=False, **kwargs) -> None:
    """Remember whether this save removes the video or lifts its removal."""
    if raw or instance.pk is None:
        instance._removal_changed = False
        return
    was = Video.objects.filter(pk=instance.pk).values_list("is_banned", flat=True).first()
    instance._removal_changed = was is not None and was != instance.is_banned


@receiver(post_save, sender=Video, dispatch_uid="fk_clear_cache_on_removal")
def clear_page_cache_on_removal(sender, instance: Video, **kwargs) -> None:
    """A removed video drops out of the schedule and its page at once.

    Removing one changes what the schedule says without touching a
    schedule row, so nothing above would clear the cached copy that
    playout reads, and the video could stay on air for ten minutes.
    """
    if getattr(instance, "_removal_changed", False):
        transaction.on_commit(cache.clear)
