from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem, User, Video


class LiveTransmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Broadcaster")
        creator = User.objects.create_user(email="producer@example.com")
        cls.video = Video.objects.create(
            name="Live debate",
            organization=cls.organization,
            creator=creator,
        )
        cls.source = LiveSource.objects.create(name="Studio", organization=cls.organization)
        cls.entry = Scheduleitem.objects.create(
            video=cls.video,
            starttime=timezone.now(),
            duration=timedelta(hours=1),
            schedulereason=Scheduleitem.REASON_ADMIN,
        )

    def test_a_live_video_without_a_file_is_not_published(self):
        self.assertFalse(self.video.videofile_set.exists())
        self.assertFalse(self.video.proper_import)
        transmission = LiveTransmission.objects.create(video=self.video, source=self.source)

        self.video.refresh_from_db()
        self.assertEqual(self.video.live_transmission, transmission)
        self.assertFalse(Video.objects.public().filter(pk=self.video.pk).exists())

    def test_a_recorded_video_needs_no_live_transmission(self):
        self.assertFalse(hasattr(self.video, "live_transmission"))

    def test_only_one_transmission_per_video(self):
        LiveTransmission.objects.create(video=self.video, source=self.source)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LiveTransmission.objects.create(video=self.video, source=self.source)

    def test_source_protected_and_transmission_deleted_with_video(self):
        transmission = LiveTransmission.objects.create(video=self.video, source=self.source)
        with self.assertRaises(ProtectedError):
            self.source.delete()
        self.entry.delete()
        self.video.delete()
        self.assertFalse(LiveTransmission.objects.filter(pk=transmission.pk).exists())
        self.assertTrue(LiveSource.objects.filter(pk=self.source.pk).exists())
