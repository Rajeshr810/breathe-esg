"""
API Views — Ingestion and Analyst Review Dashboard
"""

import logging
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.contrib.auth import authenticate
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.authtoken.models import Token

from core.models import (
    Organization, IngestionJob, EmissionsRecord, AuditLog, FacilityLocation
)
from .serializers import (
    IngestionJobSerializer, EmissionsRecordSerializer,
    EmissionsRecordListSerializer, AuditLogSerializer
)
from ingestion.tasks import process_ingestion_job

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    username = request.data.get('username')
    password = request.data.get('password')
    user = authenticate(username=username, password=password)
    if not user:
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_400_BAD_REQUEST)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key})


def get_user_org(request):
    """Get the organization for the authenticated user."""
    membership = request.user.memberships.select_related("organization").first()
    if not membership:
        return None
    return membership.organization


class IngestionJobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = IngestionJobSerializer

    def get_queryset(self):
        org = get_user_org(self.request)
        if not org:
            return IngestionJob.objects.none()
        return IngestionJob.objects.filter(organization=org).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        return self._handle_upload(request)

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        return self._handle_upload(request)

    def _handle_upload(self, request):
        org = get_user_org(request)
        if not org:
            return Response({"error": "No organization found for user"}, status=400)

        source_type = request.data.get("source_type")
        if source_type not in [c[0] for c in IngestionJob.SourceType.choices]:
            return Response(
                {"error": f"Invalid source_type. Must be one of: {[c[0] for c in IngestionJob.SourceType.choices]}"},
                status=400
            )

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "No file provided"}, status=400)

        job = IngestionJob.objects.create(
            organization=org,
            source_type=source_type,
            source_reference=uploaded_file.name,
            raw_file=uploaded_file,
            created_by=request.user,
            status=IngestionJob.Status.PENDING,
        )

        try:
            process_ingestion_job(job.id)
        except Exception as e:
            logger.error(f"Ingestion job {job.id} failed: {e}")
            job.status = IngestionJob.Status.FAILED
            job.error_log = [{"error": str(e)}]
            job.save()

        job.refresh_from_db()
        return Response(IngestionJobSerializer(job).data, status=201)

    @action(detail=True, methods=["get"])
    def errors(self, request, pk=None):
        job = self.get_object()
        return Response({
            "job_id": str(job.id),
            "status": job.status,
            "rows_total": job.rows_total,
            "rows_success": job.rows_success,
            "rows_failed": job.rows_failed,
            "rows_flagged": job.rows_flagged,
            "error_log": job.error_log,
        })


class EmissionsRecordViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EmissionsRecordSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        org = get_user_org(self.request)
        if not org:
            return EmissionsRecord.objects.none()

        qs = EmissionsRecord.objects.filter(
            organization=org
        ).select_related(
            "job", "facility", "emission_factor", "reviewed_by"
        ).order_by("-activity_date")

        params = self.request.query_params
        if status_filter := params.get("status"):
            qs = qs.filter(status=status_filter)
        if scope := params.get("scope"):
            qs = qs.filter(scope=scope)
        if source := params.get("source_type"):
            qs = qs.filter(source_type=source)
        if job_id := params.get("job"):
            qs = qs.filter(job_id=job_id)
        if flagged := params.get("flagged"):
            if flagged.lower() == "true":
                qs = qs.exclude(flags=[])
        if date_from := params.get("date_from"):
            qs = qs.filter(activity_date__gte=date_from)
        if date_to := params.get("date_to"):
            qs = qs.filter(activity_date__lte=date_to)

        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return EmissionsRecordListSerializer
        return EmissionsRecordSerializer

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        record = self.get_object()
        if record.status == EmissionsRecord.Status.LOCKED:
            return Response({"error": "Cannot approve a locked record"}, status=400)
        note = request.data.get("note", "")
        record.status = EmissionsRecord.Status.APPROVED
        record.reviewed_by = request.user
        record.reviewed_at = timezone.now()
        record.reviewer_note = note
        record.save()
        AuditLog.objects.create(
            record=record,
            action=AuditLog.Action.APPROVED,
            performed_by=request.user,
            note=note,
            snapshot={
                "status": record.status,
                "co2e_kg": str(record.co2e_kg),
                "quantity_normalized": str(record.quantity_normalized),
            }
        )
        return Response(EmissionsRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def flag(self, request, pk=None):
        record = self.get_object()
        if record.status == EmissionsRecord.Status.LOCKED:
            return Response({"error": "Cannot flag a locked record"}, status=400)
        message = request.data.get("message", "Flagged by analyst")
        record.status = EmissionsRecord.Status.FLAGGED
        record.flags = record.flags + [{"code": "ANALYST_FLAG", "severity": "warning", "message": message}]
        record.save()
        AuditLog.objects.create(
            record=record,
            action=AuditLog.Action.FLAGGED,
            performed_by=request.user,
            note=message,
        )
        return Response(EmissionsRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        record = self.get_object()
        try:
            record.lock(request.user)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        return Response(EmissionsRecordSerializer(record).data)

    @action(detail=False, methods=["post"])
    def bulk_approve(self, request):
        ids = request.data.get("ids", [])
        note = request.data.get("note", "Bulk approved")
        org = get_user_org(request)
        records = EmissionsRecord.objects.filter(
            id__in=ids,
            organization=org,
        ).exclude(status=EmissionsRecord.Status.LOCKED)
        updated = 0
        for record in records:
            record.status = EmissionsRecord.Status.APPROVED
            record.reviewed_by = request.user
            record.reviewed_at = timezone.now()
            record.reviewer_note = note
            updated += 1
        EmissionsRecord.objects.bulk_update(
            records, ["status", "reviewed_by", "reviewed_at", "reviewer_note"]
        )
        AuditLog.objects.bulk_create([
            AuditLog(
                record=r,
                action=AuditLog.Action.APPROVED,
                performed_by=request.user,
                note=note,
            ) for r in records
        ])
        return Response({"approved": updated})

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        record = self.get_object()
        logs = AuditLog.objects.filter(record=record).order_by("timestamp")
        return Response(AuditLogSerializer(logs, many=True).data)


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def summary(self, request):
        org = get_user_org(request)
        if not org:
            return Response({"error": "No organization"}, status=400)

        records = EmissionsRecord.objects.filter(organization=org)

        scope_totals = records.filter(
            status__in=[EmissionsRecord.Status.APPROVED, EmissionsRecord.Status.LOCKED]
        ).values("scope").annotate(
            total_co2e_kg=Sum("co2e_kg"),
            count=Count("id")
        )

        pending_count = records.filter(status=EmissionsRecord.Status.PENDING).count()
        flagged_count = records.filter(status=EmissionsRecord.Status.FLAGGED).count()
        approved_count = records.filter(status=EmissionsRecord.Status.APPROVED).count()
        locked_count = records.filter(status=EmissionsRecord.Status.LOCKED).count()
        with_flags = records.exclude(flags=[]).count()

        recent_jobs = IngestionJob.objects.filter(
            organization=org
        ).order_by("-created_at")[:5].values(
            "id", "source_type", "status", "rows_total",
            "rows_success", "rows_failed", "rows_flagged", "created_at"
        )

        scope_data = {item["scope"]: item for item in scope_totals}

        return Response({
            "totals": {
                "scope_1_co2e_kg": scope_data.get("scope_1", {}).get("total_co2e_kg", 0),
                "scope_2_co2e_kg": scope_data.get("scope_2", {}).get("total_co2e_kg", 0),
                "scope_3_co2e_kg": scope_data.get("scope_3", {}).get("total_co2e_kg", 0),
            },
            "review_queue": {
                "pending": pending_count,
                "flagged": flagged_count,
                "approved": approved_count,
                "locked": locked_count,
                "with_flags": with_flags,
            },
            "recent_jobs": list(recent_jobs),
        })