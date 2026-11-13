import io
import json
import urllib.error

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk import brreg

# Real-looking numbers: the first passes the modulus 11 check, the second does not.
VALID = "971526920"
INVALID_CHECK = "971526921"

ENTITY = {
    "organisasjonsnummer": VALID,
    "navn": "TESTFORENINGEN",
    "organisasjonsform": {"kode": "FLI", "beskrivelse": "Forening/lag/innretning"},
    "hjemmeside": "www.testforeningen.no",
    "vedtektsfestetFormaal": ["Foreningen skal lage", "lokal-TV."],
    "forretningsadresse": {
        "land": "Norge",
        "landkode": "NO",
        "postnummer": "0150",
        "poststed": "OSLO",
        "adresse": ["Testgata 1", None],
    },
    "konkurs": False,
    "underAvvikling": False,
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def answer(monkeypatch, body=None, code=None):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if code:
            raise urllib.error.HTTPError(request.full_url, code, "", {}, None)
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(brreg.urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize(
    "value, valid",
    [
        (VALID, True),
        (INVALID_CHECK, False),
        ("12345678", False),
        ("97152692a", False),
        ("", False),
    ],
)
def test_orgnr_check_digit(value, valid):
    assert brreg.is_valid_orgnr(value) is valid


def test_number_whose_check_digit_would_be_ten_is_invalid():
    # Such numbers are never issued: no ninth digit can make them valid.
    for n in range(10000000, 10001000):
        base = str(n).zfill(8)
        remainder = sum(int(d) * w for d, w in zip(base, brreg.WEIGHTS)) % 11
        if remainder == 1:
            assert not any(brreg.is_valid_orgnr(base + str(d)) for d in range(10))
            return
    pytest.fail("no example found")


@pytest.fixture
def client(db):
    from fk.models import User

    user = User.objects.create(email="brreg@example.test")
    # The register being down is a server error, which the error handler
    # reports; let the response through rather than re-raise it.
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(user=user)
    return client


def url(orgnr):
    return reverse("api-organization-brreg-lookup", args=[orgnr])


def test_lookup_returns_our_fields(client, monkeypatch):
    calls = answer(monkeypatch, ENTITY)

    response = client.get(url(VALID))

    assert response.status_code == status.HTTP_200_OK
    assert calls == [brreg.BASE_URL + VALID]
    assert response.data == {
        "orgnr": VALID,
        "name": "TESTFORENINGEN",
        "organization_form": "Forening/lag/innretning",
        "homepage": "https://www.testforeningen.no",
        "purpose": "Foreningen skal lage lokal-TV.",
        "street_address": "Testgata 1\n0150 OSLO",
        "postal_address": "Testgata 1\n0150 OSLO",
        "bankrupt": False,
        "being_dissolved": False,
    }


def test_separate_postal_address_is_used(client, monkeypatch):
    entity = dict(
        ENTITY,
        postadresse={
            "landkode": "NO",
            "postnummer": "0101",
            "poststed": "OSLO",
            "adresse": ["Postboks 1"],
        },
    )
    answer(monkeypatch, entity)

    response = client.get(url(VALID))

    assert response.data["postal_address"] == "Postboks 1\n0101 OSLO"
    assert response.data["street_address"] == "Testgata 1\n0150 OSLO"


def test_spaces_in_the_number_are_ignored(client, monkeypatch):
    calls = answer(monkeypatch, ENTITY)

    response = client.get(url("971 526 920"))

    assert response.status_code == status.HTTP_200_OK
    assert calls == [brreg.BASE_URL + VALID]


def test_invalid_number_is_rejected_without_asking(client, monkeypatch):
    calls = answer(monkeypatch, ENTITY)

    response = client.get(url(INVALID_CHECK))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert calls == []


@pytest.mark.parametrize(
    "code, expected",
    [
        (404, status.HTTP_404_NOT_FOUND),
        (410, status.HTTP_410_GONE),
        (500, status.HTTP_503_SERVICE_UNAVAILABLE),
    ],
)
def test_register_answers(client, monkeypatch, code, expected):
    answer(monkeypatch, code=code)

    assert client.get(url(VALID)).status_code == expected


def test_unreachable_register(client, monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(brreg.urllib.request, "urlopen", urlopen)

    assert client.get(url(VALID)).status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_anonymous_users_cannot_look_up(db, monkeypatch):
    calls = answer(monkeypatch, ENTITY)

    response = APIClient().get(url(VALID))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert calls == []


def test_orgnr_is_saved_without_spaces(client):
    response = client.post(
        reverse("api-organization-list"),
        {
            "name": "New organization",
            "description": "Created through the API",
            "orgnr": "971 526 920",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["orgnr"] == VALID


def test_invalid_orgnr_is_not_saved(client):
    response = client.post(
        reverse("api-organization-list"),
        {"name": "New organization", "description": "x", "orgnr": INVALID_CHECK},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_activity_stands_in_for_a_missing_purpose(client, monkeypatch):
    entity = {k: v for k, v in ENTITY.items() if k != "vedtektsfestetFormaal"}
    answer(monkeypatch, dict(entity, aktivitet=["Drive nærradio"]))

    assert client.get(url(VALID)).data["purpose"] == "Drive nærradio"
