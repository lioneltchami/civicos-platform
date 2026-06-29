"""
Reusable DRF permission classes for the Govstack API.

All permission classes follow the principle of least privilege and are
deliberately composable so views can mix them as needed.
"""

from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """Allows access only to staff users (is_staff=True)."""

    message = "Staff access required."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class IsCitizenOwner(BasePermission):
    """
    Object-level permission: the object's citizen_id must match the
    requesting user's PK.

    Requires the model to expose a ``citizen_id`` attribute (the FK id
    column that Django adds automatically for ForeignKey fields named
    ``citizen``).
    """

    message = "You do not have permission to access this resource."

    def has_object_permission(self, request, view, obj) -> bool:
        return hasattr(obj, "citizen_id") and obj.citizen_id == request.user.pk


class IsOwnerOrStaff(BasePermission):
    """
    View-level: user must be authenticated.
    Object-level: user owns the object OR is staff.

    Use this for endpoints where staff need full access but citizens
    are restricted to their own records.
    """

    message = "You do not have permission to access this resource."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        if request.user.is_staff:
            return True
        return hasattr(obj, "citizen_id") and obj.citizen_id == request.user.pk
