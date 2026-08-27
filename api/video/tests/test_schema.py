from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import VideoFileVariant


def camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


def test_openapi_schema_documents_every_files_key() -> None:
    response = APIClient().get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")

    assert response.status_code == 200
    schema = response.json()
    properties = schema["components"]["schemas"]["VideoFiles"]["properties"]
    expected = {camel_case(variant.value) for variant in VideoFileVariant}

    assert properties.keys() == expected
    assert all(
        property_schema["$ref"] == "#/components/schemas/VideoFileLink"
        for property_schema in properties.values()
    )
    link_properties = schema["components"]["schemas"]["VideoFileLink"]["properties"]
    assert link_properties == {
        "url": {"type": "string"},
        "mimeType": {"type": "string", "nullable": True},
    }


def test_openapi_schema_is_warning_free_and_keeps_weekly_slot_shapes_distinct(capsys) -> None:
    response = APIClient().get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")

    assert response.status_code == 200
    assert "Warning [" not in capsys.readouterr().err

    schema = response.json()
    video_parameters = schema["paths"]["/api/videos"]["get"]["parameters"]
    category_parameter = next(
        parameter
        for parameter in video_parameters
        if parameter["name"] == "categories__name__icontains"
    )
    assert category_parameter["schema"] == {
        "type": "array",
        "items": {"type": "string"},
    }

    schemas = schema["components"]["schemas"]
    assert schemas["WeeklySlotRequest"]["properties"].keys() == {"source"}
    assert schemas["WeeklySlotRequestEnvelope"]["properties"].keys() == {
        "creation",
        "ownership",
    }
    update_request = schema["paths"]["/api/weekly-slots/{id}"]["put"]["requestBody"]
    assert update_request["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WeeklySlotRequest"
    }
