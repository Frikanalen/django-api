"""Index the columns the schedule and playout log are read by.

Both indexes are built CONCURRENTLY: a plain CREATE INDEX holds ACCESS
EXCLUSIVE for the length of the build, which on these tables means the
schedule API and playout logging block on it. The cost is that this
migration cannot run in a transaction, so a failure part-way leaves the
first index in place -- drop it by hand before re-running.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("fk", "0026_organization_search_document_video_search_document_and_more"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="scheduleitem",
            index=models.Index(fields=["starttime"], name="scheduleitem_starttime_idx"),
        ),
        AddIndexConcurrently(
            model_name="asrun",
            index=models.Index(fields=["-played_at", "-id"], name="asrun_played_at_desc_idx"),
        ),
    ]
