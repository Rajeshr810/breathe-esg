# MODEL.md — Data Model Design

## Overview

The schema is built around one central question: **given any row in `EmissionsRecord`, can an auditor reconstruct exactly how it got there, from what source, with what data, by whom, and when?** If the answer is ever "no," the model is wrong.

---

## Entity Map

```
Organization
  └── OrganizationMembership (→ User, with Role)
  └── FacilityLocation (plant codes, meter IDs, resolved to physical sites)
  └── IngestionJob (one per file upload / API pull)
      └── RawSAPRecord     ──┐
      └── RawUtilityRecord ──┼──→ EmissionsRecord (1:1 OneToOne)
      └── RawTravelRecord  ──┘
  └── EmissionsRecord
      └── AuditLog (append-only, never deleted)

EmissionFactor (global reference table, not org-scoped)
```

---

## Design Decisions

### 1. Two-stage ingestion (Raw → Normalized)

Every record goes through two tables:
1. **Raw staging** (`RawSAPRecord`, `RawUtilityRecord`, `RawTravelRecord`): exact values as received, before any transformation. German column headers, SAP unit codes, unparsed dates — all preserved.
2. **Normalized** (`EmissionsRecord`): canonical units, GHG scope assigned, CO2e computed.

**Why?** When an auditor asks "what exactly did the SAP file say?", we can show them. When a client says "our diesel quantity should be in liters not gallons," we can reprocess without losing the original. The raw tables are also what we show in the error log — `row_number` maps back to the source file.

### 2. Multi-tenancy via hard FK, not row-level security

Every `EmissionsRecord`, `IngestionJob`, and `FacilityLocation` has a required `organization` FK. The ORM layer always filters by `organization = request.user.org`. We do **not** use Postgres Row Level Security here — it adds complexity without meaningful additional protection when the application layer is consistently enforced.

What we *would* add before production: a `OrganizationQuerySet` mixin that overrides `get_queryset()` on every ViewSet and fails loudly if the filter is absent.

### 3. GHG Protocol Scope classification

Scope is assigned at the `EmissionsRecord` level, not the source level:

| Source | Typical Scope | Rationale |
|--------|--------------|-----------|
| SAP fuel (movement type 261) | Scope 1 | Direct combustion in owned/controlled equipment |
| SAP electricity procurement | Scope 2 | Purchased energy (if SAP tracks utility invoices) |
| Utility meter reads | Scope 2 | Purchased electricity, location-based |
| Air travel | Scope 3, Cat 6 | Business travel, GHG Protocol Cat 6 |
| Hotel stays | Scope 3, Cat 6 | Business travel, same category |
| Ground transport | Scope 3, Cat 6 | Business travel |

**What we don't handle:** Scope 2 market-based (requires RECs/PPA tracking — the `is_renewable` flag is preserved but market-based factors aren't implemented). This is noted in TRADEOFFS.md.

### 4. Unit normalization strategy

Raw units are **always preserved** in `unit_raw`. Normalized units per category:

| Category | Canonical Unit | Why |
|----------|---------------|-----|
| Liquid fuel | liters | SAP may give kg or gallons; liters is DEFRA default |
| Electricity | kWh | Universal; MWh converted 1:1 |
| Air distance | passenger_km | DEFRA per-passenger methodology |
| Hotel | room_nights | DEFRA per-room-night factor |
| Ground transport | km | Standard distance unit |

The `quantity_normalized` field always holds the value in the canonical unit. `co2e_kg` is then `quantity_normalized × emission_factor.factor_kgco2e`.

### 5. Audit trail design

`AuditLog` is **append-only**. The application never updates or deletes rows here. It contains:
- `action` (created, approved, edited, locked, etc.)
- `performed_by` (user FK)
- `timestamp` (auto, UTC)
- `snapshot` (JSON: key field values at the moment of the action)

This means you can reconstruct the full history of any record — including what the CO2e value was before an edit.

**Post-lock corrections:** Once a record is `LOCKED`, it cannot be edited. A correction creates a **new** `EmissionsRecord` and sets `superseded_by` on the original to point to the replacement. Both records remain in the database; auditors can see what was originally submitted and what replaced it.

### 6. The `flags` JSONField

Flags are stored as a list of `{code, severity, message}` dicts on `EmissionsRecord`. 

Alternatives considered:
- **Separate `RecordFlag` table**: better for querying "all records with flag X," but adds a join on every list query. For a prototype, JSONField is fine; in production we'd add a `flag_codes` ArrayField for indexed querying.
- **Enum-only flags (no message)**: less useful for analysts — the message tells them *why* the flag was raised (e.g., "SAP unit code 'NM3' could not be normalized" vs. just "UNIT_UNUSUAL").

Severity levels:
- `error`: CO2e could not be computed — record needs manual review before approval
- `warning`: CO2e was computed but something looks suspicious
- `info`: informational context (e.g., distance was estimated from IATA codes)

### 7. EmissionFactor versioning

Factors are versioned by `valid_from_year` / `valid_to_year`. A null `valid_to_year` means currently active. The lookup always uses the factor that was valid in the year of the activity, not the year of ingestion. This means historical submissions can be reproduced even if factors have since been updated.

**Factor sources used:**
- Fuel combustion: DEFRA/BEIS GHG Conversion Factors 2023
- Electricity (UK): DEFRA 2023 grid intensity (0.20707 kgCO2e/kWh)
- Air travel: DEFRA 2023 per-passenger km with radiative forcing (RF = 1.891)
- Hotels: DEFRA 2023 per room-night (25.0 kgCO2e)
- Ground transport: DEFRA 2023 per km by mode

### 8. FacilityLocation as a resolution layer

SAP plant codes (`werks`) are meaningless strings — `'1000'` could be a warehouse in Hamburg or a refinery in Aberdeen. `FacilityLocation` maps source system codes to:
- Human-readable name
- Country code (for region-specific emission factors)
- Grid region (for electricity, which has country-level factors)

This table is populated during client onboarding. Records that can't be resolved get a `MISSING_FACILITY` flag but are still ingested — we don't block on missing lookups.

### 9. PII handling in travel records

Concur/Navan exports contain employee IDs. We:
1. Hash immediately on parse (SHA-256, truncated to 16 hex chars)
2. Never store names
3. Retain cost center for attribution

This is sufficient for emissions reporting purposes — we care about totals by cost center, not by person.

---

## What would break at production scale

1. **JSONField `flags` isn't indexed** — add a `flag_codes` `ArrayField(CharField)` for querying
2. **Synchronous job processing** — replace with Celery + Redis for large files
3. **Single-org per user** — currently assumes one org membership; production needs org switching
4. **No soft-delete** — records and jobs can be hard-deleted; add `deleted_at` for GDPR right-to-erasure compliance
5. **EmissionFactor table needs a seeding job** — currently populated via fixture; should be updated from DEFRA API annually
