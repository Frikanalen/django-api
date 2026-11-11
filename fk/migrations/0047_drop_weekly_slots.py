"""Weekly slots and their sources are gone; slot requests point at
recurring slots (copied in scheduling 0002)."""

import datetime

import django.db.models.deletion
from django.db import migrations, models


def audit(name):
    return models.CheckConstraint(
        condition=models.Q(
            models.Q(
                ("reviewed_at__isnull", True),
                ("reviewed_by__isnull", True),
                ("status", "pending"),
            ),
            models.Q(
                ("reviewed_at__isnull", False),
                ("reviewed_by__isnull", False),
                ("status__in", ("approved", "denied")),
            ),
            _connector="OR",
        ),
        name=name,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0046_airtime_grants"),
        ("scheduling", "0002_from_weekly_slots"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="recurringslotcreationrequest",
            name="weeklyslotcreationrequest_duration_not_negative",
        ),
        migrations.RemoveConstraint(
            model_name="recurringslotcreationrequest",
            name="weeklyslotcreationrequest_decision_audit_consistent",
        ),
        migrations.RemoveConstraint(
            model_name="recurringslotcreationrequest",
            name="weeklyslotcreationrequest_result_consistent",
        ),
        migrations.RemoveConstraint(
            model_name="recurringslotownershiprequest",
            name="weeklyslotownershiprequest_decision_audit_consistent",
        ),
        migrations.RemoveConstraint(
            model_name="recurringslotownershiprequest",
            name="one_pending_ownership_request_per_organization_slot",
        ),
        migrations.RemoveField(model_name="recurringslotcreationrequest", name="weekly_slot"),
        migrations.RemoveField(model_name="recurringslotownershiprequest", name="weekly_slot"),
        migrations.AlterField(
            model_name="recurringslotownershiprequest",
            name="slot",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ownership_requests",
                to="scheduling.recurringslot",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringslotcreationrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(("duration__gte", datetime.timedelta(0))),
                name="recurringslotcreationrequest_duration_not_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringslotcreationrequest",
            constraint=audit("recurringslotcreationrequest_decision_audit_consistent"),
        ),
        migrations.AddConstraint(
            model_name="recurringslotcreationrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("slot__isnull", False), ("status", "approved")),
                    models.Q(("slot__isnull", True), ("status__in", ("pending", "denied"))),
                    _connector="OR",
                ),
                name="recurringslotcreationrequest_result_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringslotownershiprequest",
            constraint=audit("recurringslotownershiprequest_decision_audit_consistent"),
        ),
        migrations.AddConstraint(
            model_name="recurringslotownershiprequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("organization", "slot"),
                name="one_pending_ownership_request_per_organization_slot",
            ),
        ),
        migrations.RemoveField(model_name="scheduleitem", name="weekly_slot"),
        migrations.DeleteModel(name="WeeklySlot"),
        migrations.DeleteModel(name="WeeklySlotSource"),
    ]
