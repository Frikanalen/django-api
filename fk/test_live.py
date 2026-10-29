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

    def test_live_booking_without_original_does_not_publish_video(self):
        self.assertFalse(self.video.videofile_set.exists())
        self.assertFalse(self.video.proper_import)
        transmission = LiveTransmission.objects.create(schedule_item=self.entry, source=self.source)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.live_transmission, transmission)
        self.assertEqual(self.entry.duration, timedelta(hours=1))
        self.assertFalse(Video.objects.public().filter(pk=self.video.pk).exists())

    def test_recorded_entry_requires_no_attachment(self):
        self.assertFalse(hasattr(self.entry, "live_transmission"))
        self.assertEqual(self.entry.video_id, self.video.pk)

    def test_only_one_transmission_per_entry(self):
        LiveTransmission.objects.create(schedule_item=self.entry, source=self.source)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LiveTransmission.objects.create(schedule_item=self.entry, source=self.source)

    def test_source_protected_and_attachment_deleted_with_entry(self):
        transmission = LiveTransmission.objects.create(schedule_item=self.entry, source=self.source)
        with self.assertRaises(ProtectedError):
            self.source.delete()
        self.entry.delete()
        self.assertFalse(LiveTransmission.objects.filter(pk=transmission.pk).exists())
        self.assertTrue(LiveSource.objects.filter(pk=self.source.pk).exists())
