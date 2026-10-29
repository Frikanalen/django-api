"""Add the generated airtime range and its GiST index.

ADD COLUMN ... STORED rewrites the table under ACCESS EXCLUSIVE and has
no concurrent form, so this is the one migration here that wants a quiet
moment. The GiST index is built in the same lock window on purpose --
there is nothing to gain from building it concurrently while the rewrite
already holds the table.
"""

import django.contrib.postgres.fields.ranges
import django.contrib.postgres.indexes
import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0028_videofile_drop_redundant_video_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduleitem",
            name="airtime",
            field=models.GeneratedField(
                db_persist=True,
                expression=models.Func(
                    models.F("starttime"),
                    models.Func(
                        models.Value("UTC"),
                        models.ExpressionWrapper(
                            django.db.models.expressions.CombinedExpression(
                                models.Func(
                                    models.Value("UTC"),
                                    models.F("starttime"),
                                    function="timezone",
                                ),
                                "+",
                                models.F("duration"),
                            ),
                            output_field=models.DateTimeField(),
                        ),
                        function="timezone",
                    ),
                    models.Value("[)"),
                    function="tstzrange",
                ),
                output_field=django.contrib.postgres.fields.ranges.DateTimeRangeField(),
            ),
        ),
        migrations.AddIndex(
            model_name="scheduleitem",
            index=django.contrib.postgres.indexes.GistIndex(
                fields=["airtime"], name="scheduleitem_airtime_gist"
            ),
        ),
    ]
