"""Second half of the move of live transmissions onto videos: drop the
link to the schedule item and require the video."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0048_live_transmission_on_video"),
    ]

    operations = [
        migrations.RemoveField(model_name="livetransmission", name="schedule_item"),
        migrations.AlterField(
            model_name="livetransmission",
            name="video",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="live_transmission",
                to="fk.video",
            ),
        ),
    ]
