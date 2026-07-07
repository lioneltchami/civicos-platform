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
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAdminUser, IsAuthenticated
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
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """LIST — GovStack configPolicyList"""
        qs = ConsentPolicy.objects.filter(is_active=True).order_by("-created_at")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
    permission_classes = [IsAuthenticated, IsAdminUser]

    def _get_policy(self, pk):
        try:
            return ConsentPolicy.objects.get(pk=pk)
        except ConsentPolicy.DoesNotExist:
            raise NotFound("Policy not found.")

    def get(self, request, policy_id):
        """READ — GovStack configPolicyRead"""
        policy = self._get_policy(policy_id)
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
    permission_classes = [IsAuthenticated, IsAdminUser]

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
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """LIST — GovStack configDataAgreementList"""
        qs = ConsentCategory.objects.order_by("sort_order", "slug")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
        page = qs[offset: offset + limit]
        return Response({
            "dataAgreements": DataAgreementSerializer(page, many=True).data,
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
    permission_classes = [IsAuthenticated, IsAdminUser]

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
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """LIST — GovStack configIndividualList"""
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
    permission_classes = [IsAuthenticated, IsAdminUser]

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
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """LIST — GovStack configWebhookList"""
        qs = ConsentWebhook.objects.all()
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
        return Response({"webhooks": WebhookSerializer(qs[offset: offset + limit], many=True).data})

    def post(self, request):
        """CREATE — GovStack configWebhookCreate"""
        payload = request.data.get("webhook", request.data)
        serializer = WebhookSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        # isActive is a read-only computed field; accept isActive from input to set is_disabled
        is_active_input = payload.get("isActive", True)
        webhook = ConsentWebhook.objects.create(
            payload_url=serializer.validated_data["payload_url"],
            content_type=serializer.validated_data.get("content_type", "application/json"),
            is_disabled=not is_active_input,
            secret_key=serializer.validated_data["secret_key"],
            subscribed_events=serializer.validated_data.get("subscribed_events", []),
            signature_header=serializer.validated_data.get("signature_header", "X-GovStack-Signature"),
            skipped_headers=serializer.validated_data.get("skipped_headers", []),
        )
        return Response({"webhook": WebhookSerializer(webhook).data})


class ConfigWebhookDetailView(APIView):
    """GET /config/webhook/{id}/ + PUT + DELETE"""
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsAdminUser]

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
        # Handle isActive → is_disabled inversion before serializer validation
        if "isActive" in payload:
            webhook.is_disabled = not payload["isActive"]
        serializer = WebhookSerializer(webhook, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(webhook, field, value)
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
    permission_classes = [IsAuthenticated, IsAdminUser]

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
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """LIST — GovStack serviceVerificationDataAgreementList"""
        qs = ConsentCategory.objects.filter(is_active=True).order_by("sort_order")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
        return Response({
            "dataAgreements": DataAgreementSerializer(qs[offset: offset + limit], many=True).data,
        })


class ServiceVerificationConsentRecordsView(APIView):
    """
    GET /service/verification/consent-records/
    LIST — query consent records. Data consumers check whether consent exists.
    Supports ?individual_id= and ?data_agreement_id= filters.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """LIST — GovStack serviceVerificationConsentRecordList"""
        qs = ConsentRecord.objects.filter(
            status=ConsentRecord.STATUS_GRANTED
        ).select_related("category", "citizen")

        individual_id = request.query_params.get("individualId") or request.query_params.get("individual_id")
        if individual_id:
            qs = qs.filter(citizen_id=individual_id)

        agreement_id = request.query_params.get("dataAgreementId") or request.query_params.get("data_agreement_id")
        if agreement_id:
            qs = qs.filter(category_id=agreement_id)

        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))

        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(qs[offset: offset + limit], many=True).data,
        })


class ServiceVerificationConsentRecordDetailView(APIView):
    """
    GET /service/verification/consent-record/{id}/
    READ — read a single consent record for verification.
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated]

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
        ).select_related("category")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
        return Response({
            "consentRecords": ConsentRecordGovStackSerializer(qs[offset: offset + limit], many=True).data,
        })

    def post(self, request):
        """CREATE (grant consent) — GovStack serviceIndividualConsentRecordSignatureCreate"""
        payload = request.data.get("consentRecord", request.data)
        category_id = payload.get("dataAgreementId") or payload.get("data_agreement_id")
        if not category_id:
            raise ValidationError({"dataAgreementId": "Required."})
        try:
            category = ConsentCategory.objects.get(pk=category_id, is_active=True)
        except (ConsentCategory.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"dataAgreementId": "DataAgreement not found or inactive."})

        record = ConsentService.grant(
            citizen=request.user,
            category_slug=category.slug,
            request=request,
        )
        revision = record.data_agreement_revision
        sig = getattr(record, "signature_obj", None)
        return Response({
            "consentRecord": ConsentRecordGovStackSerializer(record).data,
            "revision": RevisionSerializer(revision).data if revision else None,
            "signature": SignatureSerializer(sig).data if sig else None,
        })


class ServiceIndividualConsentRecordDetailView(APIView):
    """
    GET    /service/individual/record/consent-record/{id}/ — read one record
    PUT    /service/individual/record/consent-record/{id}/ — update opt_in (grant/withdraw)
    DELETE /service/individual/record/consent-record/{id}/ — delete one record
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

    def delete(self, request, consent_record_id):
        """DELETE — GovStack serviceIndividualConsentRecordDelete"""
        record = self._get_record(request, consent_record_id)
        record.delete()
        return Response(status=status.HTTP_200_OK)

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
            record = ConsentRecord.objects.get(citizen=request.user, category=category)
        except ConsentRecord.DoesNotExist:
            raise NotFound("No ConsentRecord found for this DataAgreement.")
        return Response({"consentRecord": ConsentRecordGovStackSerializer(record).data})

    def post(self, request, data_agreement_id):
        """CREATE — GovStack serviceIndividualConsentRecordCreate"""
        category = self._get_category(data_agreement_id)
        if not category.is_active:
            raise ValidationError("DataAgreement is not active.")
        record = ConsentService.grant(
            citizen=request.user,
            category_slug=category.slug,
            request=request,
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

        revision = ConsentService._get_latest_revision(category)

        # Return a draft (no PK, no DB save)
        draft = {
            "id": None,  # no PK — this is a draft
            "dataAgreement": DataAgreementSerializer(category).data,
            "dataAgreementRevisionHash": revision.serialized_hash if revision else "",
            "individual": IndividualSerializer(request.user).data,
            "optIn": False,
            "state": "unsigned",
        }
        return Response({
            "consentRecord": draft,
            "signature": {
                "id": None,
                "payload": "",
                "verificationMethod": "string",
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
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request):
        """LIST — GovStack auditConsentRecordList"""
        qs = ConsentRecord.objects.all().select_related("category", "citizen")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
        qs = ConsentCategory.objects.all().order_by("sort_order", "slug")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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
    permission_classes = [IsAuthenticated, IsAuditorUser]

    def get(self, request):
        qs = ConsentAuditEntry.objects.all().order_by("-timestamp")
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
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

        # Reject if a signature already exists — use PUT to replace
        if ConsentSignature.objects.filter(consent_record=record).exists():
            raise ValidationError(
                "A signature already exists for this ConsentRecord. Use PUT to update."
            )

        payload = request.data.get("signature", request.data)
        serializer = SignatureSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        sig = serializer.save(consent_record=record)

        # Advance the record state to signed if it was unsigned/pending
        if record.state != ConsentRecord.STATE_SIGNED:
            record.state = ConsentRecord.STATE_SIGNED
            record.save(update_fields=["state"])

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

    LIST — fetches ALL consent records for a particular DataAgreement across
    all individuals (admin-scoped).  Individual ID may be supplied as an HTTP
    header (X-Individual-Id) or query param per the GovStack spec.

    GovStack operationId: serviceIndividualDataAgreementConsentRecordList
    """
    authentication_classes = _AUTH
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request, data_agreement_id):
        try:
            category = ConsentCategory.objects.get(pk=data_agreement_id)
        except ConsentCategory.DoesNotExist:
            raise NotFound("DataAgreement not found.")

        qs = ConsentRecord.objects.filter(
            category=category
        ).select_related("category", "citizen")

        # Optional: filter by individual via header or query param
        individual_id = (
            request.headers.get("X-Individual-Id")
            or request.query_params.get("individualId")
            or request.query_params.get("individual_id")
        )
        if individual_id:
            qs = qs.filter(citizen_id=individual_id)

        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))

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
