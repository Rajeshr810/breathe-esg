"""
Core data models for Breathe ESG ingestion platform.

Design philosophy:
- Every emissions row is immutable once approved; edits create new versions
- Raw values are always preserved alongside normalized values
- Every row knows exactly where it came from (source file, job, timestamp)
- Multi-tenancy is enforced at the ORM level via Organization FK
- Scope classification (1/2/3) follows GHG Protocol definitions
"""

import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

class Organization(models.Model):
    """
    Top-level tenant. Every data row is scoped to an org.
    In production this would integrate with an auth provider (Okta, etc.)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Reporting year boundaries — clients often have non-calendar fiscal years
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)  # Jan

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    """Maps users to orgs with role-based access."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        ANALYST = "analyst", "Analyst"
        AUDITOR = "auditor", "Auditor (read-only)"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ANALYST)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "organization")]


# ---------------------------------------------------------------------------
# Ingestion Jobs — batch-level tracking
# ---------------------------------------------------------------------------

class IngestionJob(models.Model):
    """
    Represents one ingestion event: a file upload, an API pull, etc.
    Every EmissionsRecord links back to the job that created it.
    This lets analysts see "what came in this batch" and re-process if needed.
    """

    class SourceType(models.TextChoices):
        SAP = "sap", "SAP (Fuel & Procurement)"
        UTILITY = "utility", "Utility (Electricity)"
        TRAVEL = "travel", "Corporate Travel"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial (some rows failed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="ingestion_jobs")
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # File upload path or API endpoint used
    source_reference = models.TextField(help_text="File path, API endpoint, or other source identifier")

    # Raw file stored for reprocessing / audit
    raw_file = models.FileField(upload_to="raw_uploads/%Y/%m/", null=True, blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Counts for dashboard display
    rows_total = models.IntegerField(default=0)
    rows_success = models.IntegerField(default=0)
    rows_failed = models.IntegerField(default=0)
    rows_flagged = models.IntegerField(default=0)

    error_log = models.JSONField(default=list, help_text="List of row-level error dicts")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.organization.slug} | {self.source_type} | {self.created_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Reference / Lookup tables
# ---------------------------------------------------------------------------

class FacilityLocation(models.Model):
    """
    SAP plant codes, meter IDs, and facility identifiers resolve to this.
    SAP exports plant codes (e.g. '1000', 'DE01') with no human-readable name;
    the client must supply a mapping table during onboarding.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="facilities")
    # The raw code as it appears in the source system
    source_code = models.CharField(max_length=100)
    source_system = models.CharField(max_length=50, help_text="e.g. 'sap', 'utility_portal'")
    display_name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    region = models.CharField(max_length=100, blank=True)
    grid_region = models.CharField(
        max_length=100,
        blank=True,
        help_text="Electricity grid region for emission factor lookup (e.g. 'WECC', 'UK National Grid')"
    )

    class Meta:
        unique_together = [("organization", "source_code", "source_system")]


class EmissionFactor(models.Model):
    """
    Emission factors sourced from DEFRA, EPA, IPCC, or utility-specific grids.
    Versioned by year so historical calculations can be reproduced.
    """

    class Scope(models.TextChoices):
        SCOPE_1 = "scope_1", "Scope 1 (Direct)"
        SCOPE_2 = "scope_2", "Scope 2 (Purchased Energy)"
        SCOPE_3 = "scope_3", "Scope 3 (Value Chain)"

    class Category(models.TextChoices):
        # Scope 1
        STATIONARY_COMBUSTION = "stationary_combustion", "Stationary Combustion (fuel)"
        MOBILE_COMBUSTION = "mobile_combustion", "Mobile Combustion (fleet)"
        # Scope 2
        PURCHASED_ELECTRICITY = "purchased_electricity", "Purchased Electricity"
        # Scope 3
        BUSINESS_TRAVEL_AIR = "business_travel_air", "Business Travel - Air"
        BUSINESS_TRAVEL_HOTEL = "business_travel_hotel", "Business Travel - Hotel"
        BUSINESS_TRAVEL_GROUND = "business_travel_ground", "Business Travel - Ground"
        PURCHASED_GOODS = "purchased_goods", "Purchased Goods & Services"
        UPSTREAM_TRANSPORT = "upstream_transport", "Upstream Transportation"

    activity_type = models.CharField(max_length=100, help_text="e.g. 'diesel', 'natural_gas', 'electricity_uk'")
    category = models.CharField(max_length=50, choices=Category.choices)
    scope = models.CharField(max_length=10, choices=Scope.choices)

    # Factor value: kgCO2e per unit_of_activity
    factor_kgco2e = models.DecimalField(max_digits=12, decimal_places=6)
    unit_of_activity = models.CharField(max_length=50, help_text="e.g. 'liter', 'kWh', 'km', 'passenger_km'")

    source = models.CharField(max_length=100, help_text="e.g. 'DEFRA 2023', 'EPA eGRID 2022'")
    valid_from_year = models.IntegerField()
    valid_to_year = models.IntegerField(null=True, blank=True, help_text="Null means currently active")
    region = models.CharField(max_length=100, blank=True, help_text="Region-specific factors (e.g. grid mix)")

    class Meta:
        indexes = [
            models.Index(fields=["activity_type", "valid_from_year"]),
        ]


# ---------------------------------------------------------------------------
# Raw staging tables — one per source type
# ---------------------------------------------------------------------------

class RawSAPRecord(models.Model):
    """
    Staging table for SAP flat-file exports (MM60 / ME2M report format).
    We preserve every raw field before normalization.
    SAP exports often have German column headers, plant codes, and dates
    in DD.MM.YYYY format — all preserved here as-ingested.
    """
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="sap_records")

    # SAP document identifiers
    belnr = models.CharField(max_length=20, blank=True, help_text="SAP document number (Belegnummer)")
    bukrs = models.CharField(max_length=4, blank=True, help_text="Company code (Buchungskreis)")
    werks = models.CharField(max_length=4, blank=True, help_text="Plant code (Werk)")
    matnr = models.CharField(max_length=18, blank=True, help_text="Material number")
    maktx = models.CharField(max_length=255, blank=True, help_text="Material description")

    # Raw date as it came in (SAP uses DD.MM.YYYY in most locales)
    budat_raw = models.CharField(max_length=20, help_text="Posting date raw string")
    budat = models.DateField(null=True, blank=True, help_text="Parsed posting date")

    # Quantity and unit as-received
    menge_raw = models.CharField(max_length=50, help_text="Quantity raw string")
    meins = models.CharField(max_length=10, help_text="Base unit of measure (SAP UoM code)")

    # Value in document currency
    dmbtr_raw = models.CharField(max_length=50, blank=True)
    waers = models.CharField(max_length=5, blank=True, help_text="Currency code")

    # Movement type — determines if this is consumption (261) or reversal (262)
    bwart = models.CharField(max_length=4, blank=True, help_text="Movement type (Bewegungsart)")

    # Cost center / GL account for procurement categorization
    kostl = models.CharField(max_length=10, blank=True, help_text="Cost center")
    sakto = models.CharField(max_length=10, blank=True, help_text="G/L account")

    # Ingestion metadata
    row_number = models.IntegerField(help_text="Row number in source file for error tracing")
    parse_errors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class RawUtilityRecord(models.Model):
    """
    Staging table for utility portal CSV exports.
    Green Button / ESPI format (the US standard) or UK HH data exports.
    Billing periods do NOT align with calendar months; this is preserved exactly.
    """
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="utility_records")

    # Meter identification
    meter_id = models.CharField(max_length=100)
    account_number = models.CharField(max_length=100, blank=True)
    service_address = models.CharField(max_length=255, blank=True)

    # Billing period — explicitly start/end, not "month" — bills span arbitrary ranges
    period_start_raw = models.CharField(max_length=50)
    period_end_raw = models.CharField(max_length=50)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    # Consumption
    consumption_raw = models.CharField(max_length=50, help_text="As it appears in the export")
    consumption_unit_raw = models.CharField(max_length=20, help_text="kWh, MWh, kVAh, therms, etc.")

    # Tariff / rate info preserved for context
    tariff_code = models.CharField(max_length=50, blank=True)
    supplier = models.CharField(max_length=100, blank=True)

    # Is this a renewable / RECs-matched supply?
    is_renewable_raw = models.CharField(max_length=50, blank=True)

    row_number = models.IntegerField()
    parse_errors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class RawTravelRecord(models.Model):
    """
    Staging table for corporate travel exports (Concur SAE / Navan API format).
    Different trip categories require different emission factor lookups.
    Distances are often absent — only origin/destination codes given.
    """

    class TripCategory(models.TextChoices):
        AIR = "air", "Air"
        HOTEL = "hotel", "Hotel"
        CAR_RENTAL = "car_rental", "Car Rental"
        RAIL = "rail", "Rail"
        TAXI_RIDESHARE = "taxi", "Taxi / Rideshare"
        MILEAGE = "mileage", "Personal Vehicle Mileage"

    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="travel_records")

    # Expense / trip identifiers
    expense_key = models.CharField(max_length=100, blank=True, help_text="Concur expense key or Navan trip ID")
    employee_id = models.CharField(max_length=100, blank=True, help_text="Hashed — PII not stored in clear")
    cost_center = models.CharField(max_length=50, blank=True)

    category_raw = models.CharField(max_length=100, help_text="As it came from the platform")
    category = models.CharField(max_length=20, choices=TripCategory.choices, null=True, blank=True)

    # Dates
    travel_date_raw = models.CharField(max_length=50)
    travel_date = models.DateField(null=True, blank=True)

    # Air travel specifics
    origin_iata = models.CharField(max_length=3, blank=True)
    destination_iata = models.CharField(max_length=3, blank=True)
    cabin_class_raw = models.CharField(max_length=50, blank=True)

    # Distance (may be absent — we compute from IATA codes if missing)
    distance_raw = models.CharField(max_length=50, blank=True)
    distance_unit_raw = models.CharField(max_length=20, blank=True)

    # Hotel
    hotel_name = models.CharField(max_length=255, blank=True)
    hotel_country = models.CharField(max_length=2, blank=True, help_text="ISO country code")
    hotel_nights = models.IntegerField(null=True, blank=True)

    # Ground transport
    ground_distance_raw = models.CharField(max_length=50, blank=True)
    ground_distance_unit_raw = models.CharField(max_length=20, blank=True)

    # Spend (used as proxy when physical quantity unavailable)
    amount_raw = models.CharField(max_length=50, blank=True)
    currency_raw = models.CharField(max_length=5, blank=True)

    row_number = models.IntegerField()
    parse_errors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# Normalized emissions records — the canonical table
# ---------------------------------------------------------------------------

class EmissionsRecord(models.Model):
    """
    THE canonical table. Every row here represents one normalized, attributed
    emissions activity, regardless of source system.

    Design decisions:
    1. Raw staging records are preserved and FK'd here — you can always trace
       back to the exact source row.
    2. quantity_normalized is always in a canonical unit (liters for fuel,
       kWh for electricity, km for travel) — raw unit preserved for traceability.
    3. co2e_kg is computed at ingestion time using the matched EmissionFactor,
       but can be recomputed if factors are updated.
    4. Status flow: pending → flagged | approved → locked
       Once locked (sent to auditor), no edits allowed — a correction creates
       a new record with a superseded_by link.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        FLAGGED = "flagged", "Flagged — Needs Attention"
        APPROVED = "approved", "Approved"
        LOCKED = "locked", "Locked (Sent to Audit)"
        SUPERSEDED = "superseded", "Superseded by Correction"

    class Scope(models.TextChoices):
        SCOPE_1 = "scope_1", "Scope 1"
        SCOPE_2 = "scope_2", "Scope 2"
        SCOPE_3 = "scope_3", "Scope 3"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="emissions_records")
    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="emissions_records")

    # --- Source traceability ---
    source_type = models.CharField(max_length=20, choices=IngestionJob.SourceType.choices)
    # Exactly one of these will be set — the raw staging record this came from
    raw_sap = models.OneToOneField(RawSAPRecord, null=True, blank=True, on_delete=models.SET_NULL)
    raw_utility = models.OneToOneField(RawUtilityRecord, null=True, blank=True, on_delete=models.SET_NULL)
    raw_travel = models.OneToOneField(RawTravelRecord, null=True, blank=True, on_delete=models.SET_NULL)

    # --- GHG Protocol classification ---
    scope = models.CharField(max_length=10, choices=Scope.choices)
    category = models.CharField(max_length=50, choices=EmissionFactor.Category.choices)

    # --- Facility / location ---
    facility = models.ForeignKey(FacilityLocation, null=True, blank=True, on_delete=models.SET_NULL)

    # --- Activity data (normalized) ---
    activity_description = models.CharField(max_length=255, help_text="Human-readable: 'Diesel consumption', 'Grid electricity', 'LHR→JFK Business'")
    activity_date = models.DateField()
    period_start = models.DateField(help_text="For billing-period data like utility bills")
    period_end = models.DateField()

    # Raw quantity preserved exactly as received
    quantity_raw = models.DecimalField(max_digits=16, decimal_places=4)
    unit_raw = models.CharField(max_length=30)

    # Normalized quantity in canonical unit for this category
    quantity_normalized = models.DecimalField(max_digits=16, decimal_places=4)
    unit_normalized = models.CharField(max_length=30, help_text="liters, kWh, km, passenger_km, room_nights")

    # --- Emissions computation ---
    emission_factor = models.ForeignKey(EmissionFactor, null=True, on_delete=models.SET_NULL)
    co2e_kg = models.DecimalField(max_digits=16, decimal_places=4, help_text="Computed: quantity_normalized × factor_kgco2e")

    # --- Review status ---
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)

    # --- Anomaly / QA flags ---
    flags = models.JSONField(
        default=list,
        help_text=(
            "List of flag dicts: [{code, severity, message}]. "
            "Codes: UNIT_UNUSUAL, VALUE_OUTLIER, MISSING_FACILITY, "
            "PERIOD_GAP, PERIOD_OVERLAP, FACTOR_NOT_FOUND, DISTANCE_ESTIMATED"
        )
    )

    # --- Analyst review ---
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_records")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_note = models.TextField(blank=True)

    # --- Audit locking ---
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="locked_records")

    # --- Correction chain ---
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="supersedes",
        help_text="If this record was corrected post-lock, points to the replacement"
    )

    # --- Edit tracking ---
    # Was this record manually edited after ingestion?
    manually_edited = models.BooleanField(default=False)
    edit_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activity_date"]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "scope", "activity_date"]),
            models.Index(fields=["job"]),
        ]

    def __str__(self):
        return f"{self.organization.slug} | {self.scope} | {self.activity_description} | {self.co2e_kg} kgCO2e"

    def approve(self, user):
        """Approve this record for audit submission."""
        self.status = self.Status.APPROVED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        AuditLog.objects.create(
            record=self,
            action=AuditLog.Action.APPROVED,
            performed_by=user,
        )

    def lock(self, user):
        """Lock after audit submission. No further edits permitted."""
        if self.status != self.Status.APPROVED:
            raise ValueError("Only approved records can be locked.")
        self.status = self.Status.LOCKED
        self.locked_at = timezone.now()
        self.locked_by = user
        self.save(update_fields=["status", "locked_at", "locked_by", "updated_at"])
        AuditLog.objects.create(
            record=self,
            action=AuditLog.Action.LOCKED,
            performed_by=user,
        )


# ---------------------------------------------------------------------------
# Audit trail — immutable log
# ---------------------------------------------------------------------------

class AuditLog(models.Model):
    """
    Append-only log of every state change on an EmissionsRecord.
    Never deleted. This is what auditors actually look at.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created by ingestion"
        FLAGGED = "flagged", "Flagged"
        FLAG_CLEARED = "flag_cleared", "Flag cleared"
        EDITED = "edited", "Manually edited"
        APPROVED = "approved", "Approved"
        APPROVAL_REVOKED = "approval_revoked", "Approval revoked"
        LOCKED = "locked", "Locked for audit"
        SUPERSEDED = "superseded", "Superseded by correction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey(EmissionsRecord, on_delete=models.CASCADE, related_name="audit_logs")
    action = models.CharField(max_length=30, choices=Action.choices)
    performed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(auto_now_add=True)

    # Snapshot of key fields at time of action
    snapshot = models.JSONField(
        default=dict,
        help_text="Stores {status, co2e_kg, quantity_normalized, flags} at time of action"
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["timestamp"]
        # Explicitly no update/delete permissions should be granted on this table
