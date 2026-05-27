"""
Management command: seed_data
Creates demo organization, users, emission factors, facility lookups,
and ingests the three sample files so the deployed app has real data.

Usage: python manage.py seed_data
"""

import os
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from core.models import (
    Organization, OrganizationMembership,
    EmissionFactor, FacilityLocation, IngestionJob
)


EMISSION_FACTORS = [
    # ── Scope 1: Stationary combustion (DEFRA 2023) ──────────────────────────
    # Source: DEFRA/BEIS GHG Conversion Factors 2023
    # https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2023
    dict(activity_type="diesel", category="stationary_combustion", scope="scope_1",
         factor_kgco2e=Decimal("2.6391"), unit_of_activity="liters",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    dict(activity_type="petrol", category="mobile_combustion", scope="scope_1",
         factor_kgco2e=Decimal("2.3120"), unit_of_activity="liters",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    dict(activity_type="natural_gas", category="stationary_combustion", scope="scope_1",
         factor_kgco2e=Decimal("2.0423"), unit_of_activity="cubic_meters",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    dict(activity_type="lpg", category="stationary_combustion", scope="scope_1",
         factor_kgco2e=Decimal("1.5554"), unit_of_activity="kg",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    dict(activity_type="heating_oil", category="stationary_combustion", scope="scope_1",
         factor_kgco2e=Decimal("2.5203"), unit_of_activity="liters",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    dict(activity_type="heavy_fuel_oil", category="stationary_combustion", scope="scope_1",
         factor_kgco2e=Decimal("3.1818"), unit_of_activity="liters",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    # ── Scope 2: Purchased electricity ───────────────────────────────────────
    # UK grid: DEFRA 2023 location-based (0.20707 kgCO2e/kWh)
    dict(activity_type="electricity", category="purchased_electricity", scope="scope_2",
         factor_kgco2e=Decimal("0.20707"), unit_of_activity="kwh",
         source="DEFRA 2023", valid_from_year=2023, region="GB"),

    # US average grid: EPA eGRID 2022 national average
    dict(activity_type="electricity", category="purchased_electricity", scope="scope_2",
         factor_kgco2e=Decimal("0.38600"), unit_of_activity="kwh",
         source="EPA eGRID 2022", valid_from_year=2022, region="US"),

    # US NPCC (Northeast) grid — lower than national average (hydro mix)
    dict(activity_type="electricity", category="purchased_electricity", scope="scope_2",
         factor_kgco2e=Decimal("0.22400"), unit_of_activity="kwh",
         source="EPA eGRID 2022", valid_from_year=2022, region="US-NPCC"),

    # Germany grid: UBA 2023
    dict(activity_type="electricity", category="purchased_electricity", scope="scope_2",
         factor_kgco2e=Decimal("0.38500"), unit_of_activity="kwh",
         source="UBA 2023", valid_from_year=2023, region="DE"),

    # Netherlands grid: CBS 2023
    dict(activity_type="electricity", category="purchased_electricity", scope="scope_2",
         factor_kgco2e=Decimal("0.32800"), unit_of_activity="kwh",
         source="CBS Netherlands 2023", valid_from_year=2023, region="NL"),

    # ── Scope 3: Business travel — air ────────────────────────────────────────
    # DEFRA 2023 per passenger-km (before cabin class and RF multiplier)
    # These are base factors; cabin class and RF are applied in the normalizer
    dict(activity_type="air", category="business_travel_air", scope="scope_3",
         factor_kgco2e=Decimal("0.25510"), unit_of_activity="passenger_km",
         source="DEFRA 2023 short-haul economy with RF", valid_from_year=2023, region=""),

    # ── Scope 3: Business travel — hotels ─────────────────────────────────────
    # DEFRA 2023 per room-night global average
    dict(activity_type="hotel", category="business_travel_hotel", scope="scope_3",
         factor_kgco2e=Decimal("25.00"), unit_of_activity="room_nights",
         source="DEFRA 2023", valid_from_year=2023, region=""),

    # ── Scope 3: Business travel — ground transport ───────────────────────────
    dict(activity_type="car_rental", category="business_travel_ground", scope="scope_3",
         factor_kgco2e=Decimal("0.21000"), unit_of_activity="km",
         source="DEFRA 2023 average car", valid_from_year=2023, region=""),

    dict(activity_type="taxi", category="business_travel_ground", scope="scope_3",
         factor_kgco2e=Decimal("0.21000"), unit_of_activity="km",
         source="DEFRA 2023 average taxi", valid_from_year=2023, region=""),

    dict(activity_type="rail", category="business_travel_ground", scope="scope_3",
         factor_kgco2e=Decimal("0.03500"), unit_of_activity="km",
         source="DEFRA 2023 national rail", valid_from_year=2023, region="GB"),

    dict(activity_type="mileage", category="business_travel_ground", scope="scope_3",
         factor_kgco2e=Decimal("0.21000"), unit_of_activity="km",
         source="DEFRA 2023 average car", valid_from_year=2023, region=""),
]


FACILITIES = [
    # SAP plant codes → physical facilities
    dict(source_code="1000", source_system="sap", display_name="Hamburg HQ",
         country_code="DE", region="Hamburg", grid_region="DE"),
    dict(source_code="1010", source_system="sap", display_name="Munich Plant",
         country_code="DE", region="Bavaria", grid_region="DE"),
    dict(source_code="2000", source_system="sap", display_name="UK Subsidiary — Oxford",
         country_code="GB", region="Oxfordshire", grid_region="GB"),

    # Utility meter IDs → facilities
    dict(source_code="MPAN-0012345678901", source_system="utility_portal",
         display_name="Harwell Campus — Unit 4 (Main)",
         country_code="GB", region="Oxfordshire", grid_region="GB"),
    dict(source_code="MPAN-0098765432101", source_system="utility_portal",
         display_name="Harwell Campus — Building 6 (Annex)",
         country_code="GB", region="Oxfordshire", grid_region="GB"),
    dict(source_code="METER-US-MFG-01", source_system="utility_portal",
         display_name="Newark NJ Manufacturing",
         country_code="US", region="New Jersey", grid_region="US-NPCC"),
    dict(source_code="METER-SOLAR-01", source_system="utility_portal",
         display_name="Harwell Campus — Rooftop Solar (Export)",
         country_code="GB", region="Oxfordshire", grid_region="GB"),
]


class Command(BaseCommand):
    help = "Seed demo organization, users, emission factors, and sample data"

    def handle(self, *args, **options):
        self.stdout.write("Seeding Breathe ESG demo data...")

        with transaction.atomic():
            # ── Emission factors ──────────────────────────────────────────────
            ef_count = 0
            for ef_data in EMISSION_FACTORS:
                _, created = EmissionFactor.objects.get_or_create(
                    activity_type=ef_data["activity_type"],
                    valid_from_year=ef_data["valid_from_year"],
                    region=ef_data.get("region", ""),
                    defaults=ef_data,
                )
                if created:
                    ef_count += 1
            self.stdout.write(f"  ✓ {ef_count} emission factors created")

            # ── Demo organization ─────────────────────────────────────────────
            org, created = Organization.objects.get_or_create(
                slug="acme-manufacturing",
                defaults={"name": "ACME Manufacturing GmbH", "fiscal_year_start_month": 1},
            )
            if created:
                self.stdout.write("  ✓ Organization: ACME Manufacturing GmbH")

            # ── Facility lookups ──────────────────────────────────────────────
            fac_count = 0
            for fac_data in FACILITIES:
                _, created = FacilityLocation.objects.get_or_create(
                    organization=org,
                    source_code=fac_data["source_code"],
                    source_system=fac_data["source_system"],
                    defaults=fac_data,
                )
                if created:
                    fac_count += 1
            self.stdout.write(f"  ✓ {fac_count} facility mappings created")

            # ── Users ─────────────────────────────────────────────────────────
            users_created = []

            admin_user, created = User.objects.get_or_create(
                username="admin",
                defaults={"email": "admin@breathe-esg.demo", "is_staff": True, "is_superuser": True},
            )
            if created:
                admin_user.set_password("breathe-admin-2024")
                admin_user.save()
                users_created.append("admin")

            analyst_user, created = User.objects.get_or_create(
                username="analyst",
                defaults={"email": "analyst@breathe-esg.demo"},
            )
            if created:
                analyst_user.set_password("breathe-analyst-2024")
                analyst_user.save()
                users_created.append("analyst")

            auditor_user, created = User.objects.get_or_create(
                username="auditor",
                defaults={"email": "auditor@breathe-esg.demo"},
            )
            if created:
                auditor_user.set_password("breathe-auditor-2024")
                auditor_user.save()
                users_created.append("auditor")

            if users_created:
                self.stdout.write(f"  ✓ Users created: {', '.join(users_created)}")

            # ── Memberships ───────────────────────────────────────────────────
            OrganizationMembership.objects.get_or_create(
                user=admin_user, organization=org,
                defaults={"role": OrganizationMembership.Role.ADMIN}
            )
            OrganizationMembership.objects.get_or_create(
                user=analyst_user, organization=org,
                defaults={"role": OrganizationMembership.Role.ANALYST}
            )
            OrganizationMembership.objects.get_or_create(
                user=auditor_user, organization=org,
                defaults={"role": OrganizationMembership.Role.AUDITOR}
            )

            # ── Auth tokens ───────────────────────────────────────────────────
            from rest_framework.authtoken.models import Token
            for user in [admin_user, analyst_user, auditor_user]:
                Token.objects.get_or_create(user=user)

            analyst_token = Token.objects.get(user=analyst_user).key
            self.stdout.write(f"\n  Analyst token (for API testing): {analyst_token}")

            # ── Ingest sample files ───────────────────────────────────────────
            self._ingest_sample_files(org, analyst_user)

        self.stdout.write(self.style.SUCCESS("\n✓ Seed complete. Login credentials:"))
        self.stdout.write("  admin / breathe-admin-2024")
        self.stdout.write("  analyst / breathe-analyst-2024")
        self.stdout.write("  auditor / breathe-auditor-2024")

    def _ingest_sample_files(self, org, user):
        """Ingest the three sample CSV/TXT files if they exist."""
        from ingestion.tasks import process_ingestion_job

        # Path: backend/core/management/commands/ -> backend/ -> project root -> sample_data
        base = os.path.join(
            os.path.dirname(  # commands/
                os.path.dirname(  # management/
                    os.path.dirname(  # core/
                        os.path.dirname(  # backend/
                            os.path.dirname(  # project root
                                os.path.abspath(__file__)
                            )
                        )
                    )
                )
            ),
            "sample_data"
        )

        sample_files = [
            ("sap", "sap_mm60_export_2023_H1.txt"),
            ("utility", "utility_portal_export_2023.csv"),
            ("travel", "travel_concur_navan_2023_H1.csv"),
        ]

        for source_type, filename in sample_files:
            filepath = os.path.join(base, filename)
            if not os.path.exists(filepath):
                self.stdout.write(f"  ⚠ Sample file not found: {filepath}")
                continue

            if IngestionJob.objects.filter(organization=org, source_reference=filename).exists():
                self.stdout.write(f"  – Already ingested: {filename}")
                continue

            from django.core.files import File
            with open(filepath, "rb") as f:
                job = IngestionJob.objects.create(
                    organization=org,
                    source_type=source_type,
                    source_reference=filename,
                    created_by=user,
                )
                job.raw_file.save(filename, File(f), save=True)

            self.stdout.write(f"  ↑ Ingesting {filename}...")
            process_ingestion_job(job.id)
            job.refresh_from_db()
            self.stdout.write(
                f"    → {job.status}: {job.rows_success}/{job.rows_total} rows OK, "
                f"{job.rows_failed} failed, {job.rows_flagged} flagged"
            )
