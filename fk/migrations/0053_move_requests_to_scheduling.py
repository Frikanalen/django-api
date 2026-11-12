"""The slot request models move to scheduling (see the airtime grants ADR).

State only: the tables stay, and scheduling/0005 takes them over.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("fk", "0052_suspend_rogue_organizations"),
        # scheduling/0002 still reads these models from fk.
        ("scheduling", "0004_drop_grant_live_items"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
            migrations.RemoveField(
                model_name="recurringslotownershiprequest",
                name="organization",
            ),
            migrations.RemoveField(
                model_name="recurringslotownershiprequest",
                name="previous_organization",
            ),
            migrations.RemoveField(
                model_name="recurringslotownershiprequest",
                name="requested_by",
            ),
            migrations.RemoveField(
                model_name="recurringslotownershiprequest",
                name="reviewed_by",
            ),
            migrations.RemoveField(
                model_name="recurringslotownershiprequest",
                name="slot",
            ),
            migrations.DeleteModel(
                name="RecurringSlotCreationRequest",
            ),
            migrations.DeleteModel(
                name="RecurringSlotOwnershipRequest",
            ),
            ],
            database_operations=[],
        ),
    ]
