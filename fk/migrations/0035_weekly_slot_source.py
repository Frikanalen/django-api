from django.db import migrations, models


class Migration(migrations.Migration):
    """SchedulePurpose becomes WeeklySlotSource, and the slot's `purpose`
    becomes `source`.

    Renames only: the table and the column change name, nothing changes
    shape, and Postgres does both as catalogue edits. The API keeps
    publishing the field as `purpose` -- see WeeklySlotReadSerializer --
    so no client has to move at the same time as the database.
    """

    dependencies = [
        ("fk", "0034_slot_source_vocabulary"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="SchedulePurpose",
            new_name="WeeklySlotSource",
        ),
        migrations.AlterModelOptions(
            name="weeklyslotsource",
            options={"ordering": ("-id",)},
        ),
        migrations.RenameField(
            model_name="weeklyslot",
            old_name="purpose",
            new_name="source",
        ),
        migrations.AlterField(
            model_name="weeklyslot",
            name="source",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Which source picks the video for this slot. Blank means the slot "
                    "reserves airtime that nothing is scheduled into automatically."
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                to="fk.weeklyslotsource",
            ),
        ),
    ]
