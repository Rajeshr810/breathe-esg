"""
SAP Ingestion: Flat-file MM60 / ME2M goods movement report.

WHY THIS FORMAT:
SAP exposes data many ways: IDocs (EDI), OData (S/4HANA), BAPIs, and flat-file
reports. For an enterprise client that isn't running a full S/4HANA with API
access, the most common real-world handoff is a scheduled background job (SM36)
that runs a goods-movement report (MB51, ME2M, or MM60) and drops a text file
on an SFTP server or emails it to a distribution list.

IDocs would be cleaner but require SAP Basis configuration the client probably
hasn't done. OData requires S/4HANA or a Fiori setup. The flat-file report is
what sustainability teams actually have.

REALISTIC QUIRKS HANDLED:
- Column headers in German (Buchungskreis, Werk, Menge, Bewegungsart)
- Dates in DD.MM.YYYY format (SAP German locale default)
- Quantities use period as thousands separator in some locales (1.234,56)
- Units in SAP internal codes: L (liter), KG (kilogram), M3 (cubic meter), KWH
- Movement types: 261=goods issue to cost center, 101=goods receipt, 201=GI to order
- Material descriptions truncated at 40 chars
- Plant codes that are just numbers ('1000', '2000') with no inherent meaning
"""

import csv
import io
import re
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SAP unit code → canonical unit mapping
# SAP uses its own UoM codes, not SI symbols.
# Source: SAP table T006 / MSEHI field
# ---------------------------------------------------------------------------
SAP_UNIT_MAP = {
    "L": "liters",
    "LTR": "liters",
    "GAL": "us_gallons",
    "GAL(I)": "imperial_gallons",
    "KG": "kg",
    "G": "grams",
    "T": "metric_tonnes",
    "M3": "cubic_meters",
    "KWH": "kwh",
    "MWH": "mwh",
    "GJ": "gj",
    "MMBTU": "mmbtu",
    "NM3": "normal_cubic_meters",  # natural gas
    "FT3": "cubic_feet",
    "M": "meters",
    "KM": "km",
    "MI": "miles",
    "PC": "pieces",
    "ST": "pieces",  # Stück (German for pieces)
}

# Conversion factors to canonical units per activity category
# canonical: liters for liquid fuel, kWh for energy, kg for mass
UNIT_CONVERSIONS = {
    "us_gallons": ("liters", Decimal("3.78541")),
    "imperial_gallons": ("liters", Decimal("4.54609")),
    "grams": ("kg", Decimal("0.001")),
    "metric_tonnes": ("kg", Decimal("1000")),
    "mwh": ("kwh", Decimal("1000")),
    "gj": ("kwh", Decimal("277.778")),
    "mmbtu": ("kwh", Decimal("293.071")),
    "normal_cubic_meters": ("liters", Decimal("1")),  # proxy; varies by gas type
    "cubic_feet": ("liters", Decimal("28.3168")),
    "cubic_meters": ("liters", Decimal("1000")),
    "miles": ("km", Decimal("1.60934")),
}

# Fuel material keywords → activity type for emission factor lookup
# This is a best-effort classification from material descriptions.
# In production, the client would supply a material→fuel type mapping table.
MATERIAL_FUEL_KEYWORDS = {
    "diesel": "diesel",
    "gasoil": "diesel",
    "petrol": "petrol",
    "gasoline": "petrol",
    "benzin": "petrol",  # German
    "natural gas": "natural_gas",
    "erdgas": "natural_gas",  # German
    "lpg": "lpg",
    "propan": "lpg",
    "heating oil": "heating_oil",
    "heizöl": "heating_oil",  # German
    "hfo": "heavy_fuel_oil",
    "schwer": "heavy_fuel_oil",  # German abbreviation
}

# Movement types that represent actual consumption (not stock transfer, reversal, etc.)
# 261 = goods issue to cost center (main consumption event)
# 201 = goods issue to order
# 221 = goods issue to project
CONSUMPTION_MOVEMENT_TYPES = {"261", "201", "221"}
REVERSAL_MOVEMENT_TYPES = {"262", "202", "222"}  # reversals of the above

# German column header → normalized field name mapping
# Real SAP exports can come in German or English depending on user language settings.
# We handle both.
COLUMN_ALIASES = {
    # German → normalized
    "buchungskreis": "bukrs",
    "werk": "werks",
    "material": "matnr",
    "materialkurztext": "maktx",
    "buchungsdatum": "budat",
    "menge": "menge",
    "mengeneinheit": "meins",
    "betrag in hauswährung": "dmbtr",
    "währung": "waers",
    "bewegungsart": "bwart",
    "kostenstelle": "kostl",
    "sachkonto": "sakto",
    "belegnummer": "belnr",
    # English variants
    "company code": "bukrs",
    "plant": "werks",
    "material number": "matnr",
    "material description": "maktx",
    "posting date": "budat",
    "quantity": "menge",
    "unit of measure": "meins",
    "amount in local currency": "dmbtr",
    "currency": "waers",
    "movement type": "bwart",
    "cost center": "kostl",
    "g/l account": "sakto",
    "document number": "belnr",
}


@dataclass
class SAPRow:
    """Parsed, validated SAP goods movement row."""
    belnr: str
    bukrs: str
    werks: str
    matnr: str
    maktx: str
    budat: Optional[date]
    budat_raw: str
    menge: Optional[Decimal]
    menge_raw: str
    meins: str  # SAP UoM code
    dmbtr: Optional[Decimal]
    waers: str
    bwart: str
    kostl: str
    sakto: str
    row_number: int
    parse_errors: list = field(default_factory=list)

    # Derived fields
    canonical_quantity: Optional[Decimal] = None
    canonical_unit: Optional[str] = None
    activity_type: Optional[str] = None
    is_consumption: bool = False
    is_reversal: bool = False


class SAPParseError(Exception):
    pass


def _normalize_header(header: str) -> str:
    """Map raw column header to normalized field name."""
    clean = header.strip().lower().rstrip(":")
    return COLUMN_ALIASES.get(clean, clean.replace(" ", "_").replace("/", "_"))


def _parse_sap_date(raw: str) -> Optional[date]:
    """
    SAP dates come in multiple formats depending on locale settings:
    - DD.MM.YYYY (German/European default)
    - MM/DD/YYYY (US locale)
    - YYYYMMDD (IDoc / BAPI format)
    We try all three.
    """
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_sap_quantity(raw: str) -> Optional[Decimal]:
    """
    SAP quantities use locale-specific separators.
    German locale: 1.234,56 (period=thousands, comma=decimal)
    US locale: 1,234.56
    We detect which by looking at position of separators.
    """
    raw = raw.strip()
    if not raw:
        return None

    # Remove leading minus (handled separately)
    negative = raw.startswith("-")
    raw = raw.lstrip("-").strip()

    # German format: last separator is comma
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            # German: 1.234,56 → 1234.56
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56 → 1234.56
            raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:
        # Could be German decimal: 1234,56 → 1234.56
        # Or US thousands: 1,234 → 1234
        # Heuristic: if digits after comma <= 3, treat as decimal
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) <= 3:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")

    try:
        result = Decimal(raw)
        return -result if negative else result
    except InvalidOperation:
        return None


def _infer_activity_type(maktx: str, matnr: str) -> Optional[str]:
    """
    Infer fuel type from material description.
    This is heuristic — in production, the client supplies a mapping table.
    """
    text = (maktx + " " + matnr).lower()
    for keyword, activity_type in MATERIAL_FUEL_KEYWORDS.items():
        if keyword in text:
            return activity_type
    return None


def _normalize_unit(sap_unit: str, quantity: Decimal) -> tuple[Optional[Decimal], Optional[str]]:
    """Convert SAP UoM code + quantity to canonical unit."""
    canonical_unit_name = SAP_UNIT_MAP.get(sap_unit.upper())
    if not canonical_unit_name:
        return None, None

    if canonical_unit_name in UNIT_CONVERSIONS:
        target_unit, factor = UNIT_CONVERSIONS[canonical_unit_name]
        return quantity * factor, target_unit

    return quantity, canonical_unit_name


class SAPFlatFileParser:
    """
    Parses SAP MM60/ME2M goods movement flat-file exports.

    The export is a tab-separated or pipe-delimited text file with:
    - An optional header block (report title, date range, org unit)
    - Column headers (possibly German)
    - Data rows
    - An optional footer with totals (which we skip)

    We skip non-consumption movement types and reversals are tracked
    so they can offset the original consumption.
    """

    def __init__(self, delimiter: str = "\t"):
        self.delimiter = delimiter
        self.rows_parsed = 0
        self.rows_skipped = 0
        self.errors = []

    def detect_delimiter(self, sample: str) -> str:
        """Auto-detect delimiter from first few lines."""
        for delim in ["\t", "|", ";", ","]:
            if delim in sample:
                return delim
        return "\t"

    def parse(self, file_content: str) -> Iterator[SAPRow]:
        """
        Parse SAP flat file content, yielding one SAPRow per data row.
        Skips header/footer blocks and non-fuel movement types.
        """
        lines = file_content.splitlines()

        # Find where the actual data starts (skip SAP report header)
        # SAP reports often start with lines like:
        #   "Material Document List"
        #   "Company Code: 1000"
        #   "Date range: 01.01.2023 - 31.12.2023"
        #   (blank line)
        #   (column headers)
        header_line_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            # Must contain a column-name keyword AND have multiple fields (tab/pipe/semicolon)
            # This avoids matching metadata lines like "Company Code: 1000"
            has_keyword = any(key in lower for key in [
                "buchungskreis", "bewegungsart", "belegnummer",
                "movement type", "document number", "posting date"
            ])
            has_delimiters = stripped.count("\t") >= 3 or stripped.count("|") >= 3 or stripped.count(";") >= 3
            if has_keyword and has_delimiters:
                header_line_idx = i
                break

        if header_line_idx is None:
            raise SAPParseError("Could not find column headers in SAP export. Check file format.")

        sample = lines[header_line_idx]
        delimiter = self.detect_delimiter(sample)

        reader = csv.DictReader(
            io.StringIO("\n".join(lines[header_line_idx:])),
            delimiter=delimiter
        )

        # Force fieldnames to be read by peeking — csv.DictReader populates
        # .fieldnames lazily on first iteration; accessing before iterating gives None.
        _ = reader.fieldnames  # triggers the header read
        normalized_fieldnames = [_normalize_header(f) for f in (reader.fieldnames or [])]

        for row_number, raw_row in enumerate(reader, start=header_line_idx + 2):
            # Skip footer / totals lines (SAP often puts "Total:" at end)
            row_values = list(raw_row.values())
            if any("total" in str(v).lower() or "summe" in str(v).lower() for v in row_values[:3]):
                self.rows_skipped += 1
                continue

            # Re-map with normalized headers
            # raw_row is already a dict with original headers; re-map to normalized names
            row = {_normalize_header(k): v for k, v in raw_row.items() if k is not None}

            parse_errors = []

            # Parse date
            budat_raw = row.get("budat", "").strip()
            budat = _parse_sap_date(budat_raw)
            if budat_raw and not budat:
                parse_errors.append(f"Could not parse date: '{budat_raw}'")

            # Parse quantity
            menge_raw = row.get("menge", "").strip()
            menge = _parse_sap_quantity(menge_raw)
            if menge_raw and menge is None:
                parse_errors.append(f"Could not parse quantity: '{menge_raw}'")

            # Parse amount
            dmbtr_raw = row.get("dmbtr", "").strip()
            dmbtr = _parse_sap_quantity(dmbtr_raw)

            # Classify movement type
            bwart = row.get("bwart", "").strip()
            is_consumption = bwart in CONSUMPTION_MOVEMENT_TYPES
            is_reversal = bwart in REVERSAL_MOVEMENT_TYPES

            maktx = row.get("maktx", "").strip()
            matnr = row.get("matnr", "").strip()
            meins = row.get("meins", "").strip()

            # Normalize units
            canonical_quantity = None
            canonical_unit = None
            if menge is not None and meins:
                canonical_quantity, canonical_unit = _normalize_unit(meins, menge)
                if canonical_quantity is None:
                    parse_errors.append(f"Unknown SAP unit code: '{meins}'")

            sap_row = SAPRow(
                belnr=row.get("belnr", "").strip(),
                bukrs=row.get("bukrs", "").strip(),
                werks=row.get("werks", "").strip(),
                matnr=matnr,
                maktx=maktx,
                budat=budat,
                budat_raw=budat_raw,
                menge=menge,
                menge_raw=menge_raw,
                meins=meins,
                dmbtr=dmbtr,
                waers=row.get("waers", "").strip(),
                bwart=bwart,
                kostl=row.get("kostl", "").strip(),
                sakto=row.get("sakto", "").strip(),
                row_number=row_number,
                parse_errors=parse_errors,
                canonical_quantity=canonical_quantity,
                canonical_unit=canonical_unit,
                activity_type=_infer_activity_type(maktx, matnr),
                is_consumption=is_consumption,
                is_reversal=is_reversal,
            )

            self.rows_parsed += 1
            yield sap_row
