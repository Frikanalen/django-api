from django.db import models


class KeeperBooking(models.Model):
    """A grant the keeper made, so it knows which grants are its
    own: it changes only these, apart from making room for a sitting."""

    class Kind(models.TextChoices):
        SITTING = "sitting", "Stortinget sitting"
        DEMOCRACY_NOW = "democracy_now", "Democracy Now!"
        EU_BRIEFING = "eu_briefing", "EU Commission midday briefing"

    grant = models.OneToOneField(
        "scheduling.AirtimeGrant", on_delete=models.CASCADE, related_name="keeper_booking"
    )
    kind = models.CharField(max_length=16, choices=Kind)
    # Stortinget's meeting id (talerliste mote_id); unset otherwise.
    mote_id = models.IntegerField(null=True, blank=True)
    # The broadcast day (Europe/Oslo) the booking belongs to.
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["day", "kind"])]

    def __str__(self):
        what = self.get_kind_display()
        if self.mote_id:
            what += f" (møte {self.mote_id})"
        return f"{what} {self.day}: {self.grant}"
