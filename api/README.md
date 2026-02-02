# Grey TG API

FastAPI + uvicorn backend with SQLAlchemy 2 (Postgres), CORS for `http://localhost:5173`, and `.env` loading.

## Requirements

- Python 3.10+
- Postgres (via Docker, see root `docker-compose.yml`)

## Setup

1. **Start Postgres** (from repo root):

   ```bash
   docker compose up -d
   ```

2. **Copy env** (from `api/`):

   ```bash
   cp .env.example .env
   ```

   Edit `.env` if your Postgres URL differs. Default: `postgresql+psycopg://postgres:postgres@localhost:5432/greytg`.

3. **Create venv and install**:

   ```bash
   cd api
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```

   **Generate `SESSION_ENC_KEY`** (run with the same venv active):

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Add the output to `.env` as `SESSION_ENC_KEY=...`.

## Run

```bash
uvicorn main:app --reload --port 8000
```

- API: http://localhost:8000  
- Docs: http://localhost:8000/docs  
- Health: `GET /health` → `{"ok": true}`

## Structure

- `main.py` — App, CORS, lifespan (init DB, start/stop dispatchers), `/`, `/health`; mounts `tenants`, `tenant_auth`, `tenant_messages`, `tenant_callbacks`
- `routers/tenants.py` — `GET /tenants`, `GET /tenants/{id}`, `POST /tenants` (create: name, callback_url)
- `routers/tenant_auth.py` — `GET /status`, `POST /auth/start`, `POST /auth/verify`, `POST /logout`; starts dispatcher on verify if `callback_url` set, stops on logout
- `routers/tenant_messages.py` — `POST /messages/send` (rate-limited)
- `routers/tenant_callbacks.py` — `POST /tenants/{id}/callback/test` (POST test payload to tenant callback_url)
- `routers/dev_callback_receiver.py` — `POST`/`GET` `/dev/callback-receiver` (in-memory payload store; mounted only when `DEV_CALLBACK_RECEIVER=1`)
- `callback_dispatch.py` — Inbound dispatcher: long-lived Telethon client per authorized tenant, POST to `callback_url` on `NewMessage(incoming=True)`; HMAC signing, retries; see module docstring for MTProto vs webhooks
- `rate_limit.py` — Per-tenant in-memory rate limiter; see module docstring for why
- `schemas.py` — Pydantic models for auth and send-message request/response
- `config.py` — `load_dotenv()`, `DATABASE_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `SESSION_ENC_KEY`, `CALLBACK_SIGNING_SECRET`, `DEV_CALLBACK_RECEIVER`
- `database.py` — SQLAlchemy engine, `init_db()`, `get_session()`
- `models/tenant.py` — **Tenant**: `id`, `name`, `callback_url`, `created_at`
- `models/tenant_auth.py` — **TenantAuth** (1:1 with Tenant): Telethon session storage per tenant
- `session_crypto.py` — Fernet encrypt/decrypt for session strings (`SESSION_ENC_KEY`)
- `telethon_manager.py` — `build_client(tenant_id)`, `save_session(tenant_id, client)`; see module docstring for why sessions = passwords and DB storage for multi-tenancy
- `requirements.txt` — FastAPI, uvicorn, sqlalchemy, psycopg[binary], python-dotenv, cryptography, telethon, httpx

Uses **psycopg 3** (`postgresql+psycopg://`) for Postgres. No build tools needed; installs from wheels.

## Tenant table

| Column        | Type        | Notes                          |
|---------------|-------------|--------------------------------|
| `id`          | UUID        | PK, default `uuid4`            |
| `name`        | VARCHAR     | Required                       |
| `callback_url`| VARCHAR     | Nullable                       |
| `created_at`  | TIMESTAMP   | Default `now()`                |

## TenantAuth table (Telethon session storage)

| Column         | Type      | Notes                                |
|----------------|-----------|--------------------------------------|
| `id`           | UUID      | PK                                    |
| `tenant_id`    | UUID      | FK → Tenant, unique                   |
| `phone`        | VARCHAR   | Nullable; set after login             |
| `session_string` | TEXT    | Encrypted (Fernet); never log or expose |
| `authorized`   | BOOLEAN   | Default false                         |
| `last_error`   | TEXT      | Nullable; last auth error for status  |
| `updated_at`   | TIMESTAMP | Default / on update                   |

**Env:** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (from [my.telegram.org](https://my.telegram.org)), `SESSION_ENC_KEY` (Fernet key; generate *after* `pip install -r requirements.txt` with venv active, then add to `.env`), `CALLBACK_SIGNING_SECRET` (for inbound callback `X-Signature` HMAC; optional but recommended).

**Why sessions = passwords, why DB for multi-tenancy:** see `telethon_manager` module docstring.

Tables are created on app startup via `init_db()`.

## Tenant CRUD

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants` | List all tenants |
| `GET` | `/tenants/{id}` | Get one tenant |
| `POST` | `/tenants` | Create tenant. Body: `{ "name": "...", "callback_url": "..."? }` |

## Tenant auth endpoints

All under `/tenants/{tenant_id}`. **Tenant isolation:** each request uses only that `tenant_id`; sessions are never reused across tenants.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants/{id}/status` | `{ authorized, phone?, last_error? }` |
| `POST` | `/tenants/{id}/auth/start` | Body: `{ "phone": "+..." }`. Sends OTP via `send_code_request`. |
| `POST` | `/tenants/{id}/auth/verify` | Body: `{ "phone", "code", "password"? }`. Sign-in; handle 2FA if `password` provided. |
| `POST` | `/tenants/{id}/logout` | `log_out()` and clear stored session. |

**OTP vs Telegram 2FA:**  
- **OTP:** One-time code Telegram sends (SMS or in-app) when you request login. Required every time.  
- **2FA (cloud password):** Optional extra password (Settings → Privacy → Two-Step Verification). If enabled, send `password` in `/auth/verify` after the OTP.

**Error responses:**  
- `2fa_required` (403): 2FA enabled; retry with `password` in body.  
- `invalid_code` (400): Wrong or expired OTP.  
- `code_expired` (400): Request a new code via `/auth/start`.  
- `flood_wait` (429): `retry_after_seconds` in body; wait before retrying.

## Send message

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tenants/{id}/messages/send` | Body: `{ "peer": "...", "text": "...", "allow_import_contact": true }`. Resolve peer, send message. |

**Peer:** `"me"` (Saved Messages), `@username`, numeric user/chat id, or **phone number** in E.164 (e.g. `+79001234567`). Resolved via `peer_resolver.resolve_peer`; response includes `peer_resolved`, `message_id`, `date` (ISO).

**Phone resolution:** Telethon can send to a phone only if that user is in the account's contacts (or we import the contact first). We first try `get_entity(phone)` (existing contact). If not found and `allow_import_contact` is true, we call `contacts.ImportContactsRequest`; if import returns no users (number not on Telegram or privacy), we return 400 `PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM`. If `allow_import_contact` is false and phone not in contacts, we return 400 `PHONE_NOT_IN_CONTACTS`. **MVP:** Imported numbers may remain in the account's contacts; we do not delete them after send.

**Structured errors:** `invalid_phone` (400), `peer_not_found` / `user_not_found` (400), `PHONE_NOT_IN_CONTACTS` (400), `PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM` (400), `flood_wait` (429 with `retry_after_seconds`).

**Rate limiting (per tenant):** In-memory sliding window (see `rate_limit` module). Default 10 requests / 60 s per tenant. Returns 429 `rate_limited` with `retry_after_seconds` when exceeded.

**Why rate limiting:**  
- Telegram enforces flood limits; too many requests → `FloodWaitError` and blocked client.  
- Prevents one tenant from exhausting capacity and affecting others.  
- Mitigates retry loops and abuse.  
- Per-tenant keeps isolation; one tenant cannot starve others.

MVP uses in-memory limits (single process). For multi-worker production, use Redis or similar.

## Send read receipt

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tenants/{id}/messages/read-receipt` | Body: `{ "peer": "...", "max_id": <int> }`. Mark messages in chat as read up to `max_id` (sends read receipt to senders). |

**Peer:** Same as send—`"me"`, @username, numeric user/chat id, or phone E.164. Use `chat_id` from the inbound callback as `peer` when acknowledging received messages (e.g. `peer` = `message.chat_id`).

**max_id:** Last message ID to mark as read; all messages with `id <= max_id` in that chat are marked read. Use `message_id` from the callback, or the latest ID you've processed when batching.

**Rate limiting:** Same per-tenant limit as send. Returns 401 if tenant not authorized, 429 on rate limit or Telegram `flood_wait`.

**Use case:** After processing an incoming message from the callback, call this endpoint with `peer` = `message.chat_id` and `max_id` = `message.message_id` to send a read receipt so the sender sees the messages as read.

## Inbound message dispatch

When a tenant is **authorized** and has a **`callback_url`**, a background task keeps a Telethon client connected, listens for `NewMessage(incoming=True)`, and POSTs each event to `callback_url`.

**Payload (JSON):**

```json
{
  "tenant_id": "<uuid>",
  "event": "message",
  "message": {
    "chat_id": 123,
    "message_id": 456,
    "sender_id": 789,
    "sender_username": "user",
    "text": "...",
    "date": "2025-01-25T12:00:00+00:00"
  }
}
```

Use `chat_id` and `message_id` with `POST /tenants/{id}/messages/read-receipt` (`peer` = `chat_id`, `max_id` = `message_id`) to send read receipts for incoming messages.

**`X-Signature` header:** HMAC-SHA256 of the raw JSON body using `CALLBACK_SIGNING_SECRET`. Format: `sha256=<hex>`. Customers can verify authenticity by recomputing `HMAC-SHA256(body, secret)` and comparing. If `CALLBACK_SIGNING_SECRET` is unset, the header is omitted.

**Retries:** On 5xx, we retry with exponential backoff (1s, 2s, 4s, …). Drop after 5 attempts; log failures.

**Lifecycle:** Dispatcher starts on app startup for all authorized tenants with `callback_url`, and after `POST /auth/verify` when the tenant has `callback_url`. It stops on `POST /logout` and on app shutdown.

**Test callback:** `POST /tenants/{id}/callback/test` sends a test message payload to the tenant's `callback_url` (single attempt). Returns 400 if no `callback_url` or send failed.

**Why MTProto doesn't use Telegram webhooks for user accounts:**  
The Bot API supports webhooks: you set a URL, Telegram POSTs updates there. That applies only to **bots**. User (client) accounts use the **MTProto** API. MTProto is session-based and connection-oriented: you keep a long-lived connection, and updates (including new messages) are pushed over it. There is no webhook concept for user accounts—you must stay connected and handle updates in your client.

**Why we implement our own callback:**  
We use Telethon (MTProto) to act as the user's Telegram client. We receive updates via the live connection. To integrate with tenants' systems (notifications, automation, etc.), we need to push those events somewhere. So we implement a callback: when we receive an incoming message, we POST it to the tenant's `callback_url`. That gives tenants a webhook-like experience (HTTP POSTs for new messages) while we handle the MTProto connection and update loop ourselves.

**Why customers host their own callback URL in production:**  
- **Control and ownership:** They run the receiver on their infra, so they control scaling, uptime, and deployment. No dependency on our dev or shared endpoints.
- **Security:** They verify `X-Signature` with their shared secret, process sensitive data behind their firewall, and enforce their own auth and rate limits.
- **Integration:** Callbacks feed directly into their pipelines (queues, DBs, internal APIs). Hosting it themselves avoids extra hops and keeps data in their environment.
- **Compliance:** Data residency, audit, and regulatory requirements are easier when the receiver runs in their own stack.

The dev callback receiver is for local testing only; production tenants use their own URL.

## Dev callback receiver

When `DEV_CALLBACK_RECEIVER=1` in `.env`, the following routes are mounted:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/dev/callback-receiver` | Store request body (JSON) in memory. Returns `{"ok":"stored"}`. |
| `GET` | `/dev/callback-receiver` | Return recent stored payloads (newest first). Each entry: `{ "received_at": "...", "payload": {...} }`. |

Set a tenant's `callback_url` to `http://localhost:8000/dev/callback-receiver` and use "Send test callback" or receive real inbound messages to capture payloads. The admin UI displays them when the dev receiver is enabled. Not mounted when `DEV_CALLBACK_RECEIVER` is unset (production).
