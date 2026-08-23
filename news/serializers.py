from rest_framework import serializers

from .models import Bulletin


class BulletinSerializer(serializers.ModelSerializer):
    # `created_time` is what every other timestamp in this API is called;
    # the column keeps its own name. Plain ModelSerializer rather than the
    # hyperlinked one it used to be: no `url` field was ever exposed, so
    # the hyperlinking bought nothing and only differed from its siblings.
    created_time = serializers.DateTimeField(source="created", read_only=True)

    class Meta:
        fields = ("id", "heading", "text", "created_time", "is_published")
        model = Bulletin
