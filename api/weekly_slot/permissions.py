from rest_framework import permissions

from api.auth.permissions import can_administer_organization


class CanModifyWeeklySlot(permissions.BasePermission):
    """Members may select a source for their slot; timing is serializer-read-only."""

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or (
            obj.organization is not None
            and can_administer_organization(request.user, obj.organization)
        )
