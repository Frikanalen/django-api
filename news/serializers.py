from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Bulletin


class BulletinSerializer(serializers.ModelSerializer):
    # `created_time` is what every other timestamp in this API is called;
    # the column keeps its own name. Plain ModelSerializer rather than the
    # hyperlinked one it used to be: no `url` field was ever exposed, so
    # the hyperlinking bought nothing and only differed from its siblings.
    created_time = serializers.DateTimeField(source="created", read_only=True)
    updated_time = serializers.DateTimeField(source="updated", read_only=True)
    pages = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        read_only=True,
        help_text="The text as the screen pages it: a blank line starts a page, "
        "and a long block is paged by itself. The heading is on every page.",
    )
    page_seconds = serializers.ListField(
        child=serializers.FloatField(),
        read_only=True,
        help_text="How long each page takes to read.",
    )
    reading_seconds = serializers.IntegerField(
        read_only=True, help_text="The time it takes to read every page once."
    )
    is_current = serializers.SerializerMethodField(
        help_text="Published, and inside the dates it runs between."
    )

    class Meta:
        fields = (
            "id",
            "heading",
            "text",
            "template",
            "pages",
            "page_seconds",
            "reading_seconds",
            "created_time",
            "updated_time",
            "is_published",
            "is_current",
            "starts_at",
            "ends_at",
            "priority",
            "on_air",
            "on_infographics",
        )
        model = Bulletin

    def get_is_current(self, bulletin: Bulletin) -> bool:
        return bulletin.is_current()

    def validate(self, attrs):
        # The model's own checks (page count, dates), on the row as it
        # would be saved: a PATCH only carries the fields it changes.
        candidate = Bulletin()
        if self.instance is not None:
            for field in Bulletin._meta.concrete_fields:
                setattr(candidate, field.attname, getattr(self.instance, field.attname))
        for name, value in attrs.items():
            setattr(candidate, name, value)
        try:
            candidate.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs
