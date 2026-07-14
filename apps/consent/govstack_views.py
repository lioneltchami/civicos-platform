"""
GovStack Consent BB v1.3.0 — API views.

Implements the three API namespaces defined in the GovStack OpenAPI spec:

  /config/   — org-authenticated CRUD for Policy, DataAgreement, Individual, Webhook
  /service/  — individual-authenticated consent management + verification
  /audit/    — auditor-authenticated read-only access to consent records

Security:
  - All views require IsAuthenticated.
  - Config views additionally require IsAdminUser or IsOrgUser permission.
  - Audit views require IsAuditorUser permission.
  - Service views are scoped to the authenticated individual (IDOR protection).

GovStack OpenAPI spec:
  https://raw.githubusercontent.com/GovStackWorkingGroup/bb-consent/v23Q4/api/consent-openapi.yaml
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.api.authentication import CivicOSTokenAuthentication

from .models import (
    ConsentAuditEntry,
    ConsentCategory,
    ConsentPolicy,
    ConsentRecord,
    ConsentRevision,
    ConsentSignature,
    ConsentWebhook,
)
from .serializers import (
    ConsentAuditEntrySerializer,
    ConsentRecordGovStackSerializer,
    DataAgreementSerializer,
    IndividualSerializer,
    PolicySerializer,
    RevisionSerializer,
    SignatureSerializer,
    WebhookSerializer,
)
from .services import ConsentService

logger = logging.getLogger(__name__)
User = get_user_model()

_AUTH = [CivicOSTokenAuthentication, JWTAuthentication]


def _safe_int(value, default: int, min_val: int = 0, max_val: int = 10_000) -> int:
    """
    F22 fix: safely convert a query-param string to an integer.

    Returns ``default`` if the value is None, empty, or not a valid integer,
    and clamps the result to [min_val, max_val] to prevent runaway queries.
    """
    try:
        return max(min_val, min(max_val, int(value)))
    except (TypeError, ValueError):
        return default


class IsAuditorUser(BasePermission):
    """
    GovStack auditor role.

    An auditor can read consent records and data agreements (audit namespace)
    but has no access to the config or service namespaces.

    Membership check: user must be in the 'consent_auditors' group OR be staff.
    This preserves backwards compatibility — existing admins (is_staff) retain
    audit access; external auditors get a dedicated group.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff:
            return True
        return request.user.groups.filter(name="consent_auditors").exists()


class IsConsumerUser(BasePermission):
    """
    GovStack [consumer] OAuth2 scope — data consumers querying consent records.

    Data consumers verify whether individuals have consented before processing
    their data. They must NOT be able to read each other's consent data.

    Membership check: user must be in the 'data_consumers' group OR be staff.
    Staff inherits access for operational testing.

    This maps to the GovStack security: [{consumer: []}] scope on:
      - serviceVerificationConsentRecordList
      - serviceVerificationConsentRecordRead
      - serviceVerificationDataAgreementList
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff:
            return True
        return request.user.groups.filter(name="data_consumers").exists()


class IsConsentAdminUser(BasePermission):
    """
    GovStack [config] admin role — grants access to the /config/ namespace.

    Broader than Django's built-in IsAdminUser: consent administrators who are
    not Django staff (is_staff=False) can manage consent configuration if they
    belong to the 'consent_admins' group.

    H-07 fix: config views previously used IsAdminUser (is_staff only), which
    excluded group-based consent admins. This class unifies the admin check
    with the pattern used elsewhere in the service layer.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff:
            return True
        return request.user.groups.filter(name="consent_admins").exists()


# ===========================================================================
# Config API — Policy
# GovStack paths: /config/policy/, /config/policy/{id}/, /config/policies/,
#                 /config/policy/{id}/revisions/
# ===========================================================================

class ConfigPolicyListView(APIView):
    """
    GET  /config/policies/       — list all policies
    POST /config/policy/         — create a new policy + initial revision
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request):
        """LIST — GovStack configPolicyList"""
        qs = ConsentPolicy.objects.filter(is_active=True).order_by("-created_at")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        page = qs[offset: offset + limit]
        return Response({"policies": PolicySerializer(page, many=True).data, "total": qs.count()})

    def post(self, request):
        """CREATE — GovStack configPolicyCreate"""
        serializer = PolicySerializer(data=request.data.get("policy", request.data))
        serializer.is_valid(raise_exception=True)

        # Use all validated data — includes description and third_party_data_sharing
        data = dict(serializer.validated_data)
        policy, revision = ConsentService.create_policy(data, actor=request.user)
        return Response({
            "policy": PolicySerializer(policy).data,
            "revision": RevisionSerializer(revision).data,
        })


class ConfigPolicyDetailView(APIView):
    """
    GET    /config/policy/{policyId}/  — read a policy + latest revision
    PUT    /config/policy/{policyId}/  — update policy + new revision
    DELETE /config/policy/{policyId}/  — soft-delete (is_active=False)
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def _get_policy(self, pk):
        try:
            return ConsentPolicy.objects.get(pk=pk)
        except ConsentPolicy.DoesNotExist:
            raise NotFound("Policy not found.")

    def get(self, request, policy_id):
        """READ — GovStack configPolicyRead"""
        policy = self._get_policy(policy_id)
        # GovStack spec: optional ?revisionId= query param selects a specific revision.
        revision_id = request.query_params.get("revisionId")
        if revision_id:
            try:
                revision = ConsentRevision.objects.get(
                    pk=revision_id,
                    schema_name="Policy",
                    object_id=str(policy.pk),
                )
            except ConsentRevision.DoesNotExist:
                raise NotFound("Revision not found for this policy.")
        else:
            revision = (
                ConsentRevision.objects.filter(
                    schema_name="Policy",
                    object_id=str(policy.pk),
                    successor__isnull=True,
                ).first()
            )
        return Response({
            "policy": PolicySerializer(policy).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })

    def put(self, request, policy_id):
        """UPDATE — GovStack configPolicyUpdate"""
        policy = self._get_policy(policy_id)
        serializer = PolicySerializer(policy, data=request.data.get("policy", request.data), partial=True)
        serializer.is_valid(raise_exception=True)
        policy, revision = ConsentService.update_policy(
            policy,
            serializer.validated_data,
            actor=request.user,
        )
        return Response({
            "policy": PolicySerializer(policy).data,
            "revision": RevisionSerializer(revision).data,
        })

    def delete(self, request, policy_id):
        """DELETE (soft) — GovStack configPolicyDelete"""
        policy = self._get_policy(policy_id)
        if ConsentCategory.objects.filter(policy=policy, is_active=True).exists():
            raise ValidationError(
                "Policy cannot be deleted while active DataAgreements reference it."
            )
        policy.is_active = False
        policy.save(update_fields=["is_active"])
        _, revision = ConsentService.update_policy(policy, {}, actor=request.user)
        return Response({"revision": RevisionSerializer(revision).data})


class ConfigPolicyRevisionsView(APIView):
    """GET /config/policy/{policyId}/revisions/ — list all revisions"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request, policy_id):
        """LIST — GovStack configPolicyRevisionsList"""
        try:
            policy = ConsentPolicy.objects.get(pk=policy_id)
        except ConsentPolicy.DoesNotExist:
            raise NotFound("Policy not found.")
        revisions = ConsentRevision.objects.filter(
            schema_name="Policy",
            object_id=str(policy.pk),
        ).order_by("timestamp")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        page = revisions[offset: offset + limit]
        return Response({
            "policy": PolicySerializer(policy).data,
            "revisions": RevisionSerializer(page, many=True).data,
            "total": revisions.count(),
        })


# ===========================================================================
# Config API — DataAgreement
# GovStack paths: /config/data-agreement/, /config/data-agreement/{id}/,
#                 /config/data-agreements/
# ===========================================================================

class ConfigDataAgreementListView(APIView):
    """
    GET  /config/data-agreements/   — list all data agreements
    POST /config/data-agreement/    — create a new data agreement
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request):
        """LIST — GovStack configDataAgreementList"""
        qs = ConsentCategory.objects.order_by("sort_order", "slug").select_related("policy")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        page = qs[offset: offset + limit]
        # GovStack spec: GET /config/data-agreements/ response key is "dataAgreement"
        # (singular), even though the value is an array.  This is an intentional spec
        # choice — see v23Q4 YAML components/schemas response property name.
        return Response({
            "dataAgreement": DataAgreementSerializer(page, many=True).data,
            "total": qs.count(),
        })

    def post(self, request):
        """CREATE — GovStack configDataAgreementCreate"""
        payload = request.data.get("dataAgreement", request.data)
        # Normalize: accept both GovStack camelCase and CivicOS snake_case keys
        payload = _normalize_da_payload(payload)
        serializer = DataAgreementSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = _da_validated_to_model_fields(serializer.validated_data)
        category, revision = ConsentService.create_data_agreement(
            data,
            actor=request.user,
        )
        return Response({
            "dataAgreement": DataAgreementSerializer(category).data,
            "revision": RevisionSerializer(revision).data,
        })


class ConfigDataAgreementDetailView(APIView):
    """
    GET    /config/data-agreement/{id}/  — read a data agreement
    PUT    /config/data-agreement/{id}/  — update + new revision
    DELETE /config/data-agreement/{id}/  — deactivate
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def _get_category(self, pk):
        try:
            return ConsentCategory.objects.get(pk=pk)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")

    def get(self, request, data_agreement_id):
        """READ — GovStack configDataAgreementRead"""
        category = self._get_category(data_agreement_id)
        revision = ConsentService._get_latest_revision(category)
        return Response({
            "dataAgreement": DataAgreementSerializer(category).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })

    def put(self, request, data_agreement_id):
        """UPDATE — GovStack configDataAgreementUpdate"""
        category = self._get_category(data_agreement_id)
        payload = request.data.get("dataAgreement", request.data)
        payload = _normalize_da_payload(payload)
        serializer = DataAgreementSerializer(category, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        data = _da_validated_to_model_fields(serializer.validated_data)
        category, revision = ConsentService.update_data_agreement(
            category,
            data,
            actor=request.user,
        )
        return Response({
            "dataAgreement": DataAgreementSerializer(category).data,
            "revision": RevisionSerializer(revision).data,
        })

    def delete(self, request, data_agreement_id):
        """DELETE (deactivate) — GovStack configDataAgreementDelete"""
        category = self._get_category(data_agreement_id)
        category.is_active = False
        category.save(update_fields=["is_active"])
        _, revision = ConsentService.update_data_agreement(category, {}, actor=request.user)
        return Response({"revision": RevisionSerializer(revision).data})


# ===========================================================================
# Config API — Individual
# GovStack paths: /config/individual/, /config/individual/{id}/, /config/individuals/
# ===========================================================================

class ConfigIndividualListView(APIView):
    """GET /config/individuals/ + POST /config/individual/"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request):
        """LIST — GovStack configIndividualList"""
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        qs = User.objects.filter(is_active=True).order_by("date_joined")[offset: offset + limit]
        return Response({"individuals": IndividualSerializer(qs, many=True).data})

    def post(self, request):
        """CREATE — GovStack configIndividualCreate (creates a Django User)"""
        payload = request.data.get("individual", request.data)
        # In CivicOS, individuals are Django Users — creation goes through the
        # auth_extension registration flow. We expose a minimal proxy here.
        email = payload.get("email") or payload.get("externalId")
        if not email:
            raise ValidationError({"email": "Required."})
        if User.objects.filter(email=email).exists():
            raise ValidationError({"email": "An individual with this email already exists."})
        user = User.objects.create_user(email=email, password=None)
        return Response({"individual": IndividualSerializer(user).data})


class ConfigIndividualDetailView(APIView):
    """GET/PUT/DELETE /config/individual/{id}/"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def _get_user(self, individual_id):
        try:
            return User.objects.get(pk=individual_id)
        except (User.DoesNotExist, ValueError):
            raise NotFound("Individual not found.")

    def get(self, request, individual_id):
        """READ — GovStack configIndividualRead"""
        return Response({"individual": IndividualSerializer(self._get_user(individual_id)).data})

    def put(self, request, individual_id):
        """UPDATE — GovStack configIndividualUpdate"""
        user = self._get_user(individual_id)
        payload = request.data.get("individual", request.data)
        allowed = {"first_name", "last_name"}
        for field in allowed:
            if field in payload:
                setattr(user, field, payload[field])
        user.save()
        return Response({"individual": IndividualSerializer(user).data})

    def delete(self, request, individual_id):
        """DELETE — GovStack configIndividualDelete (deactivates the account)"""
        user = self._get_user(individual_id)
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(status=status.HTTP_200_OK)


# ===========================================================================
# Config API — Webhook
# GovStack paths: /config/webhook/, /config/webhook/{id}/, /config/webhooks/
# ===========================================================================

class ConfigWebhookListView(APIView):
    """GET /config/webhooks/ + POST /config/webhook/"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request):
        """LIST — GovStack configWebhookList"""
        qs = ConsentWebhook.objects.all()
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({"webhooks": WebhookSerializer(qs[offset: offset + limit], many=True).data})

    def post(self, request):
        """CREATE — GovStack configWebhookCreate"""
        payload = request.data.get("webhook", request.data)
        serializer = WebhookSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        # Use spec "disabled" field from serializer validated_data (source="is_disabled").
        # Fall back to legacy "isActive" inversion only when "disabled" was not provided.
        if "is_disabled" in serializer.validated_data:
            is_disabled = serializer.validated_data["is_disabled"]
        else:
            # Legacy isActive support — not in spec, kept for backwards compatibility
            is_disabled = not payload.get("isActive", True)
        webhook = ConsentWebhook.objects.create(
            payload_url=serializer.validated_data["payload_url"],
            content_type=serializer.validated_data.get("content_type", "application/json"),
            is_disabled=is_disabled,
            secret_key=serializer.validated_data["secret_key"],
            subscribed_events=serializer.validated_data.get("subscribed_events", []),
            signature_header=serializer.validated_data.get("signature_header", "X-GovStack-Signature"),
            skipped_headers=serializer.validated_data.get("skipped_headers", []),
        )
        return Response({"webhook": WebhookSerializer(webhook).data})


class ConfigWebhookDetailView(APIView):
    """GET /config/webhook/{id}/ + PUT + DELETE"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def _get_webhook(self, pk):
        try:
            return ConsentWebhook.objects.get(pk=pk)
        except ConsentWebhook.DoesNotExist:
            raise NotFound("Webhook not found.")

    def get(self, request, webhook_id):
        return Response({"webhook": WebhookSerializer(self._get_webhook(webhook_id)).data})

    def put(self, request, webhook_id):
        webhook = self._get_webhook(webhook_id)
        payload = request.data.get("webhook", request.data)
        serializer = WebhookSerializer(webhook, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        # Apply all validated fields (including is_disabled from spec "disabled" field).
        for field, value in serializer.validated_data.items():
            setattr(webhook, field, value)
        # Legacy backwards-compat: honour "isActive" only when spec "disabled" was absent.
        if "disabled" not in payload and "isActive" in payload:
            webhook.is_disabled = not payload["isActive"]
        webhook.save()
        return Response({"webhook": WebhookSerializer(webhook).data})

    def delete(self, request, webhook_id):
        self._get_webhook(webhook_id).delete()
        return Response(status=status.HTTP_200_OK)


class ConfigWebhookPayloadView(APIView):
    """
    GET /config/webhook/{id}/payload/
    Returns the last payload delivered to this webhook (ping/test endpoint).
    GovStack operationId: configWebhookPayload
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsentAdminUser]

    def get(self, request, webhook_id):
        try:
            webhook = ConsentWebhook.objects.get(pk=webhook_id)
        except ConsentWebhook.DoesNotExist:
            raise NotFound("Webhook not found.")
        # Return the webhook configuration and a placeholder payload.
        # In production this would include the last event dispatched.
        return Response({
            "webhook": WebhookSerializer(webhook).data,
            "payload": None,  # No replay storage in this implementation
        })


# ===========================================================================
# Service API — Individual (self-service)
# GovStack paths: /service/individual/, /service/individual/{id}/, /service/individuals/
# ===========================================================================

class ServiceIndividualView(APIView):
    """
    GET  /service/individuals/        — list all (admin) or own record (citizen)
    POST /service/individual/         — create/register self
    GET  /service/individual/{id}/    — read by ID (admin reads any; citizen reads own)
    PUT  /service/individual/{id}/    — update own record
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, individual_id=None):
        """READ — GovStack serviceIndividualRead / serviceIndividualList"""
        if individual_id:
            # Detail view — admin can read any, citizen can only read own
            if request.user.is_staff or str(request.user.pk) == str(individual_id):
                try:
                    user = User.objects.get(pk=individual_id)
                except User.DoesNotExist:
                    raise NotFound("Individual not found.")
            else:
                raise PermissionDenied("You may only read your own record.")
            return Response({"individual": IndividualSerializer(user).data})
        # List view — admin sees all active; citizen sees only their own record
        # Always return the plural "individuals" array (spec contract for list endpoints).
        if request.user.is_staff:
            qs = User.objects.filter(is_active=True).order_by("date_joined")
        else:
            qs = User.objects.filter(pk=request.user.pk)
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({"individuals": IndividualSerializer(qs[offset: offset + limit], many=True).data})

    def post(self, request, individual_id=None):
        """CREATE/register — GovStack serviceIndividualCreate"""
        payload = request.data.get("individual", request.data)
        email = payload.get("email")
        if not email:
            raise ValidationError({"email": "Required."})
        if User.objects.filter(email=email).exists():
            raise ValidationError({"email": "An individual with this email already exists."})
        user = User.objects.create_user(
            email=email,
            password=None,
            first_name=payload.get("first_name", "") or payload.get("name", "").split(" ")[0],
            last_name=payload.get("last_name", ""),
        )
        return Response({"individual": IndividualSerializer(user).data})

    def put(self, request, individual_id=None):
        """UPDATE own individual — limited field set"""
        user = request.user
        if individual_id and str(user.pk) != str(individual_id):
            if not request.user.is_staff:
                raise PermissionDenied("You may only update your own record.")
            try:
                user = User.objects.get(pk=individual_id)
            except User.DoesNotExist:
                raise NotFound("Individual not found.")
        payload = request.data.get("individual", request.data)
        allowed = {"first_name", "last_name"}
        for field in allowed:
            if field in payload:
                setattr(user, field, payload[field])
        user.save()
        return Response({"individual": IndividualSerializer(user).data})


# ===========================================================================
# Service API — DataAgreement (read-only for individuals)
# GovStack path: /service/data-agreement/{id}/
# ===========================================================================

class ServiceDataAgreementDetailView(APIView):
    """GET /service/data-agreement/{id}/ — read a Data Agreement"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, data_agreement_id):
        """READ — GovStack serviceDataAgreementRead"""
        try:
            category = ConsentCategory.objects.get(pk=data_agreement_id, is_active=True)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")
        revision = ConsentService._get_latest_revision(category)
        return Response({
            "dataAgreement": DataAgreementSerializer(category).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })


# ===========================================================================
# Service API — Policy (read-only for individuals)
# GovStack path: /service/policy/{id}/
# ===========================================================================

class ServicePolicyDetailView(APIView):
    """GET /service/policy/{id}/ — read a Policy"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, policy_id):
        """READ — GovStack servicePolicyRead"""
        try:
            policy = ConsentPolicy.objects.get(pk=policy_id, is_active=True)
        except ConsentPolicy.DoesNotExist:
            raise NotFound("Policy not found.")
        # GovStack spec: optional ?revisionId= query param selects a specific revision.
        revision_id = request.query_params.get("revisionId")
        if revision_id:
            try:
                revision = ConsentRevision.objects.get(
                    pk=revision_id,
                    schema_name="Policy",
                    object_id=str(policy.pk),
                )
            except ConsentRevision.DoesNotExist:
                raise NotFound("Revision not found for this policy.")
        else:
            revision = (
                ConsentRevision.objects.filter(
                    schema_name="Policy",
                    object_id=str(policy.pk),
                    successor__isnull=True,
                ).first()
            )
        return Response({
            "policy": PolicySerializer(policy).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })


# ===========================================================================
# Service API — Verification
# GovStack paths: /service/verification/data-agreements/,
#                 /service/verification/consent-records/,
#                 /service/verification/consent-record/{id}/
# ===========================================================================

class ServiceVerificationDataAgreementsView(APIView):
    """
    GET /service/verification/data-agreements/
    LIST — fetch Data Agreements for data consumers to verify consent against.
    GovStack security: [{consumer: []}]
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsumerUser]

    def get(self, request):
        """LIST — GovStack serviceVerificationDataAgreementList"""
        qs = ConsentCategory.objects.filter(is_active=True).order_by("sort_order")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({
            "dataAgreements": DataAgreementSerializer(qs[offset: offset + limit], many=True).data,
        })


class ServiceVerificationConsentRecordsView(APIView):
    """
    GET /service/verification/consent-records/
    LIST — query consent records. Data consumers check whether consent exists.
    Supports ?individual_id= and ?data_agreement_id= filters.
    GovStack security: [{consumer: []}]
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsumerUser]

    def get(self, request):
        """LIST — GovStack serviceVerificationConsentRecordList"""
        # Filter on the GovStack canonical state field, not the legacy status field.
        # STATE_SIGNED is the authoritative indicator that an individual has consented.
        qs = ConsentRecord.objects.filter(
            state=ConsentRecord.STATE_SIGNED
        ).select_related("category", "citizen", "signature_obj", "data_agreement_revision")

        individual_id = request.query_params.get("individualId") or request.query_params.get("individual_id")
        if individual_id:
            qs = qs.filter(citizen_id=individual_id)

        agreement_id = request.query_params.get("dataAgreementId") or request.query_params.get("data_agreement_id")
        if agreement_id:
            qs = qs.filter(category_id=agreement_id)

        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)

        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(qs[offset: offset + limit], many=True).data,
        })


class ServiceVerificationConsentRecordDetailView(APIView):
    """
    GET /service/verification/consent-record/{id}/
    READ — read a single consent record for verification.
    GovStack security: [{consumer: []}]
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsConsumerUser]

    def get(self, request, consent_record_id):
        """READ — GovStack serviceVerificationConsentRecordRead"""
        try:
            record = ConsentRecord.objects.select_related("category", "citizen").get(
                pk=consent_record_id
            )
        except ConsentRecord.DoesNotExist:
            raise NotFound("ConsentRecord not found.")
        revision = None
        if record.data_agreement_revision_id:
            revision = record.data_agreement_revision
        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })


# ===========================================================================
# Service API — Individual ConsentRecord CRUD
# GovStack paths: /service/individual/record/consent-record/,
#                 /service/individual/record/consent-record/{id}/,
#                 /service/individual/record/consent-record/draft/,
#                 /service/individual/record/data-agreement/{id}/,
#                 /service/individual/record/ (DELETE = RTBF)
# ===========================================================================

class ServiceIndividualConsentRecordListView(APIView):
    """
    GET  /service/individual/record/consent-record/
         LIST — all consent records for the authenticated individual.

    POST /service/individual/record/consent-record/
         CREATE — create a consent record for a data agreement (grant/opt-in).
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """LIST — GovStack serviceIndividualConsentRecordList"""
        qs = ConsentRecord.objects.filter(
            citizen=request.user
        ).select_related("category", "signature_obj", "data_agreement_revision")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(qs[offset: offset + limit], many=True).data,
        })

    def post(self, request):
        """CREATE (grant consent) — GovStack serviceIndividualConsentRecordSignatureCreate"""
        payload = request.data.get("consentRecord", request.data)
        # F10 fix: accept all three field names the spec and clients may use:
        #   dataAgreement  (GovStack v23Q4 schema field name)
        #   dataAgreementId (CivicOS legacy / convenience)
        #   data_agreement_id (snake_case alias)
        category_id = (
            payload.get("dataAgreement")
            or payload.get("dataAgreementId")
            or payload.get("data_agreement_id")
        )
        if not category_id:
            raise ValidationError({"dataAgreement": "Required (also accepted: dataAgreementId)."})
        try:
            category = ConsentCategory.objects.get(pk=category_id, is_active=True)
        except (ConsentCategory.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"dataAgreement": "DataAgreement not found or inactive."})

        record = ConsentService.grant(
            citizen=request.user,
            category_slug=category.slug,
            request=request,
        )
        revision = record.data_agreement_revision
        sig = getattr(record, "signature_obj", None)

        # C-02 fix: if caller supplied a `signature` field in the envelope,
        # validate it and persist it (overwriting the auto-generated one).
        caller_sig_data = request.data.get("signature")
        if caller_sig_data:
            sig_serializer = SignatureSerializer(data=caller_sig_data)
            if sig_serializer.is_valid():
                existing_sig = getattr(record, "signature_obj", None)
                if existing_sig:
                    # Update the auto-generated signature with caller's values
                    for attr, val in sig_serializer.validated_data.items():
                        setattr(existing_sig, attr, val)
                    existing_sig.save()
                    sig = existing_sig
                else:
                    sig = sig_serializer.save(consent_record=record)
            else:
                # Caller supplied a malformed signature — reject
                raise ValidationError({"signature": sig_serializer.errors})

        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
            "signature": SignatureSerializer(sig).data if sig else None,
        })


class ServiceIndividualConsentRecordDetailView(APIView):
    """
    GET /service/individual/record/consent-record/{id}/ — read one record
    PUT /service/individual/record/consent-record/{id}/ — update opt_in (grant/withdraw)

    NOTE: There is NO DELETE on this path per the GovStack spec. The only delete
    path in the service namespace is DELETE /service/individual/record/ (RTBF),
    which enforces forgettable=True and emits an audit entry. A per-record delete
    outside that flow would bypass forgettable checks and the audit trail.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def _get_record(self, request, pk):
        try:
            return ConsentRecord.objects.select_related("category").get(
                pk=pk, citizen=request.user
            )
        except ConsentRecord.DoesNotExist:
            raise NotFound("ConsentRecord not found.")

    def get(self, request, consent_record_id):
        """READ — GovStack serviceIndividualConsentRecordRead"""
        record = self._get_record(request, consent_record_id)
        revision = record.data_agreement_revision
        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })

    def put(self, request, consent_record_id):
        """UPDATE — GovStack serviceIndividualConsentRecordUpdate"""
        record = self._get_record(request, consent_record_id)
        payload = request.data.get("consentRecord", request.data)
        opt_in = payload.get("optIn")
        if opt_in is None:
            raise ValidationError({"optIn": "Required."})

        if opt_in:
            record = ConsentService.grant(
                citizen=request.user,
                category_slug=record.category.slug,
                request=request,
            )
        else:
            record = ConsentService.withdraw(
                citizen=request.user,
                category_slug=record.category.slug,
                request=request,
            )
        revision = record.data_agreement_revision
        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })


class ServiceIndividualDataAgreementConsentRecordView(APIView):
    """
    GET  /service/individual/record/data-agreement/{id}/
         READ — fetch the ConsentRecord for a specific DataAgreement.

    POST /service/individual/record/data-agreement/{id}/
         CREATE — grant consent for a specific DataAgreement.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def _get_category(self, data_agreement_id):
        try:
            return ConsentCategory.objects.get(pk=data_agreement_id)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")

    def get(self, request, data_agreement_id):
        """READ — GovStack serviceIndividualConsentRecordRead"""
        category = self._get_category(data_agreement_id)
        try:
            # F4 fix: filter by is_current=True to get the authoritative current
            # record; avoids MultipleObjectsReturned after append-only history.
            record = ConsentRecord.objects.get(
                citizen=request.user, category=category, is_current=True
            )
        except ConsentRecord.DoesNotExist:
            raise NotFound("No ConsentRecord found for this DataAgreement.")
        return Response({"consentRecord": ConsentRecordGovStackSerializer(record).data})

    def post(self, request, data_agreement_id):
        """
        CREATE — GovStack serviceIndividualConsentRecordCreate

        F9 fix: parse optional ?individualId= and ?revisionId= query params per
        the GovStack v23Q4 spec:
          - individualId: whose consent to record (defaults to request.user)
          - revisionId:   which DataAgreement revision to consent to (must
                          belong to this DataAgreement; validated but the system
                          always grants against the latest revision to prevent
                          consenting to a stale/superseded DA)
        """
        category = self._get_category(data_agreement_id)
        if not category.is_active:
            raise ValidationError("DataAgreement is not active.")

        # F9: parse individualId — non-admins may only grant for themselves
        individual_id = (
            request.query_params.get("individualId")
            or request.query_params.get("individual_id")
        )
        if individual_id and str(request.user.pk) != str(individual_id):
            if not (request.user.is_staff or
                    request.user.groups.filter(name="consent_admins").exists()):
                raise PermissionDenied("You may only create consent records for yourself.")
            try:
                individual = User.objects.get(pk=individual_id)
            except (User.DoesNotExist, ValueError, TypeError):
                raise NotFound("Individual not found.")
        else:
            individual = request.user

        # F9: parse revisionId — validate it belongs to this DataAgreement
        revision_id = (
            request.query_params.get("revisionId")
            or request.query_params.get("revision_id")
        )
        target_revision = None
        if revision_id:
            try:
                target_revision = ConsentRevision.objects.get(
                    pk=revision_id,
                    schema_name="DataAgreement",
                    object_id=str(category.pk),
                )
            except (ConsentRevision.DoesNotExist, ValueError, TypeError):
                raise NotFound("Revision not found for this DataAgreement.")

        record = ConsentService.grant(
            citizen=individual,
            category_slug=category.slug,
            request=request,
            revision=target_revision,
        )
        revision = record.data_agreement_revision
        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
        })


class ServiceIndividualConsentRecordDraftView(APIView):
    """
    POST /service/individual/record/consent-record/draft/
         DRAFT — returns an unsigned (unsaved) ConsentRecord preview.

    GovStack spec defines this as POST (operationId: serviceIndividualConsentRecordDraftCreate).
    Query params per spec:
      ?individualId=<uuid>  required
      ?dataAgreementId=<id> required
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """DRAFT — GovStack serviceIndividualConsentRecordDraftCreate"""
        individual_id = request.query_params.get("individualId") or request.query_params.get("individual_id")
        agreement_id = request.query_params.get("dataAgreementId") or request.query_params.get("data_agreement_id")

        # Both params are required per spec
        if not individual_id:
            raise ValidationError({"individualId": "Required."})
        if not agreement_id:
            raise ValidationError({"dataAgreementId": "Required."})

        try:
            category = ConsentCategory.objects.get(pk=agreement_id, is_active=True)
        except ConsentCategory.DoesNotExist:
            raise ValidationError({"dataAgreementId": "DataAgreement not found or inactive."})

        # F5 fix: non-admin callers may only request a draft for themselves.
        # Admins (is_staff or consent_admins group) may request for any individual.
        is_admin = (
            request.user.is_staff
            or request.user.groups.filter(name="consent_admins").exists()
        )
        if not is_admin and str(request.user.pk) != str(individual_id):
            raise PermissionDenied("You may only request a draft for yourself.")

        try:
            individual = User.objects.get(pk=individual_id)
        except (User.DoesNotExist, ValueError):
            raise ValidationError({"individualId": "Individual not found."})

        # GovStack spec: optional ?revisionId= selects a specific DataAgreement revision.
        revision_id = request.query_params.get("revisionId")
        if revision_id:
            try:
                revision = ConsentRevision.objects.get(
                    pk=revision_id,
                    schema_name="DataAgreement",
                    object_id=str(category.pk),
                )
            except ConsentRevision.DoesNotExist:
                raise NotFound("Revision not found for this DataAgreement.")
        else:
            revision = ConsentService._get_latest_revision(category)

        # Return a draft (no PK, no DB save).
        # IMPORTANT: dataAgreement and individual MUST be FK ID strings, not nested
        # objects. The spec ConsentRecord schema uses x-fk-model on both fields,
        # meaning the actual wire value is an ID — consistent with every other
        # ConsentRecord response in this codebase (ConsentRecordGovStackSerializer
        # uses PrimaryKeyRelatedField / UUIDField for these fields). Returning nested
        # objects would be a spec inconsistency and would fail cert harness schema
        # validation.
        draft = {
            "id": None,  # no PK — this is a draft
            "dataAgreement": str(category.pk),
            "dataAgreementRevision": str(revision.pk) if revision else None,
            "dataAgreementRevisionHash": revision.serialized_hash if revision else "",
            "individual": str(individual.pk),
            "optIn": False,
            "state": "unsigned",
            "signature": None,
        }
        return Response({
            "consentRecord": draft,
            # Stub signature — all 8 required GovStack Signature schema fields present.
            # The individual signs this client-side and submits via POST /signature/.
            "signature": {
                "id": None,
                "payload": "",
                "signature": None,
                "verificationMethod": "string",
                "verificationPayload": None,
                "verificationPayloadHash": None,
                "verificationSignedBy": None,
                "timestamp": None,
            },
        })


class ServiceIndividualRightToBeForgottenView(APIView):
    """
    DELETE /service/individual/record/
           Right to Be Forgotten — cascading delete of forgettable records.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        """DELETE — GovStack serviceIndividualConsentRecordDeleteAll"""
        result = ConsentService.right_to_be_forgotten(
            citizen=request.user,
            request=request,
        )
        return Response(result, status=status.HTTP_200_OK)


# ===========================================================================
# Audit API
# GovStack paths: /audit/consent-records/, /audit/consent-record/{id}/,
#                 /audit/data-agreements/, /audit/data-agreement/{id}/
# ===========================================================================

class AuditConsentRecordListView(APIView):
    """GET /audit/consent-records/ — list all consent records"""
    authentication_classes = _AUTH
    # C-01 fix: "any valid token" in GovStack spec means a valid authenticated request,
    # not unrestricted citizen access. Citizens have /service/ endpoints; data consumers
    # have /service/verification/ endpoints. Allowing citizen JWTs here would let any
    # user enumerate all other citizens' consent records — a PIPEDA violation.
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request):
        """LIST — GovStack auditConsentRecordList"""
        qs = ConsentRecord.objects.all().select_related("category", "citizen", "signature_obj", "data_agreement_revision")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(qs[offset: offset + limit], many=True).data,
            "total": qs.count(),
        })


class AuditConsentRecordDetailView(APIView):
    """GET /audit/consent-record/{id}/ — read a single consent record"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request, consent_record_id):
        """READ — GovStack auditConsentRecordRead"""
        try:
            record = ConsentRecord.objects.select_related("category", "citizen").get(
                pk=consent_record_id
            )
        except ConsentRecord.DoesNotExist:
            raise NotFound("ConsentRecord not found.")
        return Response({"consentRecord": ConsentRecordGovStackSerializer(record).data})


class AuditDataAgreementListView(APIView):
    """GET /audit/data-agreements/ — list all data agreements"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request):
        """LIST — GovStack auditDataAgreementList"""
        qs = ConsentCategory.objects.all().order_by("sort_order", "slug").select_related("policy")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        return Response({
            "dataAgreements": DataAgreementSerializer(qs[offset: offset + limit], many=True).data,
            "total": qs.count(),
        })


class AuditDataAgreementDetailView(APIView):
    """GET /audit/data-agreement/{id}/ — read a single data agreement"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request, data_agreement_id):
        """READ — GovStack auditDataAgreementRead"""
        try:
            category = ConsentCategory.objects.get(pk=data_agreement_id)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")
        return Response({"dataAgreement": DataAgreementSerializer(category).data})


class AuditConsentLogView(APIView):
    """
    GET /audit/consent-log/
    CivicOS extension: full consent audit trail with chain-integrity data.
    Satisfies GovStack Section 6.3 REQUIRED (tamper-proof audit).
    """
    authentication_classes = _AUTH
    # CivicOS extension endpoint — not in GovStack spec, keep auditor-only
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request):
        qs = ConsentAuditEntry.objects.all().order_by("-timestamp")
        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)
        individual_id = request.query_params.get("individualId")
        if individual_id:
            qs = qs.filter(citizen_id=individual_id)
        return Response({
            "consentLog": ConsentAuditEntrySerializer(qs[offset: offset + limit], many=True).data,
            "total": qs.count(),
        })


# ===========================================================================
# Service API — ConsentRecord Signature
# GovStack paths: /service/individual/record/consent-record/{id}/signature/
# ===========================================================================

class ServiceConsentRecordSignatureView(APIView):
    """
    POST /service/individual/record/consent-record/{consentRecordId}/signature/
         CREATE — attach a new Signature to a ConsentRecord.

    PUT  /service/individual/record/consent-record/{consentRecordId}/signature/
         UPDATE — replace the existing Signature on a ConsentRecord.

    The Consent BB stores whatever signature the caller provides without
    cryptographic verification — that is the verifier's responsibility.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def _get_record(self, request, pk):
        try:
            return ConsentRecord.objects.get(pk=pk, citizen=request.user)
        except ConsentRecord.DoesNotExist:
            raise NotFound("ConsentRecord not found.")

    def post(self, request, consent_record_id):
        """CREATE — GovStack serviceIndividualConsentRecordSignatureCreate"""
        record = self._get_record(request, consent_record_id)

        payload = request.data.get("signature", request.data)
        serializer = SignatureSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        vtype = serializer.validated_data.get("verification_type", "string")
        vpayload = serializer.validated_data.get("verification_payload", payload)

        sig = ConsentService.attach_signature(
            record=record,
            verification_type=vtype,
            verification_payload=vpayload if isinstance(vpayload, dict) else {"raw": str(vpayload)},
            request=request,
        )

        return Response({"signature": SignatureSerializer(sig).data})

    def put(self, request, consent_record_id):
        """UPDATE — GovStack serviceIndividualConsentRecordSignatureUpdate"""
        record = self._get_record(request, consent_record_id)

        try:
            sig = ConsentSignature.objects.get(consent_record=record)
        except ConsentSignature.DoesNotExist:
            raise NotFound("No signature found for this ConsentRecord. Use POST to create one.")

        payload = request.data.get("signature", request.data)
        serializer = SignatureSerializer(sig, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        sig = serializer.save()

        return Response({"signature": SignatureSerializer(sig).data}, status=status.HTTP_200_OK)


# ===========================================================================
# Service API — All ConsentRecords for a DataAgreement
# GovStack path: /service/individual/record/data-agreement/{id}/all/
# ===========================================================================

class ServiceIndividualDataAgreementAllConsentRecordsView(APIView):
    """
    GET /service/individual/record/data-agreement/{dataAgreementId}/all/

    LIST — fetches all consent records the authenticated individual has for a
    particular DataAgreement (including revoked/historical records).

    GovStack spec: security: [{OAuth2: ['individual']}]
    This is an individual-scoped endpoint — always filters to request.user.

    GovStack operationId: serviceIndividualDataAgreementConsentRecordList
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request, data_agreement_id):
        try:
            category = ConsentCategory.objects.get(pk=data_agreement_id)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")

        # Spec: individual scope — always scoped to the authenticated user.
        qs = ConsentRecord.objects.filter(
            category=category,
            citizen=request.user,
        ).select_related("category", "citizen", "signature_obj", "data_agreement_revision")

        offset = _safe_int(request.query_params.get("offset"), default=0)
        limit = _safe_int(request.query_params.get("limit"), default=50, max_val=500)

        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(
                qs[offset: offset + limit], many=True
            ).data,
        })


# ===========================================================================
# Module-level helpers
# ===========================================================================

# Mapping from GovStack camelCase field names → ConsentCategory model field names
_DA_CAMEL_TO_SNAKE = {
    "lawfulBasis": "lawful_basis",
    "dataUse": "data_use",
    "purposeEn": "purpose_en",
    "purposeFr": "purpose_fr",
    "nameEn": "name_en",
    "nameFr": "name_fr",
    "isRequired": "is_required",
    "isActive": "is_active",
    "controllerName": "controller_name",
    "controllerUrl": "controller_url",
    "sortOrder": "sort_order",
}


def _normalize_da_payload(payload: dict) -> dict:
    """
    Normalize a DataAgreement payload to always use the CivicOS snake_case
    field names that the DataAgreementSerializer understands.

    Accepts GovStack camelCase OR CivicOS snake_case.  Unknown keys are passed
    through unchanged.
    """
    out = {}
    for key, val in payload.items():
        out[_DA_CAMEL_TO_SNAKE.get(key, key)] = val
    return out


def _da_validated_to_model_fields(validated_data: dict) -> dict:
    """
    Convert DRF validated_data (which uses ``source`` attribute names) to
    the exact kwarg dict for ``ConsentCategory.objects.create(**data)``.

    DRF stores values under the ``source`` name, so we just pass validated_data
    through — but we remove any nested serializer objects (e.g. the policy FK
    is already resolved to an instance by DRF).
    """
    # Remove non-model keys that come from nested serializers or write_only fields
    skip = {"policy"}  # policy_id write_only maps to "policy" in validated_data
    return {k: v for k, v in validated_data.items() if k not in skip}
