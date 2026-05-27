# DECISIONS.md — Ambiguities Resolved

Every design choice that wasn't obvious, what I picked, why, and what I'd ask the PM.

---

## SAP: Which export format?

**Options researched:** IDoc (EDI), OData (S/4HANA Fiori), BAPI/RFC call, flat-file report (SM36 background job).

**Chose:** Flat-file MM60/ME2M goods movement report (tab/pipe delimited).

**Why:**
- IDocs require SAP Basis configuration (ALE/EDI setup) that most sustainability teams don't have and IT won't prioritize for a new ESG vendor
- OData assumes S/4HANA; the client may be on ECC 6.0 which predates that
- BAPI calls require RFC connectivity and a technical user — security team approval takes weeks
- Flat-file reports run as scheduled background jobs (SM36) and SFTP to an agreed location — this is what the client's SAP team will actually agree to on short notice

**Subset handled:**
- Movement type 261 (goods issue to cost center): primary consumption event
- Movement types 201, 221 (goods issue to order/project): secondary consumption
- Reversals (262, 202, 222): tracked and negated against original

**Explicitly ignored:**
- Purchase orders (ME2M): procurement data is valuable for Scope 3 Cat 1, but classifying purchased materials to emission categories requires a material master lookup table the client hasn't provided
- Production orders (PP module): out of scope for fuel/electricity tracking
- Asset-level fuel tracking (PM module): requires plant maintenance configuration

**What I'd ask the PM:**
- Is this client on SAP ECC or S/4HANA? S/4HANA opens OData.
- Does their SAP team run scheduled background jobs, or do they want to export manually?
- Do they have a material→fuel-type mapping table, or do we need to infer from descriptions?

---

## Utility: Which ingestion mechanism?

**Options researched:** Green Button XML (ESPI), portal CSV export, PDF bill parsing, EDI 867 interval data.

**Chose:** Portal CSV export.

**Why:**
- Green Button is the right answer technically (US ESPI standard), but adoption is patchy. Pacific Gas & Electric supports it; many UK and EU utilities don't. We can't assume it.
- PDF bills require OCR — unreliable, fragile, and a rabbit hole for a 4-day prototype. In production, a third party (UtilityAPI, Arcadia) would handle this.
- EDI 867 is interval (15-min or hourly) data for large commercial accounts. Valuable for granular analysis but requires the utility to set up an EDI trading partner relationship — that's a 3-month procurement process.
- Portal CSV is available to *every* facilities manager right now, today, without involving IT or the utility's commercial team. It's what they already download manually.

**Subset handled:**
- Monthly billing summary (one row per meter per billing period)
- Half-hourly interval data (detected automatically by row count / period duration heuristic)
- Units: kWh, MWh, GWh (normalized to kWh)
- Net metering: negative consumption preserved, flagged for review

**Explicitly ignored:**
- Demand charges (kW, not kWh) — relevant for cost but not emissions
- Power factor / reactive energy (kVAh, kVARh) — not included in consumption calculation
- Time-of-use tariff breakdown — we sum to total consumption
- Multi-fuel exports (gas + electricity in same file) — treated as electricity; gas rows flagged

**What I'd ask the PM:**
- Are any of the client's facilities in the US? (Green Button support worth checking)
- Are they on a renewable tariff or PPA? (market-based Scope 2 calculation changes significantly)
- Do they have interval data available, or just monthly bills?

---

## Travel: Concur SAE vs. Navan API vs. manual CSV?

**Options researched:** Concur Standard Accounting Extract (SAE), Navan REST API, Amex GBT export, manual expense spreadsheet.

**Chose:** CSV export compatible with both Concur SAE and Navan admin export.

**Why:**
- Concur API requires OAuth per client — we can't provision credentials in a prototype. Concur's API documentation also requires a "partner application" registration that takes weeks.
- Navan's API is cleaner, but same problem: OAuth + Navan partner onboarding.
- Concur's SAE flat file is what the finance team already receives — it's the "export to Excel" version of the same data. Every Concur customer has access to it.
- We designed the parser to handle both Concur (expense type codes like AIRFR, HOTEL) and Navan (plain-English categories like "flight", "hotel") in the same pipeline.

**Subset handled:**
- Air travel (distance from IATA codes if not provided, DEFRA haversine + RF methodology)
- Hotel stays (per-room-night factor)
- Car rental + taxi/rideshare + rail + mileage (per-km factors where distance available)

**Explicitly ignored:**
- Multi-leg flights (Concur stores as separate rows; we treat each leg independently — correct for emissions)
- Connecting flights with long layovers (treated as two flights — slightly overestimates vs. direct)
- Private aviation — no expense code mapping, out of scope
- Spend-based fallback for ground transport when distance unavailable — spend-based emission factors exist (DEFRA, $/£ per category) but we chose not to implement; flagged instead

**Flight distance methodology:**
- Haversine great-circle distance from IATA coordinates
- 8% route inefficiency uplift (DEFRA guidance)
- Radiative forcing multiplier: 1.891 (DEFRA 2023, accounts for contrail/cirrus effects)
- Short-haul (<3700km): 0.255 kgCO2e/passenger-km; long-haul: 0.195 kgCO2e/passenger-km
- Cabin class multipliers: Economy 1.0×, Premium Economy 1.6×, Business 2.9×, First 4.0×

**What I'd ask the PM:**
- Which travel platform is the client on? (Concur vs. Navan affects header format)
- Do they have international travel with non-IATA airports? (our IATA database covers ~60 major airports)
- How do they want to handle personal vehicle mileage reimbursements — do they have odometer readings or just amounts?

---

## Review workflow: approve vs. sign-off vs. certify?

The assignment says "analysts review and sign off before it goes to auditors." This could mean many things.

**Chose:** Simple status state machine: `pending → (flagged | approved) → locked`

- `pending`: freshly ingested, not yet reviewed
- `flagged`: either system-detected issue OR analyst manually flagged
- `approved`: analyst has reviewed and signs off
- `locked`: after audit submission; immutable

**Why not a multi-step workflow (reviewer → approver → certifier)?**
A 4-day prototype with unknown org structure doesn't warrant building BPMN. The PM said "analysts review and sign off" — singular role, singular action. We preserve the reviewer's identity and timestamp, which is what auditors actually want.

**What I'd ask the PM:**
- Does approval require two analysts (four-eyes principle)? Some standards require it.
- Is there a separate "sustainability manager" sign-off before auditor submission?
- What triggers the lock — a batch action, or does each record get locked individually?

---

## Emission factor approach: location-based vs. market-based Scope 2?

GHG Protocol allows two Scope 2 methods:
- **Location-based**: uses grid average emission factor for the region
- **Market-based**: uses supplier-specific or certificate-based factor (RECs, GOs, PPAs)

**Chose:** Location-based only.

**Why:** Market-based requires knowing the specific supplier contract and any renewable energy certificates. The `is_renewable` flag is preserved from utility exports, but we don't implement market-based calculation. CDP and most corporate reporting accepts location-based alone; market-based is additive.

**What I'd ask the PM:**
- Does the client report to CDP? (CDP requires both location-based and market-based)
- Do they have any PPAs or renewable energy contracts we should know about?

---

## Synchronous job processing vs. async?

**Chose:** Synchronous for prototype (process in the request/response cycle).

**Why:** Celery + Redis adds infrastructure complexity. For files up to ~10,000 rows, synchronous is fine on a 512MB dyno. Above that, the request will time out and we need async.

**Production path:** Each upload creates an `IngestionJob` record, then enqueues a Celery task. The frontend polls `GET /api/jobs/{id}/` until `status=complete`. The architecture supports this — we just run it synchronously for now.
