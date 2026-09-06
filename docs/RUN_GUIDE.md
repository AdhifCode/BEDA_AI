# BEDA Enquiry Intelligence & CRM Automation System
## End-to-End Operational Run Guide

This guide provides exhaustive, step-by-step instructions for installing, configuring, running, and verifying the **BEDA Enquiry Intelligence & CRM Automation Platform** across all environments (Local In-Memory, Async Worker with Redis/SQLite, and Full Docker Compose Stack).

---

## Table of Contents

1. [System Architecture & Components](#1-system-architecture--components)
2. [Prerequisites & System Requirements](#2-prerequisites--system-requirements)
3. [Environment Configuration (`.env`)](#3-environment-configuration-env)
4. [Execution Modes at a Glance](#4-execution-modes-at-a-glance)
5. [Mode 1: Local Quickstart (In-Memory / Inline Execution)](#5-mode-1-local-quickstart-in-memory--inline-execution)
6. [Mode 2: Local Distributed Mode (API + Async Worker + UI)](#6-mode-2-local-distributed-mode-api--async-worker--ui)
7. [Mode 3: Full Docker Compose Production Stack](#7-mode-3-full-docker-compose-production-stack)
8. [Ingesting & Verifying the 12 Test Fixtures (E001–E012)](#8-ingesting--verifying-the-12-test-fixtures-e001e012)
9. [Step-by-Step Operator Walkthrough](#9-step-by-step-operator-walkthrough)
   - [9.1 Ingesting an Enquiry via REST API](#91-ingesting-an-enquiry-via-rest-api)
   - [9.2 Testing Idempotency & Duplicate Delivery](#92-testing-idempotency--duplicate-delivery)
   - [9.3 Managing Reviews & Approvals in the UI](#93-managing-reviews--approvals-in-the-ui)
   - [9.4 Inspecting the Cryptographic Audit Trail](#94-inspecting-the-cryptographic-audit-trail)
10. [Configuring Live LLM Providers (OpenRouter, OpenAI, Gemini, Ollama)](#10-configuring-live-llm-providers-openrouter-openai-gemini-ollama)
11. [Running the Automated Test Suite](#11-running-the-automated-test-suite)
12. [Troubleshooting & Incident Runbooks](#12-troubleshooting--incident-runbooks)

---

## 1. System Architecture & Components

The BEDA Enquiry Intelligence platform consists of three core application services and two supporting infrastructure components:

```
                          ┌─────────────────────────────┐
                          │    Inbound Ingestion API    │
                          │   (FastAPI, Port: 8000)     │
                          └──────────────┬──────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌────────────────────────┐                     ┌────────────────────────┐
     │  SQLite / PostgreSQL   │                     │  In-Memory / Redis     │
     │   (Database State)     │                     │     Streams Queue      │
     └────────────────────────┘                     └────────────┬───────────┘
                 ▲                                               │
                 │              ┌────────────────────────────────┘
                 │              ▼
                 │    ┌─────────────────────────────┐
                 │    │     Background Worker       │
                 └────┤ (Extraction, CRM & Drafting)│
                      └─────────────────────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────┐
                      │   Review & Operations UI    │
                      │ (React + Vite, Port: 3000)  │
                      └─────────────────────────────┘
```

| Service | Technology | Default Port | Description |
|---|---|---|---|
| **API Server** (`apps/api`) | FastAPI / Uvicorn | `8000` | Ingestion webhooks, REST queries, review decisions, audit verification |
| **Worker Process** (`apps/worker`) | Python `asyncio` | Background process | Dequeues enquiries, orchestrates AI parsing, CRM identity, policy rules |
| **Review UI** (`apps/review-ui`) | React 18 / TypeScript / Vite | `3000` | Operations console for triaging queues, CRM review, draft approval |
| **Database** | SQLite / PostgreSQL 16 | Local / `5434` (Docker) | Storage for enquiries, AI runs, CRM resolutions, drafts, audit events |
| **Queue** | In-Memory / Redis 7 Streams | Local / `6380` (Docker) | Durable event stream decoupling ingestion from workflow processing |

---

## 2. Prerequisites & System Requirements

### Core Requirements
- **Python 3.12+**: Verify with `python3 --version`
- **Node.js 20+ & npm**: Verify with `node -v && npm -v` (needed for Review UI)
- **Git**: For source version control
- **Bundled `uv`**: The repository includes a high-performance standalone `uv` package manager at `./bin/uv`. No global installation required.

### Optional (For Docker Compose Mode)
- **Docker Engine 24+** & **Docker Compose v2.20+**: Verify with `docker compose version`

---

## 3. Environment Configuration (`.env`)

The application loads environment variables from `.env`. A baseline template is provided in `.env.example`.

### Creating Your `.env` File
```bash
cp .env.example .env
```

### Essential Settings Overview
```bash
# Application Mode
APP_ENV=development
PROCESS_INLINE=true               # Set to 'true' for local single-process mode, 'false' for worker queue mode

# Database
# Lightweight SQLite (default for local development):
DATABASE_URL=sqlite+aiosqlite:///./beda.db
# PostgreSQL (for Docker or production):
# DATABASE_URL=postgresql+psycopg://beda:beda@localhost:5434/beda

# Event Queue
# In-memory queue (no Redis required):
REDIS_URL=memory
# Redis Streams (for Docker or distributed mode):
# REDIS_URL=redis://localhost:6380/0

# LLM Gateway
LLM_PROVIDER=fake                  # Options: fake | openrouter | openai | openai-compatible
LLM_CONFIDENCE_THRESHOLD=0.80      # Scores below 0.80 route to Human AI Review
AUTH_MODE=local                    # Local role-based authorization headers
```

---

## 4. Execution Modes at a Glance

Choose the mode best suited for your current goal:

| Mode | External Services Required | Processing Model | Best For |
|---|---|---|---|
| **Mode 1: Local Quickstart** | None (Zero external dependencies) | Inline (FastAPI processes directly) | Quick evaluation, unit testing, fixture replay |
| **Mode 2: Local Distributed** | None (uses Memory queue) or Redis | Asynchronous (Worker dequeues from API) | Realistic production-like async queue testing |
| **Mode 3: Docker Compose** | Docker daemon | Full multi-container (Postgres + Redis + API + Worker + UI) | Complete containerized deployment & demo |

---

## 5. Mode 1: Local Quickstart (In-Memory / Inline Execution)

This is the fastest method to get up and running without installing databases or background queues.

### Step 1: Install Python Dependencies
Using the bundled high-speed `uv` binary:
```bash
# Create virtual environment if not present
./bin/uv venv .venv --python 3.12

# Install project and development dependencies in editable mode
./bin/uv pip install --python .venv/bin/python -e ".[dev]"
```

Alternatively, using standard Python:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Step 2: Initialize Database and Seed Data
This creates the SQLite database (`beda.db`), applies schema migrations, and seeds the BEDA staff directory and CRM contacts (C001–C005):
```bash
.venv/bin/python scripts/seed.py
# Or using Makefile:
make seed
```

### Step 3: Start the Backend API Server
```bash
.venv/bin/python -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```
- API Health Live: `http://localhost:8000/health/live`
- API Health Ready: `http://localhost:8000/health/ready`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`

### Step 4: Start the Frontend Review UI
In a separate terminal:
```bash
cd apps/review-ui
npm install
npm run dev
```
- Open your browser to `http://localhost:3000`
- The Vite development server automatically proxies `/api` and `/health` requests to `http://localhost:8000`.

---

## 6. Mode 2: Local Distributed Mode (API + Async Worker + UI)

To run the system with asynchronous decoupling between the API ingestion and the background processing worker:

### Step 1: Update `.env` for Asynchronous Processing
In your `.env` file, configure:
```bash
PROCESS_INLINE=false
REDIS_URL=memory   # Or redis://localhost:6379/0 if you have a local Redis server running
```

### Step 2: Launch Terminal Services
Open three separate terminal windows:

**Terminal 1 — Backend Ingestion API:**
```bash
.venv/bin/python -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Background Worker Process:**
```bash
.venv/bin/python apps/worker/main.py
```
*Output will show: `Starting BEDA Worker process...` listening for inbound enquiries on the queue.*

**Terminal 3 — Frontend Review Dashboard:**
```bash
cd apps/review-ui
npm run dev
```

When an enquiry arrives at `/api/v1/ingest/*`, the API enqueues the job and responds in `< 10ms`. The worker picks up the job, executes normalization, AI entity extraction, CRM resolution, policy rules, and writes the audit chain.

---

## 7. Mode 3: Full Docker Compose Production Stack

To run the complete production architecture with dedicated PostgreSQL 16 and Redis 7 containers:

### Step 1: Build and Launch Containers
```bash
docker compose up -d --build
```

### Step 2: Verify Container Health
```bash
docker compose ps
```
You should see 5 running healthy containers:
- `beda-postgres` (PostgreSQL 16 on port `5434`)
- `beda-redis` (Redis 7 on port `6380`)
- `beda-api` (FastAPI on port `8000`)
- `beda-worker` (Background Worker)
- `beda-review-ui` (Nginx serving production React build on port `3000`)

### Step 3: Run Seed and Ingestion inside Container
```bash
# Seed users and CRM data
docker compose exec api python scripts/seed.py

# Ingest test fixtures E001-E012
docker compose exec api python scripts/ingest_fixtures.py
```

### Step 4: Access Services
- Review UI: `http://localhost:3000`
- API Documentation: `http://localhost:8000/docs`
- View container logs: `docker compose logs -f api worker`

### Step 5: Stop Containers
```bash
docker compose down
# To also delete persistent database volumes:
docker compose down -v
```

---

## 8. Ingesting & Verifying the 12 Test Fixtures (E001–E012)

The system includes a comprehensive fixture runner (`scripts/ingest_fixtures.py`) that executes all 12 canonical BEDA test cases:

```bash
.venv/bin/python scripts/ingest_fixtures.py
# Or:
make ingest-fixtures
```

### Expected Deterministic Fixture Outcomes

```text
================================================================================
DETERMINISTIC FIXTURE SUMMARY (Section 20 Compliance)
================================================================================
E001 MATCHED C001 → Matt Cooper             (Hume Logistics, 2.1 GWh Solar/Battery)
E002 AMBIGUOUS C001/C002 → CRM review       (Hume Logistic naming ambiguity)
E003 MATCHED C003 → accounts/operations     (Greenfields Foods, $2,640 PO discrepancy)
E004 SPAM → quarantined                     (Unsolicited CEO email list)
E005 MATCHED C004 → clarification draft     (Northbank College, missing bill/fixtures)
E006 TECHNICAL → technical review           (Harmonics THD / OWNER_UNCONFIGURED)
E007 NON-SALES → non-sales review           (Internship application)
E008 MATCHED C005 → Ties Rahardjo + approval(Solara 4-person crew confirmation)
E009 NEW/UNRESOLVED → Matt Cooper           (Newcastle refrigerated warehouse, phone 0411 999 120)
E010 IDENTITY CONFLICT → identity review    (Sam phone correction 0411 999 102, conflict preserved)
E011 INTERNAL INCIDENT → Ali Pratama        (HubSpot OAuth failure token expiration)
E012 NEW/UNRESOLVED → Matt Cooper + approval(Small leased cafe, landlord consent constraint)
================================================================================
All 12 enquiry audit chains verified cryptographically and intact!
```

---

## 9. Step-by-Step Operator Walkthrough

### 9.1 Ingesting an Enquiry via REST API

You can submit an enquiry through any of the three ingestion adapters:

#### Email Ingestion:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/email \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": "msg-custom-001",
    "from": "Amelia Grant <amelia.grant@humelogistics.example>",
    "subject": "Solar Proposal for 3 Warehouses",
    "body": "We operate warehouses in Truganina, Dandenong and Epping. Consumption is about 2.1 GWh/year. Please call on 0400 111 020.",
    "attachments": ["01_hume_energy_bill.txt"]
  }'
```
**Response (`200 OK`):**
```json
{
  "enquiry_id": "9f2028ad-9095-4525-aa6f-d468759dc363",
  "status": "APPROVAL",
  "duplicate": false
}
```

#### Web Form Ingestion:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/web-form \
  -H "Content-Type: application/json" \
  -d '{
    "submission_id": "web-custom-002",
    "fields": {
      "company_name": "Northbank College",
      "contact_name": "Melissa Tran",
      "email": "melissa.tran@northbankcollege.example",
      "phone": "0400 330 110"
    },
    "message": "We have 1,100 fluorescent fixtures and need an LED upgrade consultation.",
    "attachments": []
  }'
```

#### Messaging (SMS / WhatsApp) Ingestion:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/messaging \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": "sms-custom-003",
    "sender": {
      "name": "Daniel Wu",
      "phone": "0400 880 101"
    },
    "text": "Can BEDA confirm the 4-person Ballarat crew for the week of Sept 14?",
    "attachments": []
  }'
```

---

### 9.2 Testing Idempotency & Duplicate Delivery

Submit the exact same payload a second time with the identical `message_id`:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/email \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": "msg-custom-001",
    "from": "Amelia Grant <amelia.grant@humelogistics.example>",
    "subject": "Solar Proposal for 3 Warehouses",
    "body": "Duplicate send",
    "attachments": []
  }'
```
**Response:**
```json
{
  "enquiry_id": "9f2028ad-9095-4525-aa6f-d468759dc363",
  "status": "APPROVAL",
  "duplicate": true
}
```
*The duplicate delivery is detected atomically using the idempotency key `email:msg-custom-001`. No duplicate records, workflows, or side effects are triggered.*

---

### 9.3 Managing Reviews & Approvals in the UI

1. Open **`http://localhost:3000`** in your browser.
2. In the sidebar, filter the queue by:
   - **`APPROVAL`**: Enquiries requiring human sign-off before dispatching consequential commitments (Tier 5).
   - **`REVIEW`**: Enquiries requiring identity disambiguation (e.g. C001 vs C002) or technical engineering review.
   - **`QUARANTINED`**: Filtered spam.
3. Click on an enquiry (e.g., `E008` Solara crew confirmation or `E001` Hume Logistics):
   - **AI Understanding Card**: View classified category, confidence score, and extracted energy parameters.
   - **CRM Identity Resolution Card**: View matched customer ID (`C001`), match confidence, and candidate reasons.
   - **Response Draft Card**: Review the generated draft response and grounding evidence references.
4. **Take Action**:
   - Select your actor role (`admin` or `reviewer`).
   - Choose **`Approve Draft`**, **`Edit & Approve`** (modify draft content), or **`Reject`**.
   - Provide an optional operational note and click **Submit Decision**.
   - Notice the status immediately changes to `COMPLETED` and the approval is recorded.

---

### 9.4 Inspecting the Cryptographic Audit Trail

Every state change, AI run, CRM resolution, and human decision is cryptographically chained using SHA-256 hashes.

To verify the chain for any enquiry via API:
```bash
curl -s http://localhost:8000/api/v1/enquiries/<ENQUIRY_ID>/audit | jq .
```

**Output:**
```json
{
  "enquiry_id": "9f2028ad-9095-4525-aa6f-d468759dc363",
  "chain_valid": true,
  "total_events": 6,
  "events": [
    {
      "id": "7cae0bba-1b6c-4824-beae-a39c0fa43a53",
      "event_type": "ENQUIRY_INGESTED",
      "actor_type": "system",
      "actor_id": "channel_adapter",
      "timestamp": "2026-09-05T07:50:44.908000Z",
      "previous_event_hash": null,
      "event_hash": "a4d38c645ecb37ce3fa78d5ebdb3a23a31518f8e02934ff2448df74092b3a886"
    },
    {
      "id": "67deaebe-a1c4-42b4-ba1f-b51ea1a601e3",
      "event_type": "AI_UNDERSTANDING_COMPLETED",
      "actor_type": "llm",
      "actor_id": "fake",
      "timestamp": "2026-09-05T07:50:44.921000Z",
      "previous_event_hash": "a4d38c645ecb37ce3fa78d5ebdb3a23a31518f8e02934ff2448df74092b3a886",
      "event_hash": "f6261c37d4e339b626d5fcda6fbe6c469f3792c3a50daefceaeecdeefce634b0"
    }
  ]
}
```
In the UI, a green **`Audit Chain: Cryptographically Intact`** badge verifies that no database record has been altered or retroactively tampered with.

---

## 10. Configuring Live LLM Providers (OpenRouter, OpenAI, Gemini, Ollama)

By default, `LLM_PROVIDER=fake` provides 100% deterministic, zero-latency execution. To use live LLM models:

### Option A: OpenRouter (Recommended for Free Models)
OpenRouter provides free access to leading open-source models:
```bash
LLM_PROVIDER=openrouter
LLM_API_KEY=sk-or-v1-your-actual-api-key-here
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_STRONG_MODEL=meta-llama/llama-3.3-70b-instruct:free
# Alternative free models:
# LLM_STRONG_MODEL=google/gemini-2.0-flash-exp:free
# LLM_STRONG_MODEL=qwen/qwen-2.5-72b-instruct:free
OPENROUTER_REFERER=http://localhost:8000
OPENROUTER_TITLE=BEDA Enquiry Intelligence
```

### Option B: OpenAI Direct
```bash
LLM_PROVIDER=openai
LLM_API_KEY=sk-your-openai-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_STRONG_MODEL=gpt-4o-mini
```

### Option C: Google Gemini via OpenAI Compatibility
```bash
LLM_PROVIDER=openai-compatible
LLM_API_KEY=AIzaSy...your-gemini-key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_STRONG_MODEL=gemini-2.0-flash
```

### Option D: Local Ollama (100% Offline & Private)
```bash
LLM_PROVIDER=openai-compatible
LLM_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_STRONG_MODEL=llama3.3:latest
```

---

## 11. Running the Automated Test Suite

The test suite validates compliance with deterministic validation, security boundaries, and fixture acceptance criteria:

```bash
# Run all 26 tests
.venv/bin/pytest -v tests/
# Or:
make test
```

### Sub-Suite Commands
```bash
# Unit tests (Normalizer, Audit Chaining, Policy, LLM parser)
make test-unit
# Or: .venv/bin/pytest -v tests/unit/

# Integration tests (End-to-end ingestion pipeline, idempotency, approval flow)
make test-integration
# Or: .venv/bin/pytest -v tests/integration/

# Security tests (Prompt injection defense, Delimiter isolation, RBAC denial)
make test-security
# Or: .venv/bin/pytest -v tests/security/

# Acceptance tests (Verifies exact outcomes for E001-E012)
make test-fixtures
# Or: .venv/bin/pytest -v tests/fixtures/

# API Smoke tests (HTTP-level validation of all endpoints)
make smoke
# Or: .venv/bin/python scripts/smoke_test.py
```

---

## 12. Troubleshooting & Incident Runbooks

### Common Setup Issues

#### 1. Port 8000 or 3000 Already in Use
Check and kill the conflicting process:
```bash
# Identify process on port 8000
lsof -i :8000
# Kill process
kill -9 <PID>
```
Or start the API on an alternative port:
```bash
.venv/bin/python -m uvicorn apps.api.main:app --port 8001
```

#### 2. SQLite Database Locked (`sqlite3.OperationalError: database is locked`)
Ensure `aiosqlite` is used as configured in `DATABASE_URL=sqlite+aiosqlite:///./beda.db`. If tests ran in parallel, remove the test file and re-seed:
```bash
rm -f beda.db
make seed
```

#### 3. Review UI Shows Network Errors
Ensure the FastAPI backend is running on `http://localhost:8000`. The Vite server forwards `/api` calls directly to port `8000`.

### Dedicated Incident Runbooks
For operational outages, prompt injection alerts, and disaster recovery, refer to the runbooks in `docs/runbooks/`:
- [Prompt Injection Defense](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/prompt-injection.md)
- [CRM Outage & Fallback](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/crm-outage.md)
- [LLM Gateway Outage](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/llm-outage.md)
- [Queue Backlog & Replay](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/queue-backlog.md)
- [Dead Letter Queue Replay](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/dead-letter-replay.md)
- [Duplicate Event Storms](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/duplicate-event-storm.md)
- [Model Regression Triage](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/model-regression.md)
- [Database Restore](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/database-restore.md)
- [Deployment Rollback](file:///home/adhif/Documents/antigravity/splendid-hawking/docs/runbooks/deployment-rollback.md)
