"""The shipped category fixture, which a fresh install loads.

`loaddata frikanalen` is a documented setup step, so the fixture has to
stay loadable and stay in step with the model. It also has to agree with
the data migration that seeds the same genres into existing databases --
otherwise a fresh install and an upgraded one publish different EPGs.
"""

import importlib

import pytest
from django.core.management import call_command

from fk.models import Category

pytestmark = pytest.mark.django_db

# Imported through importlib because a migration's module name starts with
# a digit and so cannot be written as an import statement. Reaching into
# one is unusual, and deliberate: the seeded mapping should have exactly
# one definition, and this is where it lives.
SEEDED_GENRES = importlib.import_module("fk.migrations.0031_seed_tva_genres").GENRES


@pytest.fixture
def loaded() -> None:
    call_command("loaddata", "frikanalen", verbosity=0)


def test_fixture_loads_and_carries_every_category(loaded: None) -> None:
    assert Category.objects.count() == 10


def test_every_category_is_mapped_or_deliberately_blank(loaded: None) -> None:
    """Blank is a decision here, not an oversight -- see docs/tvanytime.md
    -- so the test names the categories allowed to hold it rather than
    letting any of them quietly empty out."""
    blank = set(Category.objects.filter(tva_genre="").values_list("name", flat=True))
    assert blank == {"Annet"}


def test_fixture_and_migration_seed_the_same_genres(loaded: None) -> None:
    """A fresh install and an upgraded one must publish the same genres."""
    from_fixture = dict(Category.objects.values_list("name", "tva_genre"))
    assert from_fixture == SEEDED_GENRES
