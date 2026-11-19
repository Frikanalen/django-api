import logging

import pytest

from api.organization.serializers import OrganizationSerializer
from fk.models import Organization, User

pytestmark = pytest.mark.django_db


def test_serializer_includes_organization_and_editor_details(
    organization: Organization,
    editor: User,
) -> None:
    assert OrganizationSerializer(organization).data == {
        "id": organization.pk,
        "name": "Organization test group",
        "homepage": "https://example.test",
        "description": "An organization used by the API tests.",
        "orgnr": "",
        "phone_number": "+4722334455",
        "postal_address": "Postboks 1",
        "street_address": "Testgata 2",
        "editor_id": editor.pk,
        "editor_name": "Ada Lovelace",
        "editor_email": "organization-editor@example.test",
        "editor_msisdn": "+47 22 33 44 55",
        "fkmember": True,
        "is_rogue": False,
    }


@pytest.mark.parametrize("phone_number", ["", "not a number", "12345"])
def test_serializer_reports_no_usable_phone_number_as_null(editor: User, phone_number: str) -> None:
    organization = Organization.objects.create(
        name="No phone", editor=editor, phone_number=phone_number
    )

    assert OrganizationSerializer(organization).data["editor_msisdn"] is None


def test_the_editors_personal_number_is_never_listed(editor: User) -> None:
    # The editor fixture has a personal number; the organization has none.
    organization = Organization.objects.create(name="No phone", editor=editor)

    assert OrganizationSerializer(organization).data["editor_msisdn"] is None


def test_a_norwegian_number_may_be_given_without_country_code(editor: User) -> None:
    serializer = OrganizationSerializer(
        data={"name": "Local number", "orgnr": "971526920", "phone_number": "22 33 44 55"}
    )

    assert serializer.is_valid(), serializer.errors
    organization = serializer.save(editor=editor)
    assert OrganizationSerializer(organization).data["editor_msisdn"] == "+47 22 33 44 55"


def test_create_requires_an_orgnr() -> None:
    serializer = OrganizationSerializer(data={"name": "No orgnr"})

    assert not serializer.is_valid()
    assert list(serializer.errors) == ["orgnr"]


def test_orgnr_cannot_be_blanked(organization: Organization) -> None:
    serializer = OrganizationSerializer(organization, data={"orgnr": ""}, partial=True)

    assert not serializer.is_valid()
    assert list(serializer.errors) == ["orgnr"]


def test_an_older_organization_without_orgnr_can_still_be_updated(
    organization: Organization,
) -> None:
    serializer = OrganizationSerializer(organization, data={"name": "Renamed"}, partial=True)

    assert serializer.is_valid(), serializer.errors


def test_serializer_handles_an_organization_without_an_editor(caplog) -> None:
    organization = Organization.objects.create(name="No editor")

    with caplog.at_level(logging.WARNING, logger="api.serializers"):
        data = OrganizationSerializer(organization).data

    assert data["editor_id"] is None
    assert data["editor_name"] == "Ingen redaktør!"
    assert data["editor_email"] is None
    assert data["editor_msisdn"] is None
    assert f"Organization {organization.pk} has no editor assigned" in caplog.text


def test_derived_and_membership_fields_are_read_only(editor: User) -> None:
    serializer = OrganizationSerializer(
        data={
            "name": "Submitted organization",
            "orgnr": "971526920",
            "editor_id": 999999,
            "editor_name": "Injected Name",
            "editor_email": "injected@example.test",
            "editor_msisdn": "+4712345678",
            "fkmember": True,
        }
    )

    assert serializer.is_valid(), serializer.errors
    organization = serializer.save(editor=editor)

    assert organization.editor == editor
    assert organization.fkmember is False
