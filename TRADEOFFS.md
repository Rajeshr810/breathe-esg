# TRADEOFFS.md — What We Deliberately Did Not Build

---

## 1. Celery / async job processing

**What it is:** Running ingestion jobs in a background worker queue (Celery + Redis) rather than synchronously in the HTTP request.

**Why we didn't:** Adds a Redis service and Celery worker to the deployment. For the prototype's data volumes (hundreds to low thousands of rows), synchronous processing completes in under 5 seconds. The architecture already supports async — `process_ingestion_job(job.id)` is a standalone function, and the `IngestionJob` model has all the status fields needed for polling. Switching to async is a one-day addition.

**What breaks without it:** Files with 50,000+ rows (a full year of half-hourly utility data for a large estate) will time out on a standard web dyno. The PM should know this before connecting a large client.

---

## 2. Market-based Scope 2 calculation

**What it is:** A second Scope 2 calculation that uses supplier-specific emission factors from renewable energy certificates (RECs in US, GOs in EU, REGOs in UK) or Power Purchase Agreements, as distinct from the location-based grid average.

**Why we didn't:** Requires knowing each meter's supplier contract and any certificate retirement data — information that isn't in a utility portal CSV export. Getting it requires either direct utility API integration or manual data entry from the sustainability manager. This is a separate data problem, not a calculation problem.

**What breaks without it:** Companies reporting to CDP must disclose both methods. Companies with aggressive renewable energy goals (RE100 signatories, etc.) will have near-zero market-based Scope 2 and misleadingly high location-based Scope 2 in our system. The `is_renewable` flag and `tariff_code` field are preserved to support adding this later.

---

## 3. Scope 3 Category 1 (Purchased Goods & Services) from SAP procurement

**What it is:** Using SAP procurement data (purchase orders, goods receipts) to compute upstream emissions from what the company buys — Scope 3 Category 1, typically the largest source of emissions for non-manufacturing companies.

**Why we didn't:** Mapping purchased materials to emission factors requires a spend-based or physical unit approach. The spend-based approach (EEIO tables) requires mapping SAP G/L accounts or material groups to economic sector codes. The physical unit approach requires knowing what each material is and having a factor for it. Neither is possible without a client-specific mapping table we don't have. The SAP parser ingests the data; the normalization step skips procurement rows and flags them as `MISSING_CATEGORY`. This is the honest thing to do rather than compute junk numbers.

**What breaks without it:** A significant portion of most companies' total footprint is simply absent from this system. The dashboard will show a materially incomplete picture, which is worse than no dashboard. This must be surfaced clearly in the UI.

---

## General notes on scope

These are conscious cuts, not oversights. A real engagement would involve:
- 2-3 weeks of scoping calls before writing a line of code
- A client data sample to verify format assumptions
- Legal review of data processing agreements (PII in travel data)
- A DEFRA factor update job (annual)
- A test suite (no unit tests in this prototype — a notable absence that would not be acceptable in production)
