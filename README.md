# Grey TG

Minimal monorepo: FastAPI backend, React + Vite admin frontend, and Postgres via Docker.

## Structure

```
grey TG/
├── api/                 # FastAPI backend (see api/README.md)
│   ├── main.py          # App, CORS, lifespan (init DB), /, /health
│   ├── config.py       # .env loading, DATABASE_URL
│   ├── database.py     # SQLAlchemy engine, init_db, get_session
│   ├── models/
│   │   ├── tenant.py    # Tenant: id, name, callback_url, created_at
│   │   └── tenant_auth.py # TenantAuth: Telethon session per tenant (encrypted)
│   ├── session_crypto.py # Fernet encrypt/decrypt for session strings
│   ├── telethon_manager.py # build_client, save_session, clear_session, set_last_error
│   ├── callback_dispatch.py # Inbound: long-lived client, POST to callback_url, HMAC, retries
│   ├── routers/
│   │   ├── tenants.py        # GET/POST /tenants, GET /tenants/{id}
│   │   ├── tenant_auth.py    # GET /status, POST /auth/start, /auth/verify, /logout
│   │   ├── tenant_messages.py # POST /messages/send (rate-limited)
│   │   ├── tenant_callbacks.py # POST /tenants/{id}/callback/test
│   │   └── dev_callback_receiver.py # POST/GET /dev/callback-receiver (when DEV_CALLBACK_RECEIVER=1)
│   ├── rate_limit.py    # Per-tenant in-memory rate limiter
│   ├── schemas.py       # Pydantic auth + send-message request/response
│   ├── requirements.txt
│   └── .env.example     # DATABASE_URL, TELEGRAM_*, SESSION_ENC_KEY
├── web/                 # React + Vite admin UI
│   ├── src/
│   │   ├── App.tsx      # Router: /, /tenants/:id
│   │   ├── api.ts       # VITE_API_URL, fetch helpers
│   │   ├── pages/
│   │   │   ├── TenantList.tsx   # List + create tenant
│   │   │   └── TenantDetail.tsx # Auth, send message, test callback
│   │   ├── main.tsx
│   │   └── index.css
│   ├── .env.example     # VITE_API_URL
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml   # Postgres only
├── .gitignore
└── README.md
```

- **`/api`**: FastAPI + uvicorn, CORS for `http://localhost:5173`, `.env` loading, SQLAlchemy 2 → Postgres. Tenant table on startup. See `api/README.md`.
- **`/web`**: Vite dev server on 5173. Admin UI: tenant list + create, tenant detail (auth OTP/verify, status, logout, send message, send test callback), dev callback receiver payloads when enabled. Uses `VITE_API_URL` (default `http://localhost:8000`).
- **`docker-compose`**: Runs Postgres 16 on `localhost:5432`, DB `greytg`, user/pass `postgres/postgres`.

## Dev run

1. **Postgres** (required for API):

   ```bash
   docker compose up -d
   ```

2. **API**:

   ```bash
   cd api
   cp .env.example .env    # adjust DATABASE_URL if needed
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   uvicorn main:app --reload --port 8000
   ```

   Details and Tenant table docs: `api/README.md`.

3. **Web** (new terminal):

   ```bash
   cd web
   cp .env.example .env   # optional; VITE_API_URL defaults to http://localhost:8000
   npm install
   npm run dev
   ```

4. Open **http://localhost:5173** for the admin UI, **http://localhost:8000/docs** for API docs.

## Quick checks

- API root: `curl http://localhost:8000/`
- API health: `curl http://localhost:8000/health` → `{"ok": true}`
- DB connection string (when using Postgres): `postgresql+psycopg://postgres:postgres@localhost:5432/greytg`
