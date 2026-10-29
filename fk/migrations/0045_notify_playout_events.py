"""Announce every schedule change on the `playout_events` channel.

Playout and the on-air graphics subscribe to /api/playout/events, which
relays these notifications. Doing it in the database rather than in a Django
signal means admin edits, bulk updates, fixtures and raw SQL are announced
too, and every API process hears them without a broker in between.

Statement-level triggers with transition tables, so a bulk change of a
week's schedule is one notification covering its time window rather than
hundreds. Postgres allows transition tables on single-event triggers only,
hence three triggers per table sharing one function. Notifications are sent
at commit, so a consumer that refetches on one sees the committed rows.
"""

from django.db import migrations

CHANNEL = "playout_events"
TABLES = ("fk_scheduleitem", "fk_livetransmission")

# Each trigger may only name the transition tables its own event has, and
# PL/pgSQL plans a statement the first time it runs it, so every branch
# below reads only the tables that exist for its TG_OP.
FORWARD = f"""
CREATE FUNCTION fk_notify_schedule_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    window_start timestamptz;
    window_end timestamptz;
    row_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'fk_scheduleitem' THEN
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
    ELSE
        -- fk_livetransmission: its timing lives on the schedule item. On a
        -- cascade from a deleted item the join finds nothing, which is fine:
        -- the item's own trigger announces that window.
        IF TG_OP = 'INSERT' THEN
            SELECT min(s.starttime), max(upper(s.airtime)), count(*)
              INTO window_start, window_end, row_count
              FROM changed_new t LEFT JOIN fk_scheduleitem s ON s.id = t.schedule_item_id;
        ELSIF TG_OP = 'UPDATE' THEN
            SELECT min(s.starttime), max(upper(s.airtime)), count(*) / 2
              INTO window_start, window_end, row_count
              FROM (SELECT schedule_item_id FROM changed_new
                    UNION ALL
                    SELECT schedule_item_id FROM changed_old) AS t
              LEFT JOIN fk_scheduleitem s ON s.id = t.schedule_item_id;
        ELSE
            SELECT min(s.starttime), max(upper(s.airtime)), count(*)
              INTO window_start, window_end, row_count
              FROM changed_old t LEFT JOIN fk_scheduleitem s ON s.id = t.schedule_item_id;
        END IF;
    END IF;

    -- A statement that matched no rows changed nothing.
    IF row_count = 0 THEN
        RETURN NULL;
    END IF;

    PERFORM pg_notify('{CHANNEL}', json_build_object(
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


def _triggers(table: str) -> str:
    return f"""
CREATE TRIGGER {table}_notify_insert AFTER INSERT ON {table}
    REFERENCING NEW TABLE AS changed_new
    FOR EACH STATEMENT EXECUTE FUNCTION fk_notify_schedule_changed();
CREATE TRIGGER {table}_notify_update AFTER UPDATE ON {table}
    REFERENCING NEW TABLE AS changed_new OLD TABLE AS changed_old
    FOR EACH STATEMENT EXECUTE FUNCTION fk_notify_schedule_changed();
CREATE TRIGGER {table}_notify_delete AFTER DELETE ON {table}
    REFERENCING OLD TABLE AS changed_old
    FOR EACH STATEMENT EXECUTE FUNCTION fk_notify_schedule_changed();
"""


REVERSE = (
    "".join(
        f"DROP TRIGGER IF EXISTS {table}_notify_{op} ON {table};\n"
        for table in TABLES
        for op in ("insert", "update", "delete")
    )
    + "DROP FUNCTION IF EXISTS fk_notify_schedule_changed();\n"
)


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0044_streamcredential_playout"),
    ]

    operations = [
        migrations.RunSQL(
            sql=FORWARD + "".join(_triggers(table) for table in TABLES),
            reverse_sql=REVERSE,
        ),
    ]
