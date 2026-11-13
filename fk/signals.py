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
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from fk.models import LiveTransmission, Scheduleitem


@receiver(post_save, sender=Scheduleitem)
@receiver(post_delete, sender=Scheduleitem)
@receiver(post_save, sender=LiveTransmission)
@receiver(post_delete, sender=LiveTransmission)
def clear_page_cache_on_schedule_change(sender, **kwargs) -> None:
    transaction.on_commit(cache.clear)
