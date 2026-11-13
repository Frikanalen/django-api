"""Looking up an organization in Enhetsregisteret, Brønnøysundregistrene's
register of legal entities.

The register is public and needs no key. Django asks it on the member's
behalf rather than the browser asking directly: the answer arrives already
shaped as our own organization fields, and the organization number is
checked the same way here as when it is saved.
"""

import json
import re
import urllib.error
import urllib.request

from django.core.cache import cache

BASE_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/"
TIMEOUT_SECONDS = 5
CACHE_SECONDS = 24 * 60 * 60

# The weights Brønnøysundregistrene uses for the ninth digit, a modulus 11
# check digit over the first eight.
WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


class BrregUnavailable(Exception):
    """The register did not answer, or answered with something unexpected."""


def normalize_orgnr(value: str) -> str:
    """The number without the spaces people write it with ("971 526 920")."""
    return re.sub(r"\s+", "", value or "")


def is_valid_orgnr(value: str) -> bool:
    """Nine digits whose last digit is the modulus 11 check digit of the rest."""
    if not re.fullmatch(r"\d{9}", value):
        return False
    remainder = sum(int(d) * w for d, w in zip(value, WEIGHTS, strict=False)) % 11
    check = (11 - remainder) % 11
    # A remainder of 1 would need a check digit of 10; such numbers are never issued.
    return check != 10 and check == int(value[8])


def _format_address(address: dict | None) -> str:
    if not address:
        return ""
    lines = [line for line in address.get("adresse") or [] if line]
    place = " ".join(p for p in (address.get("postnummer"), address.get("poststed")) if p)
    if place:
        lines.append(place)
    if address.get("landkode") not in (None, "", "NO") and address.get("land"):
        lines.append(address["land"])
    return "\n".join(lines)


def _homepage(value: str | None) -> str:
    if not value:
        return ""
    # The register stores what the organization typed, mostly "www.example.no".
    return value if re.match(r"https?://", value) else f"https://{value}"


def _entity(data: dict) -> dict:
    street = _format_address(data.get("forretningsadresse"))
    # Most associations register one address only; it serves for both.
    postal = _format_address(data.get("postadresse")) or street
    form = data.get("organisasjonsform") or {}
    return {
        "orgnr": data["organisasjonsnummer"],
        "name": data.get("navn") or "",
        "organization_form": form.get("beskrivelse") or "",
        "homepage": _homepage(data.get("hjemmeside")),
        "street_address": street,
        "postal_address": postal,
        "bankrupt": bool(data.get("konkurs")),
        "being_dissolved": bool(
            data.get("underAvvikling") or data.get("underTvangsavviklingEllerTvangsopplosning")
        ),
        "deleted": False,
    }


def _fetch(orgnr: str) -> dict | None:
    request = urllib.request.Request(
        BASE_URL + orgnr,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return _entity(json.load(response))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 410:
            # Deleted from the register. Say so rather than "not found", so a
            # member who typed the number right knows why it won't fill in.
            return {"orgnr": orgnr, "deleted": True}
        raise BrregUnavailable(f"Enhetsregisteret answered {error.code}") from error
    except (OSError, ValueError, KeyError) as error:
        raise BrregUnavailable(str(error)) from error


def lookup(orgnr: str) -> dict | None:
    """The entity registered under a valid number, or None if there is none.

    A deleted entity comes back with deleted=True and nothing else.
    """
    key = f"brreg:enhet:{orgnr}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    result = _fetch(orgnr)
    # Remember a miss too (as {}), so retyping a wrong number doesn't keep asking.
    cache.set(key, result or {}, CACHE_SECONDS)
    return result
