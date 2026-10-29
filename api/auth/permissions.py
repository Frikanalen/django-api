from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied, ValidationError


def can_administer_organization(user, organization) -> bool:
    """
    The create/update-time analogue of the object-level checks below:
    staff, the organization's editor, or one of its members.
    """
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return (
        organization.editor_id == user.pk
        or user.organization_set.filter(pk=organization.pk).exists()
    )


def can_schedule_for_organization(user, organization) -> bool:
    """Whether a user may put an organization's videos on air.

    Uploading is deliberately available before either approval: scheduling
    is the narrower, consequential capability that requires both a confirmed
    identity and an approved Frikanalen member organization. Staff retain the
    site-wide override used by the rest of the scheduling API.
    """
    if not can_administer_organization(user, organization):
        return False
    return user.is_staff or (user.identity_confirmed and organization.fkmember)


class RequireTargetOrganizationMembership:
    """
    View mixin closing the gap the IsInOrganization* classes leave open:
    DRF never runs object-level checks on create, and on update the
    object check runs against the object as it *was*, not against the
    video/organization the payload points it at. Applied to views whose
    serializers take an `organization` or `video` target.
    """

    def perform_create(self, serializer):
        self._require_target_organization_membership(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._require_target_organization_membership(serializer)
        super().perform_update(serializer)

    def _require_target_organization_membership(self, serializer):
        data = serializer.validated_data
        if "organization" in data:
            organization = data["organization"]
        elif "video" in data:
            organization = data["video"].organization
        else:
            return
        if not can_administer_organization(self.request.user, organization):
            raise PermissionDenied("You must belong to the organization that owns this content.")


class RequireSchedulingEligibility:
    """Enforce scheduling eligibility against a create or update target.

    Object permissions check the existing schedule item on update. This mixin
    additionally checks a replacement video, and closes the create-time gap
    where DRF has no object on which to run an object permission.
    """

    message = (
        "Scheduling requires a confirmed identity and a Frikanalen member "
        "organization that you administer."
    )

    def perform_create(self, serializer):
        self._require_scheduling_eligibility(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._require_scheduling_eligibility(serializer)
        super().perform_update(serializer)

    def _require_scheduling_eligibility(self, serializer):
        user = self.request.user
        if user.is_staff:
            return

        data = serializer.validated_data
        if "video" in data:
            video = data["video"]
        elif serializer.instance is None:
            # A non-staff schedule item must be attributable to an eligible
            # organization. Without a video there is no such target to check.
            video = None
        else:
            # The object permission already checked the unchanged video.
            return

        if video is None or not can_schedule_for_organization(user, video.organization):
            raise PermissionDenied(self.message)
        if not video.proper_import:
            raise ValidationError(
                {"video": "The video must finish processing before it can be scheduled."}
            )


class CanScheduleForOrganizationOrReadOnly(permissions.IsAuthenticatedOrReadOnly):
    """Public reads; eligible organization users and staff may mutate."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.is_staff:
            return True
        video = getattr(obj, "video", None)
        return video is not None and can_schedule_for_organization(request.user, video.organization)


class IsOrganizationEditorOrDisallow(permissions.IsAuthenticatedOrReadOnly):
    """
    Object-level read permission to users in the object's organization

    Supports an organization itself, an object with an `organization`
    foreign key, or an object with a `video` belonging to an organization.
    """

    def has_object_permission(self, request, view, obj):
        # Anonymous are always disallowed
        if not request.user.is_authenticated:
            return False
        # Staff are allowed to change everything
        if request.user.is_staff:
            return True
        # The organization endpoint passes an Organization directly. Other
        # endpoints pass an object related to one either directly or via video.
        if hasattr(obj, "editor_id"):
            organization_id = obj.pk
        elif hasattr(obj, "organization_id"):
            organization_id = obj.organization_id
        else:
            organization_id = obj.video.organization_id
        # User must be editor of organization to do changes
        return request.user.editor.filter(id=organization_id).exists()


class IsOrganizationEditorOrReadOnly(IsOrganizationEditorOrDisallow):
    """
    Object-level edit permission to users in the object's organization
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        return super().has_object_permission(request, view, obj)


class IsInOrganizationOrDisallow(permissions.IsAuthenticatedOrReadOnly):
    """
    Object-level read permission to users in the object's organization

    Assumes the model instance has an `organization` foreign key or a
    `video` foreign key with such a connection.
    """

    def has_object_permission(self, request, view, obj):
        # Anonymous are always disallowed
        if not request.user.is_authenticated:
            return False
        # Staff are allowed to change everything
        if request.user.is_staff:
            return True
        # We expect either the object to have an organization directly
        # or have a video field with an organization.
        try:
            organization = obj.organization
        except AttributeError:
            organization = obj.video.organization
        # User must be part of organization to do changes
        return (
            organization.editor == request.user
            or organization in request.user.organization_set.all()
        )


class IsInOrganizationOrReadOnly(IsInOrganizationOrDisallow):
    """
    Object-level edit permission to users in the object's organization
    """

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        return super().has_object_permission(request, view, obj)


class IsStaffOrReadOnly(permissions.BasePermission):
    """
    The request user is staff, or is a read-only request.
    """

    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS or request.user and request.user.is_staff


class IngestJobPermission(permissions.BasePermission):
    """Reads for the video's organization, writes for the ingest service.

    The write side is the narrow one, and it is narrower than it looks:
    reporting ingest progress is a machine's job, but `is_staff` is the
    only identity this User model can express for a machine. `has_perm`
    answers `is_superuser` and nothing else -- there are no groups and no
    per-model permissions behind it -- so a `fk.change_ingestjob` check
    would mean exactly this while implying more. Keeping the check here
    rather than reusing IsStaffOrReadOnly leaves one place to narrow, once
    there is something to narrow it to.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.method in permissions.SAFE_METHODS or request.user.is_staff

    def has_object_permission(self, request, view, obj):
        if request.method not in permissions.SAFE_METHODS:
            return request.user.is_staff
        # An ingest job has no organization of its own; the delegate walks
        # to the one behind its video.
        return IsInOrganizationOrDisallow().has_object_permission(request, view, obj)
