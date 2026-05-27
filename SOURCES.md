# SOURCES.md — Source Research and Sample Data Rationale

---

## Source 1: SAP Fuel & Procurement Data

### What format I researched

SAP exposes goods movement data primarily through:

1. **Transaction MB51** (Material Document List): the standard transaction sustainability teams actually use to pull fuel consumption. Runs as a foreground or background job; output is a flat ALV grid exportable to .txt or .xlsx.

2. **Transaction ME2M** (Purchase Orders by Material): for procurement-side data.

3. **Standard fields in movement data:**
   - `BELNR`: Material document number (Belegnummer) — unique per posting
   - `BUKRS`: Company code (Buchungskreis) — top-level org unit
   - `WERKS`: Plant (Werk) — physical location; often a 4-digit number
   - `MATNR`: Material number — 18-char internal code
   - `MAKTX`: Material short text — truncated at 40 chars
   - `BUDAT`: Posting date — in German locale: DD.MM.YYYY
   - `MENGE`: Quantity — German locale: period=thousands, comma=decimal
   - `MEINS`: Base unit of measure — SAP internal code (L, KG, M3, KWH, etc.)
   - `BWART`: Movement type — 261=goods issue, 262=reversal, 101=goods receipt

### What I learned

- SAP exports are **not CSV-friendly by default**. The ALV export produces tab-separated text where number formats respect the user's locale. A German-locale user produces `1.234,56` (comma decimal, period thousands); a US-locale user produces `1,234.56`. The same company can have both if employees are in different countries.

- **Plant codes are opaque integers**. `1000` is a common default SAP plant code. The client's SAP configuration determines what it means. Without a plant-to-facility mapping table (SAP table T001W), you cannot know what country or facility the data is from.

- **Movement type 261** is the canonical consumption event. Everything else is either a stock movement (not consumption) or a reversal that nets against a prior 261.

- **Material descriptions are truncated** at 40 characters and may be in German. "Dieselkraftstoff" (German: diesel fuel) is common in European SAP configurations.

- **Units are SAP internal codes**, not SI symbols. `L` = liter, `KG` = kilogram, `M3` = cubic meter, `NM3` = normal cubic meter (for gas), `KWH` = kilowatt-hour.

### Sample data and why it looks the way it does

Our sample data includes:
- Company code `1000` (SAP default for German headquarters)
- Plant codes `1000` (Hamburg), `1010` (Munich), `2000` (UK subsidiary) — opaque, requiring lookup
- Material `DIESEL-001` with text `Dieselkraftstoff` (German) and `DIESEL002` with `Diesel Fuel` (English) — same company, different user language settings
- Dates in `DD.MM.YYYY` format
- Quantities with German decimal format (`1.234,56`)
- Movement types 261 (consumption) and 262 (one reversal to show netting)
- One row with movement type 311 (stock transfer, not consumption) — tests our skip logic

### What would break in production

1. **Plant codes without a mapping table**: 60% of records will get `MISSING_FACILITY` flags until the client provides T001W.
2. **Material categorization**: our keyword matching for fuel types will miss internal code systems (e.g., material number `RM-10432` with description `Type A Fuel` — too vague to classify).
3. **Procurement rows**: purchase orders have a completely different document structure; we'd need a separate parser for `EKKO`/`EKPO` (purchasing header/line item).
4. **Multi-currency**: the amount field (`DMBTR`) is in the document currency which may differ from the group reporting currency. We parse it but don't use it for emissions calculation.

---

## Source 2: Utility Electricity Data

### What format I researched

**Green Button standard (ESPI XML)**: The US Department of Energy's standard for utility data sharing. Defined in ANSI/NAESB REQ.21. Exposes `UsagePoint` (meter), `MeterReading`, `IntervalBlock` (time series of readings), and `ReadingType` (what's being measured — active energy, reactive power, etc.).

**UK half-hourly (HH) data**: Large UK commercial meters are settled on a half-hourly basis. Data is available from the Distribution Network Operator (DNO) or via data aggregators (e.g., Stark, Inenco). Format is a CSV with MPAN (Meter Point Administration Number), settlement date, and 48 half-hourly consumption values per row.

**Portal CSV exports**: National Grid's portal, UK Power Networks' portal, US utilities' account management sites — all offer a "download CSV" option that produces a flat file with billing period + consumption.

### What I learned

- **Billing periods are not calendar months.** A utility reads meters on a cycle — every 28-33 days. Your December bill might cover November 15 to December 18. This creates gaps and overlaps when you try to aggregate by calendar month.

- **Net metering**: Sites with solar panels will have *negative* consumption rows in some exports (export to grid). Our parser flags these but doesn't exclude them.

- **Units vary.** Residential = kWh. Large commercial = MWh or kVAh. Some exports use `kW·h` (with interpunct). Industrial = sometimes GJ or MWh.

- **Renewable attribution**: "Green tariff" suppliers claim 100% renewable but may not retire REGOs (Renewable Energy Guarantees of Origin) against your specific consumption. The `is_renewable` flag is marketing as much as fact.

- **UK MPANs** are 21-digit numbers. US account numbers are arbitrary strings. Neither is human-readable.

### Sample data and why it looks the way it does

Our sample includes:
- 3 meters: `MPAN-001` (UK, standard billing), `MPAN-002` (UK, HH interval data), `METER-US-001` (US, Green Button-style)
- Billing periods that cross month boundaries (e.g., Nov 15 – Dec 18)
- One period gap on MPAN-001 (missing December bill — triggers PERIOD_GAP flag)
- One negative consumption row on METER-US-001 (solar export)
- Mixed units: kWh (MPAN-001), MWh (METER-US-001)
- Different date formats per meter: `DD/MM/YYYY` (UK), `MM/DD/YYYY` (US)

### What would break in production

1. **PDF bills**: ~30% of facilities managers have PDF bills only. No reliable parser; OCR would need a service like AWS Textract.
2. **Half-hourly data volumes**: a large estate with 100 HH meters generates ~1.75 million rows/year. Current synchronous processing would time out.
3. **Tariff-based disaggregation**: some clients want to separate peak vs. off-peak consumption for more granular reporting. We don't model tariff periods.
4. **Grid region for emission factors**: we map facility → grid_region → emission factor. If a facility's grid_region is blank, we fall back to a national average. UK offshore oil platforms are technically in the UK grid but have very different consumption profiles.

---

## Source 3: Corporate Travel Data

### What format I researched

**Concur Standard Accounting Extract (SAE)**: Documented in SAP Concur's "SAE Import/Export Guide." Fixed-width or delimited file, one row per expense line item. Fields include: `ReportKey`, `ExpKey`, `EmployeeID`, `ExpenseType`, `TransactionDate`, `TransactionAmount`, `CurrencyCode`. Travel-specific fields (origin/destination airports, cabin class, hotel name) are stored in separate "Itemization" rows linked to the parent expense.

**Navan (TripActions) export**: REST API returns JSON; CSV admin export has columns: `Trip ID`, `Traveler Email`, `Category`, `Travel Date`, `From`, `To`, `Class`, `Amount`, `Currency`. Cleaner structure than Concur.

**Amex Global Business Travel (GBT)**: Similar CSV structure; categorizes by `ServiceType` (AIR, HTL, CAR, etc.).

### What I learned

- **IATA codes are not always provided for domestic rail/ground.** Concur stores the booking reference, not origin/destination codes, for many rail bookings. We handle this by flagging ground transport without distance as `FACTOR_NOT_FOUND` and noting that spend-based estimation is the fallback.

- **Concur expense types are client-configured.** The standard codes (AIRFR, HOTEL, CARRT) are defaults, but clients rename them. One client's "Client Entertainment Travel" could map to air or ground. Our parser handles unknown codes by flagging, not crashing.

- **Radiative forcing is contested.** DEFRA uses RF = 1.891; some other frameworks (ICAO Carbon Calculator) don't apply RF at all. We use DEFRA and document the methodology. This will be a point of discussion with the client.

- **Employee PII**: Concur exports contain employee IDs, sometimes names. GDPR requires a legal basis for processing. We hash employee IDs immediately and never store names.

- **Multi-leg flights**: Concur stores each flight segment as a separate row. LHR→FRA→NRT appears as two rows: LHR→FRA and FRA→NRT. Our parser handles each independently, which is the correct methodology (emissions are segment-level).

### Sample data and why it looks the way it does

Our sample includes:
- Mix of Concur-style codes (AIRFR, HOTEL) and Navan-style names (flight, hotel)
- Long-haul flights (LHR→JFK, LHR→SIN) and short-haul (LHR→AMS, LHR→EDI)
- Cabin class variation: economy, business (triggers different RF multiplier)
- Hotel stays: UK (known country), and one "unknown" country to test fallback
- Car rental with distance provided vs. taxi with no distance (tests flag behavior)
- One row with an unknown IATA code (BRS — Bristol, not in our airport table) to test FACTOR_NOT_FOUND
- Dates in both US (MM/DD/YYYY) and ISO (YYYY-MM-DD) format

### What would break in production

1. **Airport coordinate coverage**: our IATA table covers ~60 major airports. There are ~9,000 commercial airports globally. An employee flying Johannesburg→Nairobi (JNB→NBO) would get `FACTOR_NOT_FOUND`. Production fix: load the full OurAirports database.
2. **Rail in non-UK markets**: DEFRA factors are UK-centric. European Eurostar or US Amtrak have different intensity. We'd need market-specific factors.
3. **Spend-based fallback**: when ground transport has no distance, we flag rather than estimate. Production should implement DEFRA spend-based factors (kgCO2e per £ spent by category).
4. **Concur itemization rows**: the SAE format stores hotel and car details as child rows under the parent expense. Our parser assumes a flat structure. A real Concur integration would need to handle parent-child row relationships.
