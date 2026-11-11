"""Live transmissions belong to videos now, so a grant item is a video
again: live entries (staging test data only) are dropped."""

import django.db.models.deletion
from django.db import migrations, models


def drop_live_items(apps, schema_editor):
    apps.get_model("scheduling", "GrantItem").objects.filter(video__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0003_grant_live_items"),
        ("fk", "0049_live_transmission_requires_video"),
    ]

    operations = [
        migrations.RunPython(drop_live_items, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="grantitem", name="grantitem_video_or_live"),
        migrations.RemoveField(model_name="grantitem", name="live_source"),
        migrations.RemoveField(model_name="grantitem", name="duration"),
        migrations.RemoveField(model_name="grantitem", name="title"),
        migrations.AlterField(
            model_name="grantitem",
            name="video",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="grant_items",
                to="fk.video",
            ),
        ),
    ]
