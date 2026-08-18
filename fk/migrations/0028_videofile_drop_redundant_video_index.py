"""Drop the video_id index made redundant by unique_variant_per_video.

That constraint is a btree on (video, variant); a lookup by video_id
alone uses its leading column just as well, so the separate index only
ever cost writes. Kept apart from 0027 so a plain transactional DROP
INDEX is not mixed in with non-atomic concurrent builds.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0027_index_schedule_read_paths"),
    ]

    operations = [
        migrations.AlterField(
            model_name="videofile",
            name="video",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="fk.video",
            ),
        ),
    ]
