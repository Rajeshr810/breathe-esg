"""
Utility Electricity Ingestion: Portal CSV / Green Button ESPI format.

WHY PORTAL CSV:
Utility data comes in several forms:
1. Green Button XML (ESPI standard) — used by US utilities that support it
2. Portal CSV export — available from virtually every utility's web portal
3. PDF bills — lowest-quality; requires OCR or manual entry
4. EDI 867 — used for large commercial accounts with interval data

We chose portal CSV because:
- It's available universally (PDF has no standard structure; EDI 867 requires
  utility-side setup the facilities team hasn't done)
- Green Button XML is cleaner but only ~60% of US utilities support it,
  and essentially none in UK/EU
- The facilities team already downloads these CSVs manually — we're just
  replacing the spreadsheet they paste into

GREEN BUTTON INFLUENCE:
Even though we parse CSV, our field model is influenced by Green Button:
- IntervalBlock: a reading covering a time interval
- ReadingType: what's being measured (active energy, reactive power, etc.)
- UsagePoint: the meter / account

REALISTIC QUIRKS HANDLED:
- Billing periods that span arbitrary date ranges (not calendar months)
- kWh vs MWh vs kVAh — utilities use all of these
- Negative consumption (solar/export rows in net-metering accounts)
- Multiple meters per account
- Half-hourly interval data (UK HH data) vs monthly billing
- Renewable attribution claims (RECs, PPAs, REGO certificates)
"""

import csv
import io
import re
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unit normalization to kWh (canonical for electricity)
# ---------------------------------------------------------------------------

ELECTRICITY_UNIT_TO_KWH = {
    "kwh": Decimal("1"),
    "kw·h": Decimal("1"),
    "kw-h": Decimal("1"),
    "mwh": Decimal("1000"),
    "gwh": Decimal("1000000"),
    "kvah": Decimal("1"),          # kVAh ≈ kWh for our purposes (ignores power factor)
    "kvarh": Decimal("0"),         # reactive energy — not included in consumption
    "therm": Decimal("29.3071"),   # gas meters sometimes included in combined utility exports
    "mmbtu": Decimal("293.071"),
    "gj": Decimal("277.778"),
}

# Column name aliases — utility portals have zero naming standardization
UTILITY_COLUMN_ALIASES = {
    # Meter identification
    "meter id": "meter_id",
    "meter number": "meter_id",
    "meter serial": "meter_id",
    "mpan": "meter_id",             # UK: Meter Point Administration Number
    "mprn": "meter_id",             # UK: Meter Point Reference Number
    "account number": "account_number",
    "account no": "account_number",
    "account no.": "account_number",
    "service address": "service_address",
    "site address": "service_address",
    "premises": "service_address",

    # Billing period
    "from date": "period_start",
    "to date": "period_end",
    "start date": "period_start",
    "end date": "period_end",
    "billing period start": "period_start",
    "billing period end": "period_end",
    "read date from": "period_start",
    "read date to": "period_end",
    "interval start": "period_start",
    "interval end": "period_end",

    # Consumption
    "consumption": "consumption",
    "usage": "consumption",
    "energy (kwh)": "consumption",
    "units consumed": "consumption",
    "net consumption": "consumption",
    "total consumption": "consumption",
    "quantity": "consumption",
    "amount": "consumption",

    # Unit
    "unit": "unit",
    "units": "unit",
    "uom": "unit",
    "unit of measure": "unit",

    # Tariff
    "tariff": "tariff_code",
    "rate code": "tariff_code",
    "tariff code": "tariff_code",

    # Supplier
    "supplier": "supplier",
    "utility": "supplier",
    "provider": "supplier",

    # Renewable
    "renewable": "is_renewable",
    "green tariff": "is_renewable",
    "renewable energy": "is_renewable",
    "100% renewable": "is_renewable",
}


@dataclass
class UtilityRow:
    meter_id: str
    account_number: str
    service_address: str
    period_start: Optional[date]
    period_end: Optional[date]
    period_start_raw: str
    period_end_raw: str
    consumption_raw: str
    consumption_kwh: Optional[Decimal]     # normalized to kWh
    consumption_unit_raw: str
    tariff_code: str
    supplier: str
    is_renewable: bool
    is_renewable_raw: str
    row_number: int
    parse_errors: list = field(default_factory=list)

    # QA flags set during parsing
    is_negative: bool = False              # solar / export?
    is_interval_data: bool = False         # HH data vs monthly billing
    period_days: Optional[int] = None


def _parse_utility_date(raw: str) -> Optional[date]:
    """
    Utility portals use a bewildering variety of date formats.
    """
    raw = raw.strip()
    if not raw:
        return None

    # Clean up common artefacts
    raw = raw.replace("T00:00:00", "").strip()

    formats = [
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d %b %Y",       # 15 Jan 2023
        "%d %B %Y",       # 15 January 2023
        "%b %d, %Y",      # Jan 15, 2023
        "%B %d, %Y",      # January 15, 2023
        "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_consumption(raw: str) -> Optional[Decimal]:
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _normalize_to_kwh(value: Decimal, unit_raw: str) -> Optional[Decimal]:
    unit_clean = unit_raw.strip().lower().replace(" ", "")
    factor = ELECTRICITY_UNIT_TO_KWH.get(unit_clean)
    if factor is None:
        return None
    if factor == Decimal("0"):
        return Decimal("0")  # reactive energy — not consumption
    return value * factor


def _infer_unit_from_column_name(col_name: str) -> Optional[str]:
    """Last resort: pull unit from column name like 'Energy (kWh)'."""
    match = re.search(r'\(([^)]+)\)', col_name)
    if match:
        return match.group(1).strip()
    return None


def _parse_renewable_flag(raw: str) -> bool:
    return raw.strip().lower() in {"yes", "true", "1", "y", "renewable", "100%", "green"}


class UtilityCSVParser:
    """
    Parses electricity portal CSV exports.

    Handles both:
    1. Monthly billing summary (one row per meter per billing period)
    2. Half-hourly / interval data (one row per 30-min interval)

    The difference matters for period gap detection — monthly data with a gap
    means a missing bill; HH data with a gap may just be a meter outage.
    """

    # Minimum rows before we consider it interval (HH) data
    # Monthly = ~12 rows/year/meter; HH = ~17,520 rows/year/meter
    INTERVAL_DATA_THRESHOLD = 100

    def __init__(self):
        self.rows_parsed = 0
        self.errors = []

    def detect_interval_data(self, rows: list) -> bool:
        """Heuristic: if many rows cover very short periods, it's interval data."""
        if len(rows) < self.INTERVAL_DATA_THRESHOLD:
            return False
        # Check if periods are short (< 2 days)
        short_periods = 0
        for row in rows[:50]:
            if row.period_start and row.period_end:
                delta = (row.period_end - row.period_start).days
                if delta <= 1:
                    short_periods += 1
        return short_periods > 25

    def parse(self, file_content: str) -> Iterator[UtilityRow]:
        """Parse utility CSV content."""

        # Some portals export with BOM marker
        file_content = file_content.lstrip("\ufeff")

        # Skip blank header lines (portals often export metadata first)
        lines = file_content.splitlines()
        data_start = 0
        for i, line in enumerate(lines):
            # Find first line that looks like a header (has multiple comma/tab separated values)
            parts = re.split(r'[,\t|;]', line)
            if len(parts) >= 3 and any(
                any(alias in p.strip().lower() for alias in ["meter", "date", "consumption", "usage", "account"])
                for p in parts
            ):
                data_start = i
                break

        content = "\n".join(lines[data_start:])

        # Detect delimiter
        sample_line = lines[data_start] if data_start < len(lines) else ""
        delimiter = ","
        for d in [",", "\t", ";", "|"]:
            if d in sample_line:
                delimiter = d
                break

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

        # Normalize headers
        original_fields = reader.fieldnames or []
        field_map = {}
        for f in original_fields:
            normalized = UTILITY_COLUMN_ALIASES.get(f.strip().lower(), f.strip().lower().replace(" ", "_"))
            field_map[f] = normalized

        # Find unit column — sometimes embedded in consumption column header
        inferred_unit_from_header = None
        for f in original_fields:
            if any(kw in f.lower() for kw in ["consumption", "usage", "energy"]):
                inferred_unit_from_header = _infer_unit_from_column_name(f)
                break

        for row_number, raw_row in enumerate(reader, start=data_start + 2):
            row = {field_map.get(k, k): v for k, v in raw_row.items() if k is not None}
            parse_errors = []

            # Skip completely empty rows
            if not any(v.strip() for v in row.values() if isinstance(v, str)):
                continue

            # Parse dates
            period_start_raw = row.get("period_start", "").strip()
            period_end_raw = row.get("period_end", "").strip()
            period_start = _parse_utility_date(period_start_raw)
            period_end = _parse_utility_date(period_end_raw)

            if period_start_raw and not period_start:
                parse_errors.append(f"Could not parse start date: '{period_start_raw}'")
            if period_end_raw and not period_end:
                parse_errors.append(f"Could not parse end date: '{period_end_raw}'")

            # Calculate period length
            period_days = None
            if period_start and period_end:
                period_days = (period_end - period_start).days
                if period_days < 0:
                    parse_errors.append("Period end is before period start")

            # Parse consumption
            consumption_raw = row.get("consumption", "").strip()
            consumption_parsed = _parse_consumption(consumption_raw)
            if consumption_raw and consumption_parsed is None:
                parse_errors.append(f"Could not parse consumption: '{consumption_raw}'")

            # Determine unit
            unit_raw = row.get("unit", "").strip()
            if not unit_raw and inferred_unit_from_header:
                unit_raw = inferred_unit_from_header

            # Normalize to kWh
            consumption_kwh = None
            if consumption_parsed is not None and unit_raw:
                consumption_kwh = _normalize_to_kwh(consumption_parsed, unit_raw)
                if consumption_kwh is None:
                    parse_errors.append(f"Unknown unit: '{unit_raw}'")

            is_renewable_raw = row.get("is_renewable", "").strip()

            yield UtilityRow(
                meter_id=row.get("meter_id", "").strip(),
                account_number=row.get("account_number", "").strip(),
                service_address=row.get("service_address", "").strip(),
                period_start=period_start,
                period_end=period_end,
                period_start_raw=period_start_raw,
                period_end_raw=period_end_raw,
                consumption_raw=consumption_raw,
                consumption_kwh=consumption_kwh,
                consumption_unit_raw=unit_raw,
                tariff_code=row.get("tariff_code", "").strip(),
                supplier=row.get("supplier", "").strip(),
                is_renewable=_parse_renewable_flag(is_renewable_raw),
                is_renewable_raw=is_renewable_raw,
                row_number=row_number,
                parse_errors=parse_errors,
                is_negative=(consumption_parsed or Decimal("0")) < 0,
                period_days=period_days,
            )
            self.rows_parsed += 1
