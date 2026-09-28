# Multi-Tenant Wallet API

A REST API built with **Django + Django REST Framework** that provides a multi-tenant wallet/ledger service. Multiple tenants (merchants or organizations) operate on the same platform with fully isolated data.

---

## Features

- **Multi-Tenancy**: Tenant resolution via `X-Tenant-ID` or `X-API-Key` headers. Complete data isolation between tenants.
- **Wallet Management**: Create wallets, deposit, withdraw, and transfer funds.
- **Immutable Ledger**: Every balance change is recorded as an immutable transaction — the ledger is the source of truth.
- **Atomic Transfers**: Both sides of a transfer succeed or neither does.
- **Idempotency**: Client-provided `idempotency_key` prevents double-charges on retried requests (scoped per tenant).
- **Concurrency Safety**: `select_for_update` row locking with deadlock prevention (ordered locking).
- **Money Handling**: All amounts stored as integer minor units (paisa/cents) — never floats.

---

## Tech Stack

- Python 3.10+
- Django 6.x
- Django REST Framework 3.x
- SQLite (default) / PostgreSQL (recommended for production)

---

## Setup & Run

### 1. Clone the repository

`manage.py`, `requirements.txt`, and this README live at the **repository root**. After cloning, stay in that directory (do not `cd` into the `wallet_api` Python package).

```bash
git clone <repo-url>
cd <cloned-repo>
```

### 2. Create and activate virtual environment

```bash
python -m venv my_env

# Windows
my_env\Scripts\activate

# macOS/Linux
source my_env/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run migrations

```bash
python manage.py migrate
```

### 5. Run the development server

```bash
python manage.py runserver
```

### 6. Run tests

```bash
python manage.py test wallet_app
```

On SQLite this runs the core suite and **skips the two concurrent race tests** (`select_for_update` is not row-level on SQLite). To run those as well, point Django at PostgreSQL and re-run the same command.

---

## API Endpoints

All endpoints (except tenant creation) require the `X-Tenant-ID` or `X-API-Key` header.

### Tenants

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants/` | Create a new tenant |

### Wallets

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/wallets/` | Create a wallet |
| `GET` | `/api/wallets/` | List tenant's wallets |
| `GET` | `/api/wallets/{id}/` | Get wallet detail + balance |
| `POST` | `/api/wallets/{id}/deposit/` | Deposit funds |
| `POST` | `/api/wallets/{id}/withdraw/` | Withdraw funds |
| `GET` | `/api/wallets/{id}/transactions/` | Paginated transaction history |

### Transfers

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/transfers/` | Transfer funds between wallets |

---

## API Examples

### Create a Tenant

```bash
curl -X POST http://localhost:8000/api/tenants/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp"}'
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Acme Corp",
  "api_key": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "created_at": "2026-09-28T15:30:00Z"
}
```

### Create a Wallet

```bash
curl -X POST http://localhost:8000/api/wallets/ \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{"name": "John Doe", "email": "john@example.com"}'
```

### Deposit

```bash
curl -X POST http://localhost:8000/api/wallets/{wallet_id}/deposit/ \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: {tenant_id}" \
  -d '{"amount": 10000, "idempotency_key": "dep-001"}'
```

### Withdraw

```bash
curl -X POST http://localhost:8000/api/wallets/{wallet_id}/withdraw/ \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: {tenant_id}" \
  -d '{"amount": 3000, "idempotency_key": "wd-001"}'
```

### Transfer

```bash
curl -X POST http://localhost:8000/api/transfers/ \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: {tenant_id}" \
  -d '{
    "from_wallet_id": "{wallet_a_id}",
    "to_wallet_id": "{wallet_b_id}",
    "amount": 5000,
    "idempotency_key": "tx-001"
  }'
```

---

## Data Model

```
Tenant (1) ──→ (N) Wallet ──→ (N) Transaction
   │                                    
   └──→ (N) IdempotencyKey              
```

- **Tenant**: Merchant/organization with unique API key
- **Wallet**: Holds user info (name, email) + balance (minor units)
- **Transaction**: Immutable ledger entry (DEPOSIT, WITHDRAWAL, TRANSFER_IN, TRANSFER_OUT)
- **IdempotencyKey**: Prevents duplicate operations (scoped per tenant)

---

## Design Decisions & Trade-offs

### User = Wallet
The assessment refers to "user / wallet" as one concept. Rather than having a separate User model with a OneToOne to Wallet, I merged them into a single `Wallet` model with user fields (name, email). This simplifies the data model and API.

### Ledger as Source of Truth
The `balance` field on `Wallet` is a **denormalized cache** for fast reads. The `Transaction` table is the authoritative ledger. Every balance change creates an immutable transaction record.

### Deadlock Prevention
Transfers lock both wallets using `select_for_update()`. To prevent deadlocks when two concurrent transfers go in opposite directions (A→B and B→A), wallets are always locked in ascending ID order.

### Idempotency
Each tenant gets its own namespace for idempotency keys. When a duplicate key is detected, the cached response is returned without re-processing. This prevents double-charges even under network retries.

### SQLite vs PostgreSQL
SQLite is the default so the project runs with no extra services. `select_for_update()` on SQLite locks the whole database, so the concurrent withdrawal/transfer tests are skipped unless `DATABASES` is PostgreSQL. Use PostgreSQL if you want to exercise those race cases.

### Money Storage
All monetary values are stored as `BigIntegerField` in minor units (paisa/cents). This avoids floating-point precision issues.

---

## Assumptions

1. Each wallet has a unique email within a tenant (same email can exist across tenants).
2. Transfer is only between wallets of the same tenant.
3. `idempotency_key` is optional — if not provided, the request is always processed.
4. Amounts must be positive integers (minimum 1 minor unit).
5. The `balance` field is updated alongside the ledger for fast balance queries.
