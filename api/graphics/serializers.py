from rest_framework import serializers

from scheduling.models import Graphic


class GraphicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Graphic
        fields = ("id", "name", "url")
        read_only_fields = fields
