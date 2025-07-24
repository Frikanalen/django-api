import logging
from fk.models import FileFormat

logger = logging.getLogger(__name__)


def inject_video_format_enum(result, generator, request, public):
    logger.debug("Running postprocessing hook: inject_video_format_enum")

    try:
        formats = list(FileFormat.objects.values_list("fsname", flat=True))
        if not formats:
            raise ValueError("Format list empty")
    except Exception as e:
        logger.warning("Could not fetch formats for enum injection: %s", e)
        formats = [
            "no_formats_in_db",
            "have_you_loaded_fixtures",
            "check_readme",
        ]

    updated = 0
    components = result.get("components", {}).get("schemas", {})

    for path_item in result.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue

            schema_ref = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref")
            )
            if not schema_ref or not schema_ref.startswith("#/components/schemas/"):
                continue

            schema_name = schema_ref.rsplit("/", 1)[-1]
            component = components.get(schema_name)
            if not component:
                continue

            props = component.get("properties", {})
            field = props.get("format")
            if not isinstance(field, dict):
                continue

            field["enum"] = formats
            field["example"] = formats[0]
            updated += 1

    if updated:
        logger.info("Injected enum into %d `format` fields", updated)

    return result
