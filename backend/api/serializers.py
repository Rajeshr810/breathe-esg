from rest_framework import serializers
from core.models import (
    IngestionJob, EmissionsRecord, AuditLog,
    FacilityLocation, EmissionFactor
)


class IngestionJobSerializer(serializers.ModelSerializer):
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = IngestionJob
        fields = [
            "id", "source_type", "source_type_display", "status", "status_display",
            "source_reference", "created_by_username", "created_at", "completed_at",
            "rows_total", "rows_success", "rows_failed", "rows_flagged",
        ]
        read_only_fields = fields


class EmissionFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmissionFactor
        fields = ["activity_type", "factor_kgco2e", "unit_of_activity", "source", "valid_from_year", "region"]


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = FacilityLocation
        fields = ["source_code", "display_name", "country_code", "grid_region"]


class AuditLogSerializer(serializers.ModelSerializer):
    performed_by_username = serializers.CharField(source="performed_by.username", read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "action", "action_display", "performed_by_username", "timestamp", "snapshot", "note"]


class EmissionsRecordListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — no nested relations."""
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)
    flag_count = serializers.SerializerMethodField()

    class Meta:
        model = EmissionsRecord
        fields = [
            "id", "source_type", "source_type_display",
            "scope", "scope_display", "category",
            "activity_description", "activity_date",
            "period_start", "period_end",
            "quantity_raw", "unit_raw",
            "quantity_normalized", "unit_normalized",
            "co2e_kg", "status", "status_display",
            "flags", "flag_count",
            "manually_edited", "created_at",
        ]

    def get_flag_count(self, obj):
        return len(obj.flags) if obj.flags else 0


class EmissionsRecordSerializer(serializers.ModelSerializer):
    """Full serializer for detail view — includes nested relations and audit trail."""
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)
    facility_detail = FacilitySerializer(source="facility", read_only=True)
    emission_factor_detail = EmissionFactorSerializer(source="emission_factor", read_only=True)
    reviewed_by_username = serializers.CharField(source="reviewed_by.username", read_only=True)
    locked_by_username = serializers.CharField(source="locked_by.username", read_only=True)
    audit_logs = AuditLogSerializer(many=True, read_only=True)

    class Meta:
        model = EmissionsRecord
        fields = [
            "id", "source_type", "source_type_display",
            "scope", "scope_display", "category",
            "facility_detail",
            "activity_description", "activity_date",
            "period_start", "period_end",
            "quantity_raw", "unit_raw",
            "quantity_normalized", "unit_normalized",
            "emission_factor_detail", "co2e_kg",
            "status", "status_display",
            "flags",
            "reviewed_by_username", "reviewed_at", "reviewer_note",
            "locked_at", "locked_by_username",
            "manually_edited", "edit_note",
            "created_at", "updated_at",
            "audit_logs",
        ]
        read_only_fields = [f for f in fields if f not in ["reviewer_note", "edit_note"]]
