from django.db import migrations, models

# A video's organization is what carries the editorial responsibility for
# it: the visibility rules, the jukebox filter and the permission checks
# are all phrased in terms of it, and a video with none could be neither
# aired nor answered for. The column has been nullable since the start
# without that ever meaning anything, and production has no NULLs.
#
# Unlike 0020 there is no honest backfill: no column records which
# organization an orphan video would belong to, and picking one would be
# inventing an accountable party. So this refuses to guess and stops
# instead, with a message saying what to look at. Any copy of the
# database old enough to have such rows -- a dev machine, an old dump --
# needs a human to assign them.


def refuse_orphan_videos(apps, schema_editor):
    Video = apps.get_model("fk", "Video")
    orphans = Video.objects.filter(organization__isnull=True)
    count = orphans.count()
    if count:
        ids = list(orphans.values_list("id", flat=True)[:20])
        raise RuntimeError(
            f"{count} video(s) have no organization, which this migration makes "
            f"required. Assign each one to the organization answerable for it "
            f"and re-run. First ids: {ids}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0020_video_timestamps_not_nullable"),
    ]

    operations = [
        migrations.RunPython(
            refuse_orphan_videos,
            # Reversing makes NULL legal again rather than required;
            # there is nothing to put back.
            reverse_code=migrations.RunPython.noop,
            elidable=True,
        ),
        migrations.AlterField(
            model_name="video",
            name="organization",
            field=models.ForeignKey(
                help_text="Organization for video",
                on_delete=models.deletion.PROTECT,
                to="fk.organization",
            ),
        ),
    ]
