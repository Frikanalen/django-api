from django.db import models

from fk.models import Video

# This class is actually not managed by Django. It is accessed by the
# media-processor, which manipulates the database directly. It is only
# defined here so that database migrations are handled centrally.


class Asset(models.Model):
    video = models.ForeignKey(Video, on_delete=models.CASCADE)
    asset_type = models.CharField(max_length=160)
    location = models.CharField(max_length=160)


class IngestJob(models.Model):
    video = models.ForeignKey(Video, on_delete=models.CASCADE)
    job_type = models.CharField(max_length=160)
    percentage_done = models.IntegerField(blank=True, default=0)
    status_text = models.TextField(max_length=1000, default="")
    state = models.CharField(max_length=160, default="pending")
