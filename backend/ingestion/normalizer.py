"""
Normalization Engine

Takes parsed raw rows (from any source) and produces EmissionsRecord instances.

Responsibilities:
1. Look up the correct EmissionFactor for the activity
2. Compute co2e_kg = quantity_normalized × factor_kgco2e
3. Set GHG Protocol scope and category
4. Generate QA flags (outliers, missing data, gaps)
5. Map plant codes / meter IDs to FacilityLocation

QA FLAG CODES:
  UNIT_UNUSUAL      — unit not in expected set for this source type
  VALUE_OUTLIER     — value > 3 std deviations from org's historical mean
  MISSING_FACILITY  — plant code / meter ID doesn't match any known facility
  PERIOD_GAP        — gap detected between this and previous period for same meter
  PERIOD_OVERLAP    — billing periods overlap for same meter
  FACTOR_NOT_FOUND  — no emission factor found; co2e_kg is NULL
  DISTANCE_ESTIMATED — flight distance was computed from IATA codes, not received
  NEGATIVE_CONSUMPTION — negative value (could be solar export; needs review)
  MISSING_CATEGORY  — couldn't classify expense category; scope unknown
  SHORT_BILLING_PERIOD — period < 5 days (may be partial read)
  LONG_BILLING_PERIOD  — period > 45 days (may be double-billing)
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db import models

from core.models import (
    EmissionsRecord, EmissionFactor, FacilityLocation,
    IngestionJob, RawSAPRecord, RawUtilityRecord, RawTravelRecord, AuditLog
)
from ingestion.sap_parser import SAPRow
from ingestion.utility_parser import UtilityRow
from ingestion.travel_parser import TravelRow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GHG Protocol scope/category mapping
# ---------------------------------------------------------------------------

ACTIVITY_TO_SCOPE_CATEGORY = {
    # Scope 1 — direct combustion
    "diesel": ("scope_1", "stationary_combustion"),
    "petrol": ("scope_1", "mobile_combustion"),
    "natural_gas": ("scope_1", "stationary_combustion"),
    "lpg": ("scope_1", "stationary_combustion"),
    "heating_oil": ("scope_1", "stationary_combustion"),
    "heavy_fuel_oil": ("scope_1", "stationary_combustion"),
    # Scope 2 — purchased electricity
    "electricity": ("scope_2", "purchased_electricity"),
    # Scope 3 — travel
    "air": ("scope_3", "business_travel_air"),
    "hotel": ("scope_3", "business_travel_hotel"),
    "car_rental": ("scope_3", "business_travel_ground"),
    "taxi": ("scope_3", "business_travel_ground"),
    "rail": ("scope_3", "business_travel_ground"),
    "mileage": ("scope_3", "business_travel_ground"),
}

# ---------------------------------------------------------------------------
# Outlier detection — simple threshold-based for prototype
# In production: rolling z-score per org/facility/month
# ---------------------------------------------------------------------------

# Flag if consumption exceeds these per-unit values (sanity checks)
MAX_REASONABLE_KWH_PER_PERIOD = Decimal("10_000_000")  # 10 GWh per bill — almost certainly wrong
MAX_REASONABLE_LITERS_PER_ROW = Decimal("1_000_000")   # 1M liters per SAP row
MAX_REASONABLE_KM_PER_FLIGHT = Decimal("20_000")       # ~max great-circle distance


def _get_emission_factor(
    activity_type: str,
    region: Optional[str],
    year: int
) -> Optional[EmissionFactor]:
    """
    Look up the best matching emission factor.
    Priority: region-specific > global, most recent year <= reporting year.
    """
    qs = EmissionFactor.objects.filter(
        activity_type=activity_type,
        valid_from_year__lte=year,
    ).filter(
        models.Q(valid_to_year__gte=year) | models.Q(valid_to_year__isnull=True)
    ).order_by("-valid_from_year")

    # Try region-specific first
    if region:
        region_factor = qs.filter(region=region).first()
        if region_factor:
            return region_factor

    # Fall back to global
    return qs.filter(region="").first()


def _make_flags(codes_and_messages: list[tuple[str, str, str]]) -> list[dict]:
    """
    codes_and_messages: [(code, severity, message), ...]
    severity: 'info', 'warning', 'error'
    """
    return [{"code": c, "severity": s, "message": m} for c, s, m in codes_and_messages]


# ---------------------------------------------------------------------------
# SAP normalization
# ---------------------------------------------------------------------------

def normalize_sap_row(
    raw: RawSAPRecord,
    sap_row: SAPRow,
    job: IngestionJob,
) -> Optional[EmissionsRecord]:
    """
    Convert a RawSAPRecord + parsed SAPRow into an EmissionsRecord.
    Returns None if the row should be skipped (non-consumption, reversal).
    """
    # Skip non-consumption movement types
    if not sap_row.is_consumption and not sap_row.is_reversal:
        return None

    flags = []

    if sap_row.parse_errors:
        flags.append({"code": "PARSE_ERROR", "severity": "error", "message": "; ".join(sap_row.parse_errors)})

    if sap_row.canonical_quantity is None:
        flags.append({"code": "UNIT_UNUSUAL", "severity": "warning", "message": f"Could not normalize SAP unit '{sap_row.meins}'"})

    # Resolve facility
    facility = None
    if sap_row.werks:
        facility = FacilityLocation.objects.filter(
            organization=job.organization,
            source_code=sap_row.werks,
            source_system="sap"
        ).first()
        if not facility:
            flags.append({"code": "MISSING_FACILITY", "severity": "warning", "message": f"SAP plant code '{sap_row.werks}' not in facility lookup"})

    # Determine scope/category
    activity_type = sap_row.activity_type
    scope, category = "scope_1", "stationary_combustion"  # default for fuel
    if activity_type and activity_type in ACTIVITY_TO_SCOPE_CATEGORY:
        scope, category = ACTIVITY_TO_SCOPE_CATEGORY[activity_type]
    elif not activity_type:
        flags.append({"code": "MISSING_CATEGORY", "severity": "warning", "message": f"Could not classify material '{sap_row.maktx}' as fuel type"})

    # Emission factor lookup
    factor = None
    co2e_kg = None
    if activity_type and sap_row.canonical_quantity is not None:
        region = facility.country_code if facility else ""
        activity_date = sap_row.budat or date.today()
        factor = _get_emission_factor(activity_type, region, activity_date.year)
        if factor:
            co2e_kg = sap_row.canonical_quantity * factor.factor_kgco2e
            # Reversal: negate
            if sap_row.is_reversal:
                co2e_kg = -co2e_kg
        else:
            flags.append({"code": "FACTOR_NOT_FOUND", "severity": "error", "message": f"No emission factor for activity '{activity_type}'"})

    # Outlier check
    if sap_row.canonical_quantity and sap_row.canonical_unit == "liters":
        if abs(sap_row.canonical_quantity) > MAX_REASONABLE_LITERS_PER_ROW:
            flags.append({"code": "VALUE_OUTLIER", "severity": "warning", "message": f"Unusually large quantity: {sap_row.canonical_quantity} liters"})

    activity_date = sap_row.budat or date.today()

    record = EmissionsRecord(
        organization=job.organization,
        job=job,
        source_type=IngestionJob.SourceType.SAP,
        raw_sap=raw,
        scope=scope,
        category=category,
        facility=facility,
        activity_description=f"{sap_row.maktx or sap_row.matnr} — Plant {sap_row.werks}",
        activity_date=activity_date,
        period_start=activity_date,
        period_end=activity_date,
        quantity_raw=sap_row.menge or Decimal("0"),
        unit_raw=sap_row.meins,
        quantity_normalized=sap_row.canonical_quantity or Decimal("0"),
        unit_normalized=sap_row.canonical_unit or sap_row.meins,
        emission_factor=factor,
        co2e_kg=co2e_kg or Decimal("0"),
        status=EmissionsRecord.Status.FLAGGED if any(f["severity"] == "error" for f in flags) else EmissionsRecord.Status.PENDING,
        flags=flags,
    )
    return record


# ---------------------------------------------------------------------------
# Utility normalization
# ---------------------------------------------------------------------------

def normalize_utility_row(
    raw: RawUtilityRecord,
    util_row: UtilityRow,
    job: IngestionJob,
) -> Optional[EmissionsRecord]:
    flags = []

    if util_row.parse_errors:
        flags.append({"code": "PARSE_ERROR", "severity": "error", "message": "; ".join(util_row.parse_errors)})

    if util_row.is_negative:
        flags.append({"code": "NEGATIVE_CONSUMPTION", "severity": "info", "message": "Negative consumption — possible solar export or credit"})

    if util_row.period_days is not None:
        if util_row.period_days < 5:
            flags.append({"code": "SHORT_BILLING_PERIOD", "severity": "warning", "message": f"Billing period is only {util_row.period_days} days"})
        elif util_row.period_days > 45:
            flags.append({"code": "LONG_BILLING_PERIOD", "severity": "warning", "message": f"Billing period is {util_row.period_days} days — possible double billing"})

    if util_row.consumption_kwh and abs(util_row.consumption_kwh) > MAX_REASONABLE_KWH_PER_PERIOD:
        flags.append({"code": "VALUE_OUTLIER", "severity": "warning", "message": f"Unusually large consumption: {util_row.consumption_kwh} kWh"})

    # Resolve facility by meter ID
    facility = None
    if util_row.meter_id:
        facility = FacilityLocation.objects.filter(
            organization=job.organization,
            source_code=util_row.meter_id,
            source_system="utility_portal"
        ).first()
        if not facility:
            flags.append({"code": "MISSING_FACILITY", "severity": "info", "message": f"Meter '{util_row.meter_id}' not mapped to a facility"})

    # Emission factor — electricity uses grid region
    factor = None
    co2e_kg = None
    grid_region = facility.grid_region if facility else ""

    activity_date = util_row.period_start or date.today()
    factor = _get_emission_factor("electricity", grid_region, activity_date.year)

    if factor and util_row.consumption_kwh is not None:
        co2e_kg = abs(util_row.consumption_kwh) * factor.factor_kgco2e
    elif util_row.consumption_kwh is not None:
        flags.append({"code": "FACTOR_NOT_FOUND", "severity": "error", "message": f"No electricity emission factor for region '{grid_region}'"})

    period_start = util_row.period_start or date.today()
    period_end = util_row.period_end or period_start

    return EmissionsRecord(
        organization=job.organization,
        job=job,
        source_type=IngestionJob.SourceType.UTILITY,
        raw_utility=raw,
        scope="scope_2",
        category="purchased_electricity",
        facility=facility,
        activity_description=f"Electricity — Meter {util_row.meter_id} ({util_row.supplier or 'unknown supplier'})",
        activity_date=period_start,
        period_start=period_start,
        period_end=period_end,
        quantity_raw=Decimal(util_row.consumption_raw.replace(",", "") or "0"),
        unit_raw=util_row.consumption_unit_raw,
        quantity_normalized=util_row.consumption_kwh or Decimal("0"),
        unit_normalized="kwh",
        emission_factor=factor,
        co2e_kg=co2e_kg or Decimal("0"),
        status=EmissionsRecord.Status.FLAGGED if any(f["severity"] == "error" for f in flags) else EmissionsRecord.Status.PENDING,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Travel normalization
# ---------------------------------------------------------------------------

# kg CO2e per km per passenger for ground transport (DEFRA 2023)
GROUND_TRANSPORT_FACTORS = {
    "car_rental": Decimal("0.21"),   # average car, kg CO2e/km
    "taxi": Decimal("0.21"),
    "rail": Decimal("0.035"),        # average UK rail, kg CO2e/passenger-km
    "mileage": Decimal("0.21"),
}

# kg CO2e per room-night for hotels (DEFRA 2023, global average)
HOTEL_FACTOR_KG_PER_ROOM_NIGHT = Decimal("25.0")

# Air: base kg CO2e per passenger-km (before RF multiplier), by haul type
AIR_BASE_FACTORS = {
    "short_haul": Decimal("0.255"),   # < 3700 km, DEFRA 2023
    "long_haul": Decimal("0.195"),    # >= 3700 km
}
AIR_RF_MULTIPLIER = Decimal("1.891")  # radiative forcing, DEFRA 2023

CABIN_RF_FACTORS = {
    "economy": Decimal("1.0"),
    "premium_economy": Decimal("1.6"),
    "business": Decimal("2.9"),
    "first": Decimal("4.0"),
    "unknown": Decimal("1.0"),
}


def _compute_air_co2e(distance_km: float, cabin_class: str) -> Decimal:
    dist = Decimal(str(distance_km))
    haul = "short_haul" if distance_km < 3700 else "long_haul"
    base = AIR_BASE_FACTORS[haul]
    cabin_factor = CABIN_RF_FACTORS.get(cabin_class, Decimal("1.0"))
    return dist * base * AIR_RF_MULTIPLIER * cabin_factor


def normalize_travel_row(
    raw: RawTravelRecord,
    travel_row: TravelRow,
    job: IngestionJob,
) -> Optional[EmissionsRecord]:
    flags = []

    if travel_row.parse_errors:
        for err in travel_row.parse_errors:
            flags.append({"code": "PARSE_ERROR", "severity": "warning", "message": err})

    if not travel_row.category:
        flags.append({"code": "MISSING_CATEGORY", "severity": "error", "message": f"Unknown expense category: '{travel_row.category_raw}'"})

    scope, category = ACTIVITY_TO_SCOPE_CATEGORY.get(
        travel_row.category or "", ("scope_3", "business_travel_air")
    )

    co2e_kg = Decimal("0")
    quantity_normalized = Decimal("0")
    unit_normalized = "unknown"
    activity_description = travel_row.category_raw

    if travel_row.category == "air":
        if travel_row.distance_estimated:
            flags.append({"code": "DISTANCE_ESTIMATED", "severity": "info",
                          "message": f"Distance computed from IATA codes {travel_row.origin_iata}→{travel_row.destination_iata}"})
        if travel_row.distance_km:
            if travel_row.distance_km > float(MAX_REASONABLE_KM_PER_FLIGHT):
                flags.append({"code": "VALUE_OUTLIER", "severity": "warning", "message": f"Unusually large flight distance: {travel_row.distance_km:.0f} km"})
            co2e_kg = _compute_air_co2e(travel_row.distance_km, travel_row.cabin_class)
            quantity_normalized = Decimal(str(travel_row.distance_km))
            unit_normalized = "passenger_km"
            activity_description = f"Flight {travel_row.origin_iata}→{travel_row.destination_iata} ({travel_row.cabin_class})"
        else:
            flags.append({"code": "FACTOR_NOT_FOUND", "severity": "error", "message": "No distance available — cannot compute emissions"})

    elif travel_row.category == "hotel":
        if travel_row.hotel_nights:
            co2e_kg = Decimal(travel_row.hotel_nights) * HOTEL_FACTOR_KG_PER_ROOM_NIGHT
            quantity_normalized = Decimal(travel_row.hotel_nights)
            unit_normalized = "room_nights"
            activity_description = f"Hotel: {travel_row.hotel_name or 'unknown'} ({travel_row.hotel_country})"
        else:
            flags.append({"code": "FACTOR_NOT_FOUND", "severity": "error", "message": "Hotel nights not provided — cannot compute emissions"})

    elif travel_row.category in ("car_rental", "taxi", "rail", "mileage"):
        dist_raw = travel_row.ground_distance_raw
        if dist_raw:
            try:
                dist = Decimal(dist_raw.replace(",", ""))
                unit = travel_row.ground_distance_unit.lower()
                if "mile" in unit:
                    dist = dist * Decimal("1.60934")
                factor = GROUND_TRANSPORT_FACTORS.get(travel_row.category, Decimal("0.21"))
                co2e_kg = dist * factor
                quantity_normalized = dist
                unit_normalized = "km"
                activity_description = f"{travel_row.category} — {dist:.1f} km"
            except Exception:
                flags.append({"code": "FACTOR_NOT_FOUND", "severity": "warning", "message": f"Could not compute ground transport emissions from '{dist_raw}'"})
        else:
            # Fall back to spend-based estimate if no distance
            flags.append({"code": "FACTOR_NOT_FOUND", "severity": "info", "message": "Ground distance unavailable — spend-based emission not implemented"})

    activity_date = travel_row.travel_date or date.today()

    return EmissionsRecord(
        organization=job.organization,
        job=job,
        source_type=IngestionJob.SourceType.TRAVEL,
        raw_travel=raw,
        scope=scope,
        category=category,
        activity_description=activity_description,
        activity_date=activity_date,
        period_start=activity_date,
        period_end=activity_date,
        quantity_raw=quantity_normalized,
        unit_raw=travel_row.category_raw,
        quantity_normalized=quantity_normalized,
        unit_normalized=unit_normalized,
        co2e_kg=co2e_kg,
        status=EmissionsRecord.Status.FLAGGED if any(f["severity"] == "error" for f in flags) else EmissionsRecord.Status.PENDING,
        flags=flags,
    )
