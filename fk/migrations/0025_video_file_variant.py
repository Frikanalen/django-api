from django.db import migrations, models

# The format table held three things: a name, and two columns of static
# configuration (vod_publish, mime_type) that only ever changed with a
# deploy. The names have been duplicated in code since 0017 constrained
# fsname to a list, so the row was down to minting a primary key -- at
# the cost of a join on every read of a file's kind. This moves the name
# onto VideoFile as a plain string and takes the metadata into the
# VideoFileVariant enum beside it.
#
# The column is `variant` rather than `format` because DRF reserves
# ?format= for content negotiation, and because half these names never
# described a format: srt is a subtitle track, cloudflare_id is not a
# file at all.
#
# Two things could be true of production and are not true of the code, so
# this checks both rather than assuming:
#
#   - a row whose fsname is not one of the enum's values. The choices
#     were never enforced by the database, so a hand-written row could
#     carry anything, and no automatic answer for it would be honest.
#   - vod_publish or mime_type set to something the enum does not say.
#     Those are now literals in video_file.py; if the database disagrees,
#     the deploy would silently change what the API publishes.
#
# Either way it stops and names what it found, the way 0021 does. The
# fix is to correct the data or the enum, then re-run.
#
# This one does not reverse. Putting the foreign key back means adding a
# NOT NULL column to a populated table, which has no answer Django can
# generate, so a rollback is a restore from a backup taken before the
# deploy rather than a `migrate fk 0024`.

VARIANT_NAMES = [
    "large_thumb",
    "broadcast",
    "vc1",
    "med_thumb",
    "small_thumb",
    "original",
    "theora",
    "srt",
    "cloudflare_id",
    "dash",
]

# Mirrors VideoFileVariant.vod_published() and MIME_TYPES. Spelled out
# again because a migration must keep describing the world as it was when
# it was written, even after the enum moves on.
VOD_PUBLISHED = {"theora"}
MIME_TYPES = {"theora": "video/ogg", "dash": "application/dash+xml"}


def check_formats_match_the_enum(apps, schema_editor):
    FileFormat = apps.get_model("fk", "FileFormat")

    unknown = sorted(
        FileFormat.objects.exclude(fsname__in=VARIANT_NAMES).values_list("fsname", flat=True)
    )
    if unknown:
        raise RuntimeError(
            f"Format(s) {unknown} are not in VideoFileVariant. Add them to the enum "
            f"(and to this migration's VARIANT_NAMES) or remove the rows, then re-run."
        )

    disagreements = []
    for file_format in FileFormat.objects.all():
        expected_vod = file_format.fsname in VOD_PUBLISHED
        expected_mime = MIME_TYPES.get(file_format.fsname)
        if file_format.vod_publish != expected_vod:
            disagreements.append(
                f"{file_format.fsname}: vod_publish is {file_format.vod_publish}, "
                f"the enum says {expected_vod}"
            )
        # An unset mime type is stored as NULL by the fixture and as ''
        # by the admin's empty field; neither says anything.
        stored_mime = file_format.mime_type or None
        if stored_mime != expected_mime:
            disagreements.append(
                f"{file_format.fsname}: mime_type is {stored_mime!r}, "
                f"the enum says {expected_mime!r}"
            )
    if disagreements:
        raise RuntimeError(
            "The format table disagrees with VideoFileVariant, and this migration "
            "is about to drop the table:\n  " + "\n  ".join(disagreements)
        )


def copy_fsname_onto_video_files(apps, schema_editor):
    VideoFile = apps.get_model("fk", "VideoFile")
    for name in VARIANT_NAMES:
        VideoFile.objects.filter(format__fsname=name).update(variant=name)


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0024_file_format_dash"),
    ]

    operations = [
        migrations.RunPython(check_formats_match_the_enum, reverse_code=migrations.RunPython.noop),
        # The constraint names the column about to be replaced, so it has
        # to come off before the foreign key does.
        migrations.RemoveConstraint(
            model_name="videofile",
            name="unique_format_per_video",
        ),
        # Nullable while both columns exist; made NOT NULL once every row
        # has been copied across.
        migrations.AddField(
            model_name="videofile",
            name="variant",
            field=models.CharField(max_length=20, null=True),
        ),
        # No reverse: by the time this ran backwards the foreign key
        # column would have to exist again, and it cannot. See above.
        migrations.RunPython(copy_fsname_onto_video_files, reverse_code=migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="videofile",
            name="format",
        ),
        migrations.AlterField(
            model_name="videofile",
            name="variant",
            field=models.CharField(
                choices=[
                    ("large_thumb", "Large thumbnail"),
                    ("broadcast", "Broadcast master"),
                    ("vc1", "VC-1"),
                    ("med_thumb", "Medium thumbnail"),
                    ("small_thumb", "Small thumbnail"),
                    ("original", "Original upload"),
                    ("theora", "Ogg Theora"),
                    ("srt", "SubRip subtitles"),
                    ("cloudflare_id", "Cloudflare Stream identifier"),
                    ("dash", "MPEG-DASH manifest"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="videofile",
            constraint=models.UniqueConstraint(
                fields=("video", "variant"), name="unique_variant_per_video"
            ),
        ),
        migrations.DeleteModel(
            name="FileFormat",
        ),
    ]
