"""Suspend the members that tried to subvert the general assembly.

Matched by exact name; the flag is the switch from here on and can be
cleared in the admin when a suspension is lifted.
"""

from django.db import migrations

SUSPENDED = ("PROION", "Medieovervåkerne", "Trond Ali Linstad")


def suspend(apps, schema_editor):
    Organization = apps.get_model("fk", "Organization")
    Organization.objects.filter(name__in=SUSPENDED).update(is_rogue=True)


def reinstate(apps, schema_editor):
    Organization = apps.get_model("fk", "Organization")
    Organization.objects.filter(name__in=SUSPENDED).update(is_rogue=False)


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0051_organization_is_rogue"),
    ]

    operations = [migrations.RunPython(suspend, reinstate)]
