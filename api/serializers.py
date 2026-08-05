# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.

import logging

from rest_framework import serializers

from fk.models import Category, Video

logger = logging.getLogger(__name__)


class CategorySerializer(serializers.ModelSerializer):
    videocount = serializers.SerializerMethodField("count_videos")

    @staticmethod
    def count_videos(category):
        return Video.objects.public().filter(categories=category).count()

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "desc",
            "videocount",
        )
