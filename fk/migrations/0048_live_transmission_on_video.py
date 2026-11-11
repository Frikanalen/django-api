"""A live transmission belongs to the video, not to one airing.

Each booking's live transmission moves to the booking's video, and the
booking is marked live. A live booking without a video gets a
placeholder video in the source's organization, named after the
booking, as staff have been making by hand. Where several bookings of
one video carried a source, the latest booking's source wins.
"""

import django.db.models.deletion
from django.db import migrations, models

# A live transmission no longer has timing of its own to announce: making a
# video live or not changes the schedule through the items it recompiles,
# whose own triggers announce it. So the live table's triggers go, and the
# function keeps only the schedule item branch.
SCHEDULE_ONLY_TRIGGER = """
DROP TRIGGER IF EXISTS fk_livetransmission_notify_insert ON fk_livetransmission;
DROP TRIGGER IF EXISTS fk_livetransmission_notify_update ON fk_livetransmission;
DROP TRIGGER IF EXISTS fk_livetransmission_notify_delete ON fk_livetransmission;
CREATE OR REPLACE FUNCTION fk_notify_schedule_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    window_start timestamptz;
    window_end timestamptz;
    row_count bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT min(starttime), max(upper(airtime)), count(*)
          INTO window_start, window_end, row_count FROM changed_new;
    ELSIF TG_OP = 'UPDATE' THEN
        -- Old and new versions of every row: count each row once.
        SELECT min(starttime), max(upper(airtime)), count(*) / 2
          INTO window_start, window_end, row_count
          FROM (SELECT starttime, airtime FROM changed_new
                UNION ALL
                SELECT starttime, airtime FROM changed_old) AS touched;
    ELSE
        SELECT min(starttime), max(upper(airtime)), count(*)
          INTO window_start, window_end, row_count FROM changed_old;
    END IF;

    -- A statement that matched no rows changed nothing.
    IF row_count = 0 THEN
        RETURN NULL;
    END IF;

    PERFORM pg_notify('playout_events', json_build_object(
        'type', 'schedule.changed',
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'count', row_count,
        'from', window_start,
        'until', window_end
    )::text);
    RETURN NULL;
END;
$$;
"""


def to_videos(apps, schema_editor):
    LiveTransmission = apps.get_model("fk", "LiveTransmission")
    Video = apps.get_model("fk", "Video")
    User = apps.get_model("fk", "User")
    claimed = set()
    for live in LiveTransmission.objects.select_related(
        "schedule_item__video", "source__organization"
    ).order_by("-schedule_item__starttime"):
        item = live.schedule_item
        if item.video_id is None:
            organization = live.source.organization
            creator = organization.editor or User.objects.filter(is_superuser=True).first()
            if creator is None:
                live.delete()
                continue
            item.video = Video.objects.create(
                name=item.default_name or live.source.name,
                creator=creator,
                organization=organization,
                duration=item.duration,
                proper_import=False,
                publish_on_web=False,
            )
        item.is_live = True
        item.save(update_fields=["video", "is_live"])
        if item.video_id in claimed:
            live.delete()
            continue
        claimed.add(item.video_id)
        live.video_id = item.video_id
        live.save(update_fields=["video"])


class Migration(migrations.Migration):
    # The schema change that ends the move is the next migration: Postgres
    # won't alter a table with trigger events pending from the data step.
    dependencies = [
        ("fk", "0047_drop_weekly_slots"),
    ]

    operations = [
        migrations.RunSQL(SCHEDULE_ONLY_TRIGGER, migrations.RunSQL.noop),
        migrations.AddField(
            model_name="livetransmission",
            name="video",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="live_transmission",
                to="fk.video",
            ),
        ),
        migrations.RunPython(to_videos, migrations.RunPython.noop),
    ]
