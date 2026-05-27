# Breathe ESG — Emissions Ingestion Platform

A Django REST + React application for ingesting, normalising, and reviewing emissions data from SAP, utility portals, and corporate travel platforms.

**Live demo:** [Add your deployed URL here]

**Demo credentials:**
- Analyst: `analyst` / `breathe-analyst-2024`  
- Admin: `admin` / `breathe-admin-2024`

---

## Architecture

```
breathe_esg/
├── backend/              # Django 4.2 + Django REST Framework
│   ├── config/           # Settings, URLs, WSGI
│   ├── core/             # Models, migrations, seed command
│   ├── ingestion/        # Parsers (SAP, utility, travel) + normalizer + task runner
│   └── api/              # DRF views, serializers, URL routing
├── frontend/             # React 18 + Vite + Tailwind CSS
│   └── src/
│       └── App.jsx       # Single-page dashboard
├── sample_data/          # Realistic sample files for all three sources
├── MODEL.md              # Data model design and rationale
├── DECISIONS.md          # Every ambiguity resolved with justification
├── TRADEOFFS.md          # What was deliberately not built and why
└── SOURCES.md            # Source format research and sample data rationale
```

---

## Local Development

### Prerequisites
- Python 3.11+
- Node.js 18+
- (Optional) PostgreSQL — SQLite works fine locally

### Backend setup

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
SECRET_KEY=local-dev-secret-key-change-me
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
EOF

python manage.py migrate
python manage.py seed_data        # Creates users, emission factors, ingests sample data
python manage.py runserver
```

Backend runs at `http://localhost:8000`

### Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:3000` (proxies `/api/*` to Django).

### Get an auth token (for API testing)

```bash
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst", "password": "breathe-analyst-2024"}'
```

---

## Deployment on Railway (recommended — free tier available)

1. Push this repo to GitHub
2. Create a new Railway project → "Deploy from GitHub"
3. Add a PostgreSQL plugin
4. Set environment variables:
   ```
   SECRET_KEY=<generate a strong key>
   DJANGO_SETTINGS_MODULE=config.settings
   ALLOWED_HOSTS=<your-app>.up.railway.app
   CORS_ALLOWED_ORIGINS=https://<your-frontend-url>
   CSRF_TRUSTED_ORIGINS=https://<your-app>.up.railway.app
   DATABASE_URL=<auto-set by Railway PostgreSQL plugin>
   ```
5. Railway will run `Procfile` → `release` command (migrate + seed) then `web`

## Deployment on Render

1. Push this repo to GitHub
2. Create a new Render Blueprint → point to `render.yaml`
3. Render will create the backend service, frontend static site, and PostgreSQL database automatically

---

## API Reference

### Authentication
All endpoints require `Authorization: Token <token>` header.

### Ingestion
```
POST /api/jobs/upload/
  Form data: source_type (sap|utility|travel), file
  → Creates IngestionJob, processes synchronously, returns job summary

GET  /api/jobs/
  → List all ingestion jobs for the user's organisation

GET  /api/jobs/{id}/errors/
  → Full error log for a job (row-level failures)
```

### Records
```
GET  /api/records/
  Query params: status, scope, source_type, flagged, date_from, date_to, page
  → Paginated list of EmissionsRecords

GET  /api/records/{id}/
  → Full record detail with emission factor, facility, and audit log

POST /api/records/{id}/approve/
  Body: {"note": "looks correct"}
  → Approve a record; creates AuditLog entry

POST /api/records/{id}/flag/
  Body: {"message": "check this value"}
  → Flag for attention; adds ANALYST_FLAG to flags array

POST /api/records/bulk_approve/
  Body: {"ids": ["uuid1", "uuid2", ...], "note": "batch approved"}
  → Approve multiple records at once

POST /api/records/{id}/lock/
  → Lock for audit (must be approved first)

GET  /api/records/{id}/history/
  → Audit trail (all AuditLog entries for this record)
```

### Dashboard
```
GET  /api/dashboard/summary/
  → Scope 1/2/3 totals (tCO2e), review queue counts, recent jobs
```

---

## Sample Data

Three files in `sample_data/` — see `SOURCES.md` for the research behind each.

| File | Source | Rows | Notable quirks |
|------|--------|------|----------------|
| `sap_mm60_export_2023_H1.txt` | SAP MM60 | 34 | German column headers, DD.MM.YYYY dates, German decimal format, plant codes, movement type 311 (stock transfer, skipped), one reversal (262) |
| `utility_portal_export_2023.csv` | UK/US utility portal | 43 | Billing periods crossing month boundaries, MWh and kWh mixed units, negative solar export rows, missing December bill (gap flag) |
| `travel_concur_navan_2023_H1.csv` | Concur + Navan mixed | 59 | Both Concur codes (AIRFR, HOTEL) and Navan names (flight, hotel), BRS airport (unknown IATA → FACTOR_NOT_FOUND flag), First/Business/Premium Economy/Economy cabin class variation, mileage reimbursement |

---

## Design documents

Read these in order for the full picture:

1. **MODEL.md** — data model rationale, multi-tenancy, audit trail design
2. **DECISIONS.md** — every format/scope/workflow decision with justification
3. **TRADEOFFS.md** — three deliberate cuts and what breaks without them  
4. **SOURCES.md** — format research, what was learned, what would break in production
