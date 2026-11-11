"""Schedule items get a grant; slot requests get ready to point at
recurring slots. See the airtime grants ADR.

Nothing here touches existing schedule rows: the no-overlap constraint
covers only rows without a schedule reason, which until now none have.
"""

import django.contrib.postgres.constraints
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0045_notify_playout_events"),
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduleitem",
            name="grant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="schedule_items",
                to="scheduling.airtimegrant",
            ),
        ),
        migrations.AlterField(
            model_name="scheduleitem",
            name="schedulereason",
            field=models.IntegerField(
                blank=True,
                choices=[
                    (1, "Legacy"),
                    (2, "Administrative"),
                    (3, "User"),
                    (4, "Automatic"),
                    (5, "Jukebox"),
                ],
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduleitem",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                condition=models.Q(("schedulereason__isnull", True)),
                expressions=[("airtime", "&&")],
                name="scheduleitem_no_overlap",
                violation_error_message="Another programme is scheduled at that time.",
            ),
        ),
        migrations.RenameModel("WeeklySlotCreationRequest", "RecurringSlotCreationRequest"),
        migrations.RenameModel("WeeklySlotOwnershipRequest", "RecurringSlotOwnershipRequest"),
        migrations.AddField(
            model_name="recurringslotcreationrequest",
            name="slot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="creation_requests",
                to="scheduling.recurringslot",
            ),
        ),
        migrations.AddField(
            model_name="recurringslotownershiprequest",
            name="slot",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ownership_requests",
                to="scheduling.recurringslot",
            ),
        ),
        migrations.AlterField(
            model_name="recurringslotcreationrequest",
            name="requested_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="slot_creation_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="recurringslotcreationrequest",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reviewed_slot_creation_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="recurringslotownershiprequest",
            name="requested_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="slot_ownership_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="recurringslotownershiprequest",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reviewed_slot_ownership_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="recurringslotownershiprequest",
            name="previous_organization",
            field=models.ForeignKey(
                blank=True,
                help_text="The target slot's owner when the request was submitted.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="slot_transfer_requests",
                to="fk.organization",
            ),
        ),
    ]
