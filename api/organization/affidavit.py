"""
The signed editor affidavit, uploaded as a scan.

The editor of an organization uploads it; staff check it in the admin
(AffidavitSubmissionAdmin) and approve it, which makes the organization a
member and confirms the editor's identity, or reject it with a reason the
organization sees.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from api.auth.password_reset import run_in_background
from api.auth.permissions import can_administer_organization
from fk.models import AffidavitStatus, AffidavitSubmission, Organization, User

logger = logging.getLogger(__name__)

AFFIDAVIT_MAX_BYTES = 10 * 1024 * 1024

# What a scan may be, told by its first bytes rather than by what the
# browser claims.
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_content_type(head: bytes) -> str | None:
    for signature, content_type in _SIGNATURES:
        if head.startswith(signature):
            return content_type
    return None


class AffidavitSubmissionSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(choices=AffidavitStatus.choices, read_only=True)

    class Meta:
        model = AffidavitSubmission
        fields = ("id", "submitted_at", "file_name", "status", "reviewed_at", "review_note")
        read_only_fields = fields


class AffidavitUploadSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="The signed affidavit: PDF, JPEG or PNG, up to 10 MB.")


def _send(subject_template: str, body_template: str, context: dict, recipients: list[str]):
    if not recipients:
        return
    subject = "".join(render_to_string(subject_template, context).splitlines())
    body = render_to_string(body_template, context)
    try:
        send_mail(subject, body, None, recipients)
    except Exception:
        logger.exception("Failed to send affidavit mail (%s)", subject_template)


def notify_staff_of_submission(submission: AffidavitSubmission, admin_url: str) -> None:
    """Tell staff there is a scan in the queue, so it does not sit there unseen."""
    recipients = list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email__endswith="@invalid")
        .values_list("email", flat=True)
    )
    context = {"organization": submission.organization, "admin_url": admin_url}
    run_in_background(
        _send,
        "affidavit/submitted_staff_subject.txt",
        "affidavit/submitted_staff_email.txt",
        context,
        recipients,
    )


def notify_submitter_of_decision(submission: AffidavitSubmission) -> None:
    user = submission.submitted_by
    if user is None or not user.is_active:
        return
    context = {
        "user": user,
        "organization": submission.organization,
        "approved": submission.status == AffidavitStatus.APPROVED,
        "note": submission.review_note,
        "site_url": settings.SITE_URL,
    }
    run_in_background(
        _send,
        "affidavit/decided_subject.txt",
        "affidavit/decided_email.txt",
        context,
        [user.email],
    )


class OrganizationAffidavit(APIView):
    """
    The organization's uploaded affidavit scans, newest first, and how far
    each has got. The editor uploads a new one here; uploading replaces any
    scan still waiting for review.
    """

    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    def _organization(self, request, pk: int) -> Organization:
        organization = get_object_or_404(Organization.objects.all(), pk=pk)
        if not can_administer_organization(request.user, organization):
            raise PermissionDenied("Du hører ikke til denne organisasjonen.")
        return organization

    @extend_schema(responses=AffidavitSubmissionSerializer(many=True))
    def get(self, request, pk: int):
        organization = self._organization(request, pk)
        submissions = organization.affidavit_submissions.defer("content")
        return Response(AffidavitSubmissionSerializer(submissions, many=True).data)

    @extend_schema(
        request={"multipart/form-data": AffidavitUploadSerializer},
        responses={201: AffidavitSubmissionSerializer},
    )
    def post(self, request, pk: int):
        organization = self._organization(request, pk)
        # The affidavit is the editor's own declaration: nobody else signs it.
        if organization.editor_id != request.user.pk and not request.user.is_staff:
            raise PermissionDenied("Bare redaktøren kan sende inn redaktørerklæringen.")
        if organization.fkmember:
            raise ValidationError({"file": "Organisasjonen er allerede godkjent som medlem."})

        upload = AffidavitUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        file = upload.validated_data["file"]
        if file.size > AFFIDAVIT_MAX_BYTES:
            raise ValidationError({"file": "Filen er for stor. Den kan være høyst 10 MB."})
        content = file.read()
        content_type = sniff_content_type(content[:16])
        if content_type is None:
            raise ValidationError({"file": "Last opp erklæringen som PDF, JPEG eller PNG."})

        with transaction.atomic():
            # One scan in the queue per organization: a new one replaces
            # what staff have not looked at yet.
            organization.affidavit_submissions.filter(status=AffidavitStatus.PENDING).delete()
            submission = AffidavitSubmission.objects.create(
                organization=organization,
                submitted_by=request.user,
                file_name=(file.name or "erklaering")[:255],
                content_type=content_type,
                content=content,
            )
        notify_staff_of_submission(
            submission,
            request.build_absolute_uri(
                reverse("admin:fk_affidavitsubmission_change", args=[submission.pk])
            ),
        )
        return Response(
            AffidavitSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED
        )


class AffidavitFile(APIView):
    """The scan itself, for staff only."""

    permission_classes = (permissions.IsAdminUser,)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    def get(self, request, pk: int, submission_id: int):
        submission = get_object_or_404(
            AffidavitSubmission.objects.all(), pk=submission_id, organization_id=pk
        )
        response = HttpResponse(bytes(submission.content), content_type=submission.content_type)
        # Shown in the browser, not run in the site's origin as a document.
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"
        response["Cache-Control"] = "private, no-store"
        return response
