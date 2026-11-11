"""Weekly slots become recurring slots; their sources become fill rules.

Each WeeklySlot becomes a weekly, interval-1 RecurringSlot on the same
weekday, starting on the migration date, keeping its id so the slot
requests can follow it. Each WeeklySlotSource becomes a FillRule with
the same id: organization sources draw from the organization's uploads,
hand-picked ones from a new Playlist of the same videos. Sources placed
one video per occurrence, so the rules do too (mode "one"); latest stays
latest, random becomes shuffled and least_scheduled least_aired.

Grants are not issued here: that is the cut-over (cutover_schedule).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.color import no_style
from django.db import migrations

STRATEGY = {"latest": "latest", "random": "shuffled", "least_scheduled": "least_aired"}


def forward(apps, schema_editor):
    WeeklySlot = apps.get_model("fk", "WeeklySlot")
    WeeklySlotSource = apps.get_model("fk", "WeeklySlotSource")
    CreationRequest = apps.get_model("fk", "RecurringSlotCreationRequest")
    OwnershipRequest = apps.get_model("fk", "RecurringSlotOwnershipRequest")
    RecurringSlot = apps.get_model("scheduling", "RecurringSlot")
    FillRule = apps.get_model("scheduling", "FillRule")
    Playlist = apps.get_model("scheduling", "Playlist")

    today = datetime.now(ZoneInfo("Europe/Oslo")).date()

    rules = set()
    for source in WeeklySlotSource.objects.all():
        organization_id = source.organization_id or (
            WeeklySlot.objects.filter(source=source, organization__isnull=False)
            .values_list("organization_id", flat=True)
            .first()
        )
        if organization_id is None:
            continue  # owned by nobody and used by no owned slot: nothing to carry over
        playlist = None
        pool = "organization"
        if source.type == "videos":
            pool = "playlist"
            playlist = Playlist.objects.create(organization_id=organization_id, name=source.name)
            playlist.videos.set(source.direct_videos.all())
        FillRule.objects.create(
            id=source.id,
            organization_id=organization_id,
            name=source.name,
            pool=pool,
            playlist=playlist,
            ordering=STRATEGY[source.strategy],
            mode="one",
        )
        rules.add(source.id)

    for slot in WeeklySlot.objects.all():
        RecurringSlot.objects.create(
            id=slot.id,
            organization_id=slot.organization_id,
            frequency="weekly",
            interval=1,
            weekdays=[slot.day],
            start_time=slot.start_time,
            duration=slot.duration,
            starts_on=today,
            default_fill_rule_id=slot.source_id if slot.source_id in rules else None,
        )

    for request in CreationRequest.objects.filter(weekly_slot__isnull=False):
        request.slot_id = request.weekly_slot_id
        request.save(update_fields=["slot"])
    for request in OwnershipRequest.objects.all():
        request.slot_id = request.weekly_slot_id
        request.save(update_fields=["slot"])

    # Rows were inserted with explicit ids; move the sequences past them.
    with schema_editor.connection.cursor() as cursor:
        for sql in schema_editor.connection.ops.sequence_reset_sql(
            no_style(), [RecurringSlot, FillRule]
        ):
            cursor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0001_initial"),
        ("fk", "0046_airtime_grants"),
    ]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
