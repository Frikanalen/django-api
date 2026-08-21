"""Seed the TV-Anytime genre for the categories shipped in the fixture.

The mapping is an editorial judgement, not a derivation, so it is seeded
rather than computed: `Category.tva_genre` is an admin-editable field and
whatever is set there afterwards wins. Only blank rows are filled, so
re-running this never overwrites a decision somebody made.

Two rules governed the choices below. Where a controlled term fits, it is
used. Where none does, the nearest true *ancestor* is used rather than a
plausible-looking sibling -- filing children's programming under
"General non-fiction" is imprecise, filing it under "Education" would be
false, and an EPG shows the false one to viewers.
"""

from django.db import migrations

CONTENT_CS = "urn:tva:metadata:cs:ContentCS:2011"

# Keyed by the name in fk/fixtures/frikanalen.json. Names rather than
# primary keys because a deployment that renamed a category has made an
# editorial change this migration should not quietly reinterpret.
GENRES = {
    # No controlled term, and none needed: "Annet" is the residual bucket,
    # so it says nothing about the programme that a genre could carry.
    "Annet": "",
    # ContentCS 2011 has no children's or youth genre -- the closest thing
    # in TV-Anytime is IntendedAudienceCS, which BasicContentDescription
    # has no element for. The parent term is true but coarse; see docs.
    "Barn og ungdom": f"{CONTENT_CS}:3.1.3",
    # Civil preparedness. Deliberately *not* 3.1.3.8 Military/Defence,
    # which would misdescribe a public-access channel's content.
    "Beredskap": f"{CONTENT_CS}:3.1.3",
    "Idrett": f"{CONTENT_CS}:3.2",  # SPORTS
    "Kultur": f"{CONTENT_CS}:3.1.4",  # Arts
    # Culture/Tradition/Anthropology/Ethnic studies
    "Minoriteter": f"{CONTENT_CS}:3.1.5.4",
    "Religion/livssyn": f"{CONTENT_CS}:3.1.2",  # Religion/Philosophies
    "Samfunn": f"{CONTENT_CS}:3.1.3.2",  # Social
    "Solidaritet og bistand": f"{CONTENT_CS}:3.1.3.7",  # International affairs
    "Velferd": f"{CONTENT_CS}:3.1.3.2",  # Social
}


def seed(apps, schema_editor):
    Category = apps.get_model("fk", "Category")
    for name, href in GENRES.items():
        if not href:
            continue
        Category.objects.filter(name=name, tva_genre="").update(tva_genre=href)


def unseed(apps, schema_editor):
    """Clear only the values this migration could have set.

    Reversing must not discard an editor's own mapping, so a row is only
    cleared if it still holds exactly what was seeded into it.
    """
    Category = apps.get_model("fk", "Category")
    for name, href in GENRES.items():
        if not href:
            continue
        Category.objects.filter(name=name, tva_genre=href).update(tva_genre="")


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0030_tvanytime_metadata"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
