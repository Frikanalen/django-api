# Generated manually

from django.db import migrations


def populate_created_time(apps, schema_editor):
    Video = apps.get_model('fk', 'Video')
    # Update all videos where created_time is null to use updated_time
    videos_to_update = Video.objects.filter(created_time__isnull=True)
    for video in videos_to_update:
        video.created_time = video.updated_time
        video.save(update_fields=['created_time'])


class Migration(migrations.Migration):

    dependencies = [
        ('fk', '0016_alter_asrun_in_ms_alter_asrun_out_ms_and_more'),
    ]

    operations = [
        migrations.RunPython(populate_created_time, migrations.RunPython.noop),
    ]
