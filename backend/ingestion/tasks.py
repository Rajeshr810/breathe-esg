"""
Ingestion task: reads a raw file, runs it through the appropriate parser,
stages raw records, then normalizes to EmissionsRecord.

Called synchronously in the prototype; designed to be wrapped in a Celery
task for production (just call process_ingestion_job.delay(job_id)).
"""

import logging
from django.db import transaction
from django.utils import timezone

from core.models import (
    IngestionJob, RawSAPRecord, RawUtilityRecord, RawTravelRecord,
    EmissionsRecord, AuditLog
)
from ingestion.sap_parser import SAPFlatFileParser
from ingestion.utility_parser import UtilityCSVParser
from ingestion.travel_parser import TravelCSVParser
from ingestion.normalizer import normalize_sap_row, normalize_utility_row, normalize_travel_row

logger = logging.getLogger(__name__)


def process_ingestion_job(job_id: str) -> None:
    """
    Main entry point. Reads the uploaded file, routes to the right parser,
    creates raw staging records, then normalizes each to an EmissionsRecord.
    Updates IngestionJob counts and status throughout.
    """
    try:
        job = IngestionJob.objects.select_related("organization", "created_by").get(id=job_id)
    except IngestionJob.DoesNotExist:
        logger.error(f"Job {job_id} not found")
        return

    job.status = IngestionJob.Status.PROCESSING
    job.save(update_fields=["status"])

    logger.info(f"Processing job {job_id}: {job.source_type} for {job.organization.slug}")

    try:
        if not job.raw_file:
            raise ValueError("No file attached to job")

        # Read file as binary then decode carefully.
        # Always open as "rb" — Django storage may return bytes either way,
        # and opening as "r" on Windows line-ending files can corrupt splitlines().
        job.raw_file.open("rb")
        try:
            raw_bytes = job.raw_file.read()
        finally:
            job.raw_file.close()

        # Try UTF-8 with BOM first (utf-8-sig strips BOM automatically),
        # then fall back to latin-1 (SAP German locale exports use this).
        try:
            content = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw_bytes.decode("latin-1")

        # Normalize line endings so splitlines() works consistently
        # regardless of Windows (CRLF), Mac (CR), or Unix (LF) origin.
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        if job.source_type == IngestionJob.SourceType.SAP:
            _process_sap(job, content)
        elif job.source_type == IngestionJob.SourceType.UTILITY:
            _process_utility(job, content)
        elif job.source_type == IngestionJob.SourceType.TRAVEL:
            _process_travel(job, content)
        else:
            raise ValueError(f"Unknown source type: {job.source_type}")

        # Mark complete vs partial
        if job.rows_failed == 0:
            job.status = IngestionJob.Status.COMPLETE
        elif job.rows_success > 0:
            job.status = IngestionJob.Status.PARTIAL
        else:
            job.status = IngestionJob.Status.FAILED

    except Exception as e:
        logger.exception(f"Job {job_id} failed with unhandled error")
        job.status = IngestionJob.Status.FAILED
        job.error_log = [{"error": str(e), "type": type(e).__name__}]

    finally:
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "rows_total", "rows_success", "rows_failed", "rows_flagged", "error_log"])
        logger.info(f"Job {job_id} done: {job.status} | {job.rows_success}/{job.rows_total} rows OK")


def _process_sap(job: IngestionJob, content: str) -> None:
    parser = SAPFlatFileParser()
    error_log = []

    for sap_row in parser.parse(content):
        job.rows_total += 1

        with transaction.atomic():
            try:
                # Stage raw record
                raw = RawSAPRecord.objects.create(
                    job=job,
                    belnr=sap_row.belnr,
                    bukrs=sap_row.bukrs,
                    werks=sap_row.werks,
                    matnr=sap_row.matnr,
                    maktx=sap_row.maktx,
                    budat_raw=sap_row.budat_raw,
                    budat=sap_row.budat,
                    menge_raw=sap_row.menge_raw,
                    meins=sap_row.meins,
                    dmbtr_raw=str(sap_row.dmbtr) if sap_row.dmbtr else "",
                    waers=sap_row.waers,
                    bwart=sap_row.bwart,
                    kostl=sap_row.kostl,
                    sakto=sap_row.sakto,
                    row_number=sap_row.row_number,
                    parse_errors=sap_row.parse_errors,
                )

                # Normalize to EmissionsRecord
                record = normalize_sap_row(raw, sap_row, job)

                if record is None:
                    # Non-consumption row — skip, don't count as failure
                    job.rows_total -= 1
                    continue

                record.save()

                AuditLog.objects.create(
                    record=record,
                    action=AuditLog.Action.CREATED,
                    performed_by=job.created_by,
                    snapshot={"co2e_kg": str(record.co2e_kg), "status": record.status},
                )

                job.rows_success += 1
                if record.flags:
                    job.rows_flagged += 1

            except Exception as e:
                job.rows_failed += 1
                error_log.append({
                    "row": sap_row.row_number,
                    "error": str(e),
                    "raw": {"belnr": sap_row.belnr, "werks": sap_row.werks, "bwart": sap_row.bwart},
                })
                logger.warning(f"SAP row {sap_row.row_number} failed: {e}")

    job.error_log = error_log


def _process_utility(job: IngestionJob, content: str) -> None:
    parser = UtilityCSVParser()
    error_log = []

    for util_row in parser.parse(content):
        job.rows_total += 1

        with transaction.atomic():
            try:
                raw = RawUtilityRecord.objects.create(
                    job=job,
                    meter_id=util_row.meter_id,
                    account_number=util_row.account_number,
                    service_address=util_row.service_address,
                    period_start_raw=util_row.period_start_raw,
                    period_end_raw=util_row.period_end_raw,
                    period_start=util_row.period_start,
                    period_end=util_row.period_end,
                    consumption_raw=util_row.consumption_raw,
                    consumption_unit_raw=util_row.consumption_unit_raw,
                    tariff_code=util_row.tariff_code,
                    supplier=util_row.supplier,
                    is_renewable_raw=util_row.is_renewable_raw,
                    row_number=util_row.row_number,
                    parse_errors=util_row.parse_errors,
                )

                record = normalize_utility_row(raw, util_row, job)
                if record is None:
                    job.rows_total -= 1
                    continue

                record.save()

                AuditLog.objects.create(
                    record=record,
                    action=AuditLog.Action.CREATED,
                    performed_by=job.created_by,
                    snapshot={"co2e_kg": str(record.co2e_kg), "status": record.status},
                )

                job.rows_success += 1
                if record.flags:
                    job.rows_flagged += 1

            except Exception as e:
                job.rows_failed += 1
                error_log.append({
                    "row": util_row.row_number,
                    "error": str(e),
                    "raw": {"meter_id": util_row.meter_id, "period_start": util_row.period_start_raw},
                })
                logger.warning(f"Utility row {util_row.row_number} failed: {e}")

    job.error_log = error_log


def _process_travel(job: IngestionJob, content: str) -> None:
    parser = TravelCSVParser()
    error_log = []

    for travel_row in parser.parse(content):
        job.rows_total += 1

        with transaction.atomic():
            try:
                raw = RawTravelRecord.objects.create(
                    job=job,
                    expense_key=travel_row.expense_key,
                    employee_id=travel_row.employee_id_hashed,  # already hashed
                    cost_center=travel_row.cost_center,
                    category_raw=travel_row.category_raw,
                    category=travel_row.category or "",
                    travel_date_raw=travel_row.travel_date_raw,
                    travel_date=travel_row.travel_date,
                    origin_iata=travel_row.origin_iata,
                    destination_iata=travel_row.destination_iata,
                    cabin_class_raw=travel_row.cabin_class_raw,
                    distance_raw=str(travel_row.distance_km or ""),
                    hotel_name=travel_row.hotel_name,
                    hotel_country=travel_row.hotel_country,
                    hotel_nights=travel_row.hotel_nights,
                    ground_distance_raw=travel_row.ground_distance_raw,
                    amount_raw=travel_row.amount_raw,
                    currency_raw=travel_row.currency_raw,
                    row_number=travel_row.row_number,
                    parse_errors=travel_row.parse_errors,
                )

                record = normalize_travel_row(raw, travel_row, job)
                if record is None:
                    job.rows_total -= 1
                    continue

                record.save()

                AuditLog.objects.create(
                    record=record,
                    action=AuditLog.Action.CREATED,
                    performed_by=job.created_by,
                    snapshot={"co2e_kg": str(record.co2e_kg), "status": record.status},
                )

                job.rows_success += 1
                if record.flags:
                    job.rows_flagged += 1

            except Exception as e:
                job.rows_failed += 1
                error_log.append({
                    "row": travel_row.row_number,
                    "error": str(e),
                    "raw": {"expense_key": travel_row.expense_key, "category": travel_row.category_raw},
                })
                logger.warning(f"Travel row {travel_row.row_number} failed: {e}")

    job.error_log = error_log
