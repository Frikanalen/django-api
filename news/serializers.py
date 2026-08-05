from rest_framework import serializers

from .models import Bulletin


class BulletinSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        fields = ("id", "heading", "text", "created")
        model = Bulletin
