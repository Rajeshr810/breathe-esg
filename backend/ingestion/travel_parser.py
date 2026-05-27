"""
Corporate Travel Ingestion: Concur SAE / Navan export format.

WHY FILE UPLOAD (not API):
Concur exposes a "Standard Accounting Extract" (SAE) — a fixed-width or
delimited file that finance teams already receive for accounting. It contains
every expense transaction including travel.

Navan (formerly TripActions) has a REST API but requires OAuth setup per
client. For a prototype with a new enterprise client, we can't assume API
credentials are available on day one. The Navan data export is available as
CSV from the admin portal.

We use the CSV export format because:
- Both Concur and Navan support it
- Finance teams already have access to pull it
- It doesn't require IT involvement to set up OAuth / API keys
- We can move to API polling later once credentials are provisioned

CONCUR SAE FORMAT (what we actually looked at):
- Fixed-width .txt OR tab-delimited .csv
- Transaction key, employee ID, expense type, date, amount, currency
- For travel specifically: IATA codes for air, hotel name/country for lodging
- Expense types use Concur's internal codes (AIRFR, HOTEL, CARRT, TAXGD, etc.)
- Report key groups expenses into one trip submission
- Employee IDs are present — we hash these immediately (PII)

EMISSION FACTOR APPROACH:
For air travel, BEIS/DEFRA methodology uses:
  kgCO2e = distance_km × radiative_forcing_factor × cabin_class_factor × passengers
  RF factor: ~1.891 (accounts for contrail/cirrus effects at altitude)
  Cabin: Economy=1.0, Premium Economy=1.6, Business=2.9, First=4.0

Distance computation from IATA codes:
  We use the great-circle distance (haversine formula).
  DEFRA treats flights as point-to-point but adds 8% for routing inefficiency.
  Short-haul (<= 3700km): higher per-km factor; long-haul: lower per-km factor.

PII HANDLING:
  Employee IDs are hashed (SHA-256) before storage.
  Names are not stored at all — cost center is sufficient for attribution.
"""

import csv
import io
import math
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Concur expense type codes → our category
# ---------------------------------------------------------------------------

CONCUR_TYPE_MAP = {
    # Air
    "AIRFR": "air",
    "AIRRFL": "air",          # air refund
    "01": "air",              # some configs use numeric codes
    # Hotel
    "HOTEL": "hotel",
    "HOTLNG": "hotel",
    "02": "hotel",
    # Car rental
    "CARRT": "car_rental",
    "03": "car_rental",
    # Taxi / rideshare
    "TAXGD": "taxi",
    "UBER": "taxi",
    "LYFT": "taxi",
    "04": "taxi",
    # Rail
    "TRAIN": "rail",
    "RAILX": "rail",
    "05": "rail",
    # Personal vehicle mileage
    "MILEAG": "mileage",
    "MILEAGE": "mileage",
    "06": "mileage",
}

# Navan category names (they use plain English)
NAVAN_TYPE_MAP = {
    "flight": "air",
    "flights": "air",
    "air": "air",
    "hotel": "hotel",
    "lodging": "hotel",
    "accommodation": "hotel",
    "car rental": "car_rental",
    "rental car": "car_rental",
    "taxi": "taxi",
    "rideshare": "taxi",
    "uber": "taxi",
    "lyft": "taxi",
    "ground transportation": "taxi",
    "train": "rail",
    "rail": "rail",
    "mileage": "mileage",
    "personal vehicle": "mileage",
}

# Cabin class normalization
CABIN_CLASS_MAP = {
    # Standard names
    "economy": "economy",
    "coach": "economy",
    "y": "economy",
    "premium economy": "premium_economy",
    "premium": "premium_economy",
    "w": "premium_economy",
    "business": "business",
    "business class": "business",
    "c": "business",
    "j": "business",
    "first": "first",
    "first class": "first",
    "f": "first",
    "a": "first",
}

# Radiative forcing multiplier per cabin class (DEFRA 2023 methodology)
# Source: DEFRA/BEIS GHG Conversion Factors 2023, Annex 3
CABIN_RF_FACTORS = {
    "economy": Decimal("1.0"),
    "premium_economy": Decimal("1.6"),
    "business": Decimal("2.9"),
    "first": Decimal("4.0"),
    "unknown": Decimal("1.0"),  # default to economy
}

# IATA airport coordinates (subset of major airports for distance calculation)
# In production: use a full airport database (OpenFlights, OurAirports)
AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    # Format: IATA: (lat, lon)
    "LHR": (51.4775, -0.4614),
    "LGW": (51.1481, -0.1903),
    "LTN": (51.8747, -0.3683),
    "STN": (51.8850, 0.2350),
    "MAN": (53.3537, -2.2750),
    "EDI": (55.9500, -3.3725),
    "BHX": (52.4539, -1.7480),
    "JFK": (40.6413, -73.7781),
    "LGA": (40.7769, -73.8740),
    "EWR": (40.6895, -74.1745),
    "LAX": (33.9425, -118.4081),
    "ORD": (41.9742, -87.9073),
    "ATL": (33.6407, -84.4277),
    "DFW": (32.8998, -97.0403),
    "DEN": (39.8561, -104.6737),
    "SFO": (37.6213, -122.3790),
    "SEA": (47.4502, -122.3088),
    "MIA": (25.7959, -80.2870),
    "BOS": (42.3656, -71.0096),
    "IAD": (38.9531, -77.4565),
    "CDG": (49.0097, 2.5479),
    "AMS": (52.3086, 4.7639),
    "FRA": (50.0379, 8.5622),
    "MUC": (48.3537, 11.7750),
    "MAD": (40.4983, -3.5676),
    "BCN": (41.2971, 2.0785),
    "FCO": (41.8003, 12.2389),
    "ZRH": (47.4647, 8.5492),
    "VIE": (48.1103, 16.5697),
    "BRU": (50.9014, 4.4844),
    "DXB": (25.2532, 55.3657),
    "SIN": (1.3644, 103.9915),
    "HKG": (22.3080, 113.9185),
    "NRT": (35.7720, 140.3929),
    "PEK": (40.0799, 116.6031),
    "SYD": (-33.9461, 151.1772),
    "MEL": (-37.6690, 144.8410),
    "BOM": (19.0896, 72.8656),
    "DEL": (28.5562, 77.1000),
    "DUB": (53.4213, -6.2701),
    "CPH": (55.6180, 12.6508),
    "ARN": (59.6519, 17.9186),
    "HEL": (60.3172, 24.9633),
    "OSL": (60.1939, 11.1004),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Haversine formula: great-circle distance between two points.
    DEFRA methodology adds 8% uplift for route inefficiency.
    """
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    great_circle = R * c
    return great_circle * 1.08  # DEFRA 8% route inefficiency uplift


def get_flight_distance_km(origin_iata: str, dest_iata: str) -> tuple[Optional[float], bool]:
    """
    Returns (distance_km, was_estimated).
    was_estimated=True if we computed from coordinates vs. received directly.
    Returns (None, False) if either airport is unknown.
    """
    origin_coords = AIRPORT_COORDS.get(origin_iata.upper())
    dest_coords = AIRPORT_COORDS.get(dest_iata.upper())
    if not origin_coords or not dest_coords:
        return None, False
    dist = _haversine_km(*origin_coords, *dest_coords)
    return dist, True


def _hash_employee_id(raw_id: str) -> str:
    """One-way hash of employee ID. We never store PII in clear text."""
    return hashlib.sha256(raw_id.encode()).hexdigest()[:16]


def _classify_trip_category(raw: str) -> Optional[str]:
    """Map raw expense type to our category."""
    clean = raw.strip().upper()
    result = CONCUR_TYPE_MAP.get(clean)
    if result:
        return result
    result = NAVAN_TYPE_MAP.get(raw.strip().lower())
    return result


def _normalize_cabin_class(raw: str) -> str:
    clean = raw.strip().lower()
    return CABIN_CLASS_MAP.get(clean, "unknown")


def _parse_travel_date(raw: str) -> Optional[date]:
    raw = raw.strip()
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"]:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


TRAVEL_COLUMN_ALIASES = {
    # Expense / trip ID
    "expense key": "expense_key",
    "report key": "expense_key",
    "trip id": "expense_key",
    "transaction id": "expense_key",
    "booking reference": "expense_key",

    # Employee
    "employee id": "employee_id",
    "emp id": "employee_id",
    "user id": "employee_id",
    "traveler id": "employee_id",

    # Cost center
    "cost center": "cost_center",
    "cost centre": "cost_center",
    "department": "cost_center",

    # Category / expense type
    "expense type": "category",
    "expense type code": "category",
    "category": "category",
    "trip type": "category",
    "travel type": "category",
    "service type": "category",

    # Date
    "transaction date": "travel_date",
    "travel date": "travel_date",
    "date": "travel_date",
    "departure date": "travel_date",
    "check-in date": "travel_date",
    "check in date": "travel_date",

    # Air
    "origin": "origin_iata",
    "from": "origin_iata",
    "departure": "origin_iata",
    "destination": "destination_iata",
    "to": "destination_iata",
    "arrival": "destination_iata",
    "cabin class": "cabin_class",
    "class of service": "cabin_class",
    "fare class": "cabin_class",

    # Distance
    "distance": "distance",
    "distance (km)": "distance",
    "distance (miles)": "distance",
    "miles": "distance",

    # Hotel
    "hotel": "hotel_name",
    "property": "hotel_name",
    "hotel name": "hotel_name",
    "nights": "hotel_nights",
    "number of nights": "hotel_nights",
    "country": "hotel_country",

    # Spend
    "amount": "amount",
    "total amount": "amount",
    "transaction amount": "amount",
    "currency": "currency",
}


@dataclass
class TravelRow:
    expense_key: str
    employee_id_hashed: str
    cost_center: str
    category_raw: str
    category: Optional[str]
    travel_date: Optional[date]
    travel_date_raw: str

    # Air
    origin_iata: str
    destination_iata: str
    cabin_class_raw: str
    cabin_class: str
    distance_km: Optional[float]
    distance_estimated: bool

    # Hotel
    hotel_name: str
    hotel_country: str
    hotel_nights: Optional[int]

    # Ground
    ground_distance_raw: str
    ground_distance_unit: str

    # Spend
    amount_raw: str
    currency_raw: str

    row_number: int
    parse_errors: list = field(default_factory=list)


class TravelCSVParser:
    """
    Parses Concur SAE / Navan CSV travel exports.
    Handles both air/hotel/ground in the same file.
    Computes flight distances from IATA codes when not provided.
    Immediately hashes employee IDs.
    """

    def __init__(self):
        self.rows_parsed = 0

    def parse(self, file_content: str) -> Iterator[TravelRow]:
        file_content = file_content.lstrip("\ufeff")
        lines = file_content.splitlines()

        # Skip metadata header lines
        data_start = 0
        for i, line in enumerate(lines):
            parts = re.split(r'[,\t|;]', line)
            if len(parts) >= 3 and any(
                any(alias in p.strip().lower() for alias in ["expense", "date", "category", "amount", "employee"])
                for p in parts
            ):
                data_start = i
                break

        content = "\n".join(lines[data_start:])
        sample_line = lines[data_start] if data_start < len(lines) else ""
        delimiter = ","
        for d in [",", "\t", ";", "|"]:
            if d in sample_line:
                delimiter = d
                break

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        original_fields = reader.fieldnames or []
        field_map = {f: TRAVEL_COLUMN_ALIASES.get(f.strip().lower(), f.strip().lower().replace(" ", "_")) for f in original_fields}

        for row_number, raw_row in enumerate(reader, start=data_start + 2):
            row = {field_map.get(k, k): v for k, v in raw_row.items() if k is not None}
            parse_errors = []

            if not any(v.strip() for v in row.values() if isinstance(v, str)):
                continue

            # Hash employee ID immediately
            raw_emp_id = row.get("employee_id", "").strip()
            employee_id_hashed = _hash_employee_id(raw_emp_id) if raw_emp_id else ""

            travel_date_raw = row.get("travel_date", "").strip()
            travel_date = _parse_travel_date(travel_date_raw)
            if travel_date_raw and not travel_date:
                parse_errors.append(f"Could not parse date: '{travel_date_raw}'")

            category_raw = row.get("category", "").strip()
            category = _classify_trip_category(category_raw)
            if category_raw and not category:
                parse_errors.append(f"Unknown expense category: '{category_raw}'")

            origin_iata = row.get("origin_iata", "").strip().upper()[:3]
            dest_iata = row.get("destination_iata", "").strip().upper()[:3]
            cabin_class_raw = row.get("cabin_class", "").strip()
            cabin_class = _normalize_cabin_class(cabin_class_raw)

            # Compute distance if not provided
            distance_raw = row.get("distance", "").strip()
            distance_km = None
            distance_estimated = False

            if distance_raw:
                try:
                    raw_val = float(distance_raw.replace(",", ""))
                    # Detect miles vs km from column header
                    unit_in_header = any("mile" in f.lower() for f in original_fields if "distance" in f.lower())
                    distance_km = raw_val * 1.60934 if unit_in_header else raw_val
                except ValueError:
                    parse_errors.append(f"Could not parse distance: '{distance_raw}'")

            if distance_km is None and origin_iata and dest_iata and category == "air":
                computed, estimated = get_flight_distance_km(origin_iata, dest_iata)
                if computed:
                    distance_km = computed
                    distance_estimated = True
                else:
                    parse_errors.append(f"Unknown IATA codes for distance: {origin_iata} → {dest_iata}")

            # Hotel nights
            hotel_nights = None
            nights_raw = row.get("hotel_nights", "").strip()
            if nights_raw:
                try:
                    hotel_nights = int(float(nights_raw))
                except ValueError:
                    parse_errors.append(f"Could not parse hotel nights: '{nights_raw}'")

            yield TravelRow(
                expense_key=row.get("expense_key", "").strip(),
                employee_id_hashed=employee_id_hashed,
                cost_center=row.get("cost_center", "").strip(),
                category_raw=category_raw,
                category=category,
                travel_date=travel_date,
                travel_date_raw=travel_date_raw,
                origin_iata=origin_iata,
                destination_iata=dest_iata,
                cabin_class_raw=cabin_class_raw,
                cabin_class=cabin_class,
                distance_km=distance_km,
                distance_estimated=distance_estimated,
                hotel_name=row.get("hotel_name", "").strip(),
                hotel_country=row.get("hotel_country", "").strip().upper()[:2],
                hotel_nights=hotel_nights,
                ground_distance_raw=row.get("ground_distance", "").strip(),
                ground_distance_unit=row.get("ground_distance_unit", "").strip(),
                amount_raw=row.get("amount", "").strip(),
                currency_raw=row.get("currency", "").strip(),
                row_number=row_number,
                parse_errors=parse_errors,
            )
            self.rows_parsed += 1
