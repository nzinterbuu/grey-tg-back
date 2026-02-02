import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE, api, apiDevCallbackPayloads, type CallbackPayloadEntry, type Tenant } from "../api";

export default function TenantList() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [devPayloads, setDevPayloads] = useState<CallbackPayloadEntry[] | null>(null);
  const [devEnabled, setDevEnabled] = useState<boolean | null>(null);
  const [devLoading, setDevLoading] = useState(false);
  const [devError, setDevError] = useState<string | null>(null);

  const fetchDevPayloads = useCallback(async () => {
    setDevError(null);
    setDevLoading(true);
    try {
      const r = await apiDevCallbackPayloads();
      setDevEnabled(r.enabled);
      setDevPayloads(r.enabled ? r.payloads : []);
    } catch (e) {
      setDevError(String(e));
      setDevEnabled(null);
      setDevPayloads(null);
    } finally {
      setDevLoading(false);
    }
  }, []);

  useEffect(() => {
    api<Tenant[]>("/tenants")
      .then(setTenants)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchDevPayloads();
  }, [fetchDevPayloads]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);
    setCreating(true);
    try {
      const t = await api<Tenant>("/tenants", {
        method: "POST",
        body: { name: name.trim(), callback_url: callbackUrl.trim() || null },
      });
      setTenants((prev) => [t, ...prev]);
      setName("");
      setCallbackUrl("");
    } catch (e) {
      setCreateError(String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="page">
      <h1>Tenants</h1>
      <section className="card">
        <h2>Create tenant</h2>
        <form onSubmit={handleCreate}>
          <div className="field">
            <label htmlFor="create-name">Name</label>
            <input
              id="create-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="My tenant"
            />
          </div>
          <div className="field">
            <label htmlFor="create-callback">Callback URL</label>
            <input
              id="create-callback"
              type="url"
              value={callbackUrl}
              onChange={(e) => setCallbackUrl(e.target.value)}
              placeholder="https://..."
            />
          </div>
          {createError && <p className="error">{createError}</p>}
          <button type="submit" disabled={creating}>
            {creating ? "Creating…" : "Create"}
          </button>
        </form>
      </section>

      <section className="card">
        <h2>List</h2>
        {loading && <p>Loading…</p>}
        {error && <p className="error">{error}</p>}
        {!loading && !error && tenants.length === 0 && <p>No tenants yet.</p>}
        {!loading && !error && tenants.length > 0 && (
          <ul className="tenant-list">
            {tenants.map((t) => (
              <li key={t.id}>
                <Link to={`/tenants/${t.id}`}>{t.name}</Link>
                {t.callback_url && (
                  <span className="muted"> · {t.callback_url}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Dev callback receiver</h2>
        <p className="muted">
          Set a tenant&apos;s callback_url to <code>{API_BASE}/dev/callback-receiver</code> and
          enable <code>DEV_CALLBACK_RECEIVER=1</code> to capture payloads here.
        </p>
        {devEnabled === null && devLoading && <p className="muted">Loading…</p>}
        {devEnabled === false && (
          <p className="muted">
            Not enabled. Set <code>DEV_CALLBACK_RECEIVER=1</code> in API <code>.env</code> and
            restart.
          </p>
        )}
        {devError && <p className="error">{devError}</p>}
        {devEnabled === true && (
          <>
            <button type="button" onClick={fetchDevPayloads} disabled={devLoading}>
              {devLoading ? "Loading…" : "Refresh"}
            </button>
            {devPayloads && devPayloads.length === 0 && <p className="muted">No payloads yet.</p>}
            {devPayloads && devPayloads.length > 0 && (
              <ul className="payload-list">
                {devPayloads.map((e, i) => (
                  <li key={i} className="payload-item">
                    <span className="muted">{e.received_at}</span>
                    <pre className="payload-json">{JSON.stringify(e.payload, null, 2)}</pre>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </main>
  );
}
