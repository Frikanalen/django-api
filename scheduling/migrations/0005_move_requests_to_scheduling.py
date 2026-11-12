"""Requests live in scheduling.

The two slot request models arrive from fk with their tables (state
only), then the tables get scheduling names. Every request gains a
message, and airtime and exemption requests are new.
"""

import datetime
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fk", "0053_move_requests_to_scheduling"),
        ("scheduling", "0004_drop_grant_live_items"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AirtimeGrantRequest",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("denied", "Denied"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "message",
                    models.TextField(
                        blank=True,
                        help_text="What the organization wants Frikanalen to know.",
                    ),
                ),
                ("admin_comment", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("starttime", models.DateTimeField()),
                ("endtime", models.DateTimeField()),
                (
                    "grant",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="request",
                        to="scheduling.airtimegrant",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="fk.organization",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="airtime_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_airtime_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("endtime__gt", models.F("starttime"))),
                        name="airtimegrantrequest_ends_after_start",
                    ),
                    models.CheckConstraint(
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
                        name="airtimegrantrequest_decision_audit_consistent",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GrantExemptionRequest",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("denied", "Denied"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "message",
                    models.TextField(
                        blank=True,
                        help_text="What the organization wants Frikanalen to know.",
                    ),
                ),
                ("admin_comment", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "grant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exemption_requests",
                        to="scheduling.airtimegrant",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="fk.organization",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="exemption_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_exemption_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "constraints": [
                    models.CheckConstraint(
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
                        name="grantexemptionrequest_decision_audit_consistent",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("status", "pending")),
                        fields=("grant",),
                        name="one_pending_exemption_request_per_grant",
                    ),
                ],
            },
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="RecurringSlotCreationRequest",
                    fields=[
                        (
                            "id",
                            models.AutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("pending", "Pending"),
                                    ("approved", "Approved"),
                                    ("denied", "Denied"),
                                ],
                                default="pending",
                                max_length=16,
                            ),
                        ),
                        ("admin_comment", models.TextField(blank=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                        (
                            "day",
                            models.IntegerField(
                                choices=[
                                    (0, "Monday"),
                                    (1, "Tuesday"),
                                    (2, "Wednesday"),
                                    (3, "Thursday"),
                                    (4, "Friday"),
                                    (5, "Saturday"),
                                    (6, "Sunday"),
                                ]
                            ),
                        ),
                        ("start_time", models.TimeField()),
                        (
                            "duration",
                            models.DurationField(
                                validators=[
                                    django.core.validators.MinValueValidator(
                                        datetime.timedelta(0)
                                    )
                                ]
                            ),
                        ),
                        (
                            "organization",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="fk.organization",
                            ),
                        ),
                        (
                            "requested_by",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="slot_creation_requests",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "reviewed_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="reviewed_slot_creation_requests",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "slot",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="creation_requests",
                                to="scheduling.recurringslot",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "fk_recurringslotcreationrequest",
                        "ordering": ("-created_at", "-id"),
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("duration__gte", datetime.timedelta(0))),
                                name="recurringslotcreationrequest_duration_not_negative",
                            ),
                            models.CheckConstraint(
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
                                name="recurringslotcreationrequest_decision_audit_consistent",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(("slot__isnull", False), ("status", "approved")),
                                    models.Q(
                                        ("slot__isnull", True),
                                        ("status__in", ("pending", "denied")),
                                    ),
                                    _connector="OR",
                                ),
                                name="recurringslotcreationrequest_result_consistent",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="RecurringSlotOwnershipRequest",
                    fields=[
                        (
                            "id",
                            models.AutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("pending", "Pending"),
                                    ("approved", "Approved"),
                                    ("denied", "Denied"),
                                ],
                                default="pending",
                                max_length=16,
                            ),
                        ),
                        ("admin_comment", models.TextField(blank=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                        (
                            "organization",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="fk.organization",
                            ),
                        ),
                        (
                            "previous_organization",
                            models.ForeignKey(
                                blank=True,
                                help_text="The target slot's owner when the request was submitted.",
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="slot_transfer_requests",
                                to="fk.organization",
                            ),
                        ),
                        (
                            "requested_by",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="slot_ownership_requests",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "reviewed_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="reviewed_slot_ownership_requests",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "slot",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="ownership_requests",
                                to="scheduling.recurringslot",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "fk_recurringslotownershiprequest",
                        "ordering": ("-created_at", "-id"),
                        "constraints": [
                            models.CheckConstraint(
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
                                name="recurringslotownershiprequest_decision_audit_consistent",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(("status", "pending")),
                                fields=("organization", "slot"),
                                name="one_pending_ownership_request_per_organization_slot",
                            ),
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterModelTable(name="recurringslotcreationrequest", table=None),
        migrations.AlterModelTable(name="recurringslotownershiprequest", table=None),
        migrations.AddField(
            model_name="recurringslotcreationrequest",
            name="message",
            field=models.TextField(
                blank=True, help_text="What the organization wants Frikanalen to know."
            ),
        ),
        migrations.AddField(
            model_name="recurringslotownershiprequest",
            name="message",
            field=models.TextField(
                blank=True, help_text="What the organization wants Frikanalen to know."
            ),
        ),
    ]
