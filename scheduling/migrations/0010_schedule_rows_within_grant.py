"""A grant's compiled rows lie inside the grant's window.

What a grant *lists* can't be checked by the database: where each entry
plays is computed (scheduling.layout), and a programme cut short on air
stays listed. What it put on the *schedule* can: every Scheduleitem that
belongs to a grant must sit inside that grant's airtime. Checked at
commit (deferred), so a change that moves the end and the rows together
in one transaction passes.
"""

from django.db import migrations

FUNCTION = """
CREATE OR REPLACE FUNCTION scheduling_rows_within_grant() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    offending bigint;
BEGIN
    IF TG_TABLE_NAME = 'fk_scheduleitem' THEN
        IF NEW.grant_id IS NULL THEN
            RETURN NULL;
        END IF;
        SELECT s.id INTO offending
          FROM fk_scheduleitem s
          JOIN scheduling_airtimegrant g ON g.id = s.grant_id
         WHERE s.id = NEW.id AND NOT (s.airtime <@ g.airtime);
    ELSE
        SELECT s.id INTO offending
          FROM fk_scheduleitem s
         WHERE s.grant_id = NEW.id AND NOT (s.airtime <@ NEW.airtime)
         LIMIT 1;
    END IF;
    IF offending IS NOT NULL THEN
        RAISE EXCEPTION 'schedule item % lies outside its airtime grant''s window', offending
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'scheduleitem_within_grant';
    END IF;
    RETURN NULL;
END;
$$;
"""

TRIGGERS = """
CREATE CONSTRAINT TRIGGER scheduleitem_within_grant
    AFTER INSERT OR UPDATE OF starttime, duration, grant_id ON fk_scheduleitem
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION scheduling_rows_within_grant();
CREATE CONSTRAINT TRIGGER airtimegrant_holds_its_rows
    AFTER UPDATE OF starttime, endtime ON scheduling_airtimegrant
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION scheduling_rows_within_grant();
"""

DROP = """
DROP TRIGGER IF EXISTS airtimegrant_holds_its_rows ON scheduling_airtimegrant;
DROP TRIGGER IF EXISTS scheduleitem_within_grant ON fk_scheduleitem;
DROP FUNCTION IF EXISTS scheduling_rows_within_grant();
"""

# Rows already outside their grant are reported, not changed: they may be
# on air, and which way to fix each is a person's call.
REPORT = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT s.id AS item, g.id AS grant_id
          FROM fk_scheduleitem s JOIN scheduling_airtimegrant g ON g.id = s.grant_id
         WHERE NOT (s.airtime <@ g.airtime)
    LOOP
        RAISE WARNING 'schedule item % lies outside grant %', r.item, r.grant_id;
    END LOOP;
END;
$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0009_bulletin_paging"),
    ]

    operations = [
        migrations.RunSQL(REPORT, migrations.RunSQL.noop),
        migrations.RunSQL(FUNCTION + TRIGGERS, DROP),
    ]
