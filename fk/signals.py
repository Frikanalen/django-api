"""Keep the shared page cache from serving a schedule that has changed.

Anonymous callers -- playout and the on-air graphics among them -- get the
schedule from the site-wide page cache, which would otherwise keep handing
out the old one for up to CACHE_MIDDLEWARE_SECONDS. Clearing the whole cache
is blunt but cheap: it holds nothing that is expensive to rebuild, and the
schedule does not change often enough for the misses to matter.

The clear waits for the commit. Done any earlier, a request arriving between
the clear and the commit would read the old rows and cache them again.

The clear also drops the schedule version (see schedule_version), which is
how the frontend learns that the schedule changed: it polls the version and
refetches what it has cached only when it moves. Grants are included because
the frontend's own pages about them should follow too.
"""

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from fk.models import LiveTransmission, Scheduleitem
from scheduling.models import AirtimeGrant, GrantItem

SCHEDULE_VERSION_KEY = "schedule-version"


def schedule_version() -> str:
    """An opaque token that changes whenever the schedule or a grant does.

    Kept in the same cache the changes clear, so it needs no bookkeeping of
    its own: after a clear the next caller mints a new one.
    """
    return cache.get_or_set(SCHEDULE_VERSION_KEY, lambda: timezone.now().isoformat(), None)


def clear_page_cache_after_commit() -> None:
    transaction.on_commit(cache.clear)


@receiver(post_save, sender=AirtimeGrant)
@receiver(post_delete, sender=AirtimeGrant)
@receiver(post_save, sender=GrantItem)
@receiver(post_delete, sender=GrantItem)
@receiver(post_save, sender=Scheduleitem)
@receiver(post_delete, sender=Scheduleitem)
@receiver(post_save, sender=LiveTransmission)
@receiver(post_delete, sender=LiveTransmission)
def clear_page_cache_on_schedule_change(sender, **kwargs) -> None:
    clear_page_cache_after_commit()
