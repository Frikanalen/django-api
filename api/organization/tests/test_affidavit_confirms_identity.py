"""Receiving the editor's signed affidavit is what confirms who they are.

Staff record it by making the organization a member; there is no separate
identity step for an editor.
"""

import pytest

from fk.models import Organization, User
from scheduling.services import may_act_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def new_editor() -> User:
    return User.objects.create(email="new-editor@example.test")


@pytest.fixture
def applicant(new_editor: User) -> Organization:
    return Organization.objects.create(name="Applicant", orgnr="971526920", editor=new_editor)


def test_approving_membership_confirms_the_editor(
    applicant: Organization, new_editor: User
) -> None:
    assert not may_act_for(new_editor, applicant)

    applicant.fkmember = True
    applicant.save()

    new_editor.refresh_from_db()
    assert new_editor.identity_confirmed
    assert may_act_for(new_editor, applicant)


def test_a_new_member_created_as_such_confirms_its_editor(new_editor: User) -> None:
    Organization.objects.create(name="Direct", fkmember=True, editor=new_editor)

    new_editor.refresh_from_db()
    assert new_editor.identity_confirmed


def test_a_change_of_editor_does_not_confirm_the_new_one(
    applicant: Organization, new_editor: User
) -> None:
    applicant.fkmember = True
    applicant.save()
    successor = User.objects.create(email="successor@example.test")

    applicant.editor = successor
    applicant.save()

    successor.refresh_from_db()
    assert not successor.identity_confirmed


def test_members_other_than_the_editor_are_not_confirmed(
    applicant: Organization, new_editor: User
) -> None:
    member = User.objects.create(email="member@example.test")
    applicant.members.add(member)

    applicant.fkmember = True
    applicant.save()

    member.refresh_from_db()
    assert not member.identity_confirmed
