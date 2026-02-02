import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  checkApiConnection,
  type Tenant,
  type TenantStatus,
  type SendMessageRes,
} from "../api";

export default function TenantDetail() {
  const { id } = useParams<{ id: string }>();
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [status, setStatus] = useState<TenantStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [authStep, setAuthStep] = useState<"idle" | "phone" | "code">("idle");
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [codeHint, setCodeHint] = useState<string | null>(null);
  const [codeTimeoutSeconds, setCodeTimeoutSeconds] = useState(0);

  const [peer, setPeer] = useState("me");
  const [text, setText] = useState("");
  const [allowImportContact, setAllowImportContact] = useState(true);
  const [sendLoading, setSendLoading] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<SendMessageRes | null>(null);

  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [testOk, setTestOk] = useState(false);

  const [apiConnected, setApiConnected] = useState<boolean | null>(null);

  const base = `/tenants/${id}`;

  const refresh = useCallback(() => {
    if (!id) return;
    setError(null);
    Promise.all([
      api<Tenant>(`/tenants/${id}`).then(setTenant),
      api<TenantStatus>(`${base}/status`).then(setStatus).catch(() => setStatus(null)),
    ]).catch((e) => setError(String(e))).finally(() => setLoading(false));
  }, [id, base]);

  useEffect(() => {
    setLoading(true);
    refresh();
    checkApiConnection()
      .then((r) => setApiConnected(r.connected))
      .catch(() => setApiConnected(false));
  }, [refresh]);

  // Poll status when on code step to update cooldown_seconds for Resend button
  useEffect(() => {
    if (authStep !== "code" || !id) return;
    const t = setInterval(() => {
      api<TenantStatus>(`${base}/status`).then(setStatus).catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [authStep, id, base]);

  async function handleStartOtp(e: React.FormEvent) {
    e.preventDefault();
    e.stopPropagation();
    setAuthError(null);
    setAuthLoading(true);
    try {
      const response = await api<{
        ok: boolean;
        message: string;
        delivery?: string;
        timeout_seconds?: number;
        hint?: string;
      }>(`${base}/auth/start`, { method: "POST", body: { phone: phone.trim() } });
      setCodeHint(response.hint ?? null);
      setCodeTimeoutSeconds(response.timeout_seconds ?? 0);
      setAuthStep("code");
      setAuthError(null);
      refresh();
    } catch (e) {
      setAuthError(String(e));
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleResend(e: React.FormEvent) {
    e.preventDefault();
    e.stopPropagation();
    setAuthError(null);
    setAuthLoading(true);
    try {
      const response = await api<{
        ok: boolean;
        message: string;
        delivery?: string;
        timeout_seconds?: number;
        hint?: string;
      }>(`${base}/auth/resend`, { method: "POST", body: {} });
      setCodeHint(response.hint ?? codeHint);
      setCodeTimeoutSeconds(response.timeout_seconds ?? 0);
      setAuthError(null);
      refresh();
    } catch (e) {
      setAuthError(String(e));
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault();
    setAuthError(null);
    setAuthLoading(true);
    try {
      await api(`${base}/auth/verify`, {
        method: "POST",
        body: {
          phone: phone.trim(),
          code: code.trim(),
          password: password.trim() || undefined,
        },
      });
      setAuthStep("idle");
      setCode("");
      setPassword("");
      refresh();
    } catch (e) {
      setAuthError(String(e));
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleLogout() {
    setAuthError(null);
    setAuthLoading(true);
    try {
      await api(`${base}/logout`, { method: "POST" });
      setAuthStep("idle");
      setPhone("");
      setCode("");
      setPassword("");
      setCodeHint(null);
      setCodeTimeoutSeconds(0);
      refresh();
    } catch (e) {
      setAuthError(String(e));
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    setSendError(null);
    setSendResult(null);
    setSendLoading(true);
    try {
      const res = await api<SendMessageRes>(`${base}/messages/send`, {
        method: "POST",
        body: {
          peer: peer.trim(),
          text: text.trim(),
          allow_import_contact: allowImportContact,
        },
      });
      setSendResult(res);
      setText("");
    } catch (e) {
      setSendError(String(e));
    } finally {
      setSendLoading(false);
    }
  }

  async function handleTestCallback() {
    setTestError(null);
    setTestOk(false);
    setTestLoading(true);
    try {
      await api(`${base}/callback/test`, { method: "POST" });
      setTestOk(true);
    } catch (e) {
      setTestError(String(e));
    } finally {
      setTestLoading(false);
    }
  }

  if (!id) return <p className="error">Missing tenant ID</p>;
  if (loading && !tenant) return <main className="page"><p>Loading…</p></main>;
  if (error && !tenant) {
    return (
      <main className="page">
        <p className="error">{error}</p>
        <Link to="/">← Tenants</Link>
      </main>
    );
  }
  if (!tenant) return <main className="page"><p>Tenant not found.</p></main>;

  return (
    <main className="page">
      <p><Link to="/">← Tenants</Link></p>
      <h1>{tenant.name}</h1>
      {tenant.callback_url && <p className="muted">Callback: {tenant.callback_url}</p>}
      {apiConnected === false && (
        <div style={{ padding: "1rem", marginBottom: "1rem", backgroundColor: "#fee", border: "1px solid #fcc", borderRadius: "4px" }}>
          <strong>⚠️ API Server Not Connected</strong>
          <p style={{ margin: "0.5rem 0 0 0", fontSize: "0.9rem" }}>
            Cannot reach API at <code>http://localhost:8000</code>. Make sure the API server is running:
            <br />
            <code style={{ display: "block", marginTop: "0.5rem", padding: "0.5rem", backgroundColor: "#f5f5f5" }}>
              cd api<br />
              uvicorn main:app --reload --port 8000
            </code>
          </p>
        </div>
      )}

      <section className="card">
        <h2>Status</h2>
        {status === null && <p>Could not load status.</p>}
        {status && (
          <p>
            {status.authorized ? (
              <span className="ok">Authorized</span>
            ) : (
              <span className="muted">Not authorized</span>
            )}
            {status.phone && ` · ${status.phone}`}
            {status.last_error && <span className="error"> · {status.last_error}</span>}
          </p>
        )}
      </section>

      <section className="card">
        <h2>Auth</h2>
        {authStep === "idle" && (
          <div>
            <div className="field">
              <label htmlFor="phone">Phone (E.164)</label>
              <input
                id="phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+79001234567"
                required
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleStartOtp(e as unknown as React.FormEvent);
                  }
                }}
              />
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                Use E.164: +&lt;country&gt;&lt;number&gt; (e.g. +79001234567). No spaces or dashes.
              </p>
            </div>
            {authError && <p className="error">{authError}</p>}
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void handleStartOtp(e as unknown as React.FormEvent);
              }}
              disabled={authLoading || !phone.trim()}
            >
              {authLoading ? "Sending…" : "Start OTP"}
            </button>
          </div>
        )}
        {authStep === "code" && (
          <form onSubmit={handleVerify}>
            <div className="field">
              <label htmlFor="code">Code</label>
              <input
                id="code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="12345"
                required
                autoFocus
              />
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                {codeHint ? (
                  <>📱 {codeHint}</>
                ) : (
                  "📱 Check your Telegram app (Saved Messages / login message) or SMS for the code."
                )}
                <br />
                ⚠️ Enter the code immediately. Codes can expire quickly.
              </p>
            </div>
            <div className="field">
              <label htmlFor="password">2FA password (optional)</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="…"
              />
            </div>
            {authError && <p className="error">{authError}</p>}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button type="submit" disabled={authLoading || !code.trim()}>
                {authLoading ? "Verifying…" : "Verify"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  void handleResend(e as unknown as React.FormEvent);
                }}
                disabled={authLoading || (status?.cooldown_seconds ?? 0) > 0}
                title={(status?.cooldown_seconds ?? 0) > 0 ? `Wait ${status?.cooldown_seconds}s` : undefined}
              >
                {(status?.cooldown_seconds ?? 0) > 0
                  ? `Resend (wait ${status.cooldown_seconds}s)`
                  : "Resend code"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setAuthStep("idle");
                  setAuthError(null);
                  setCodeHint(null);
                  setCodeTimeoutSeconds(0);
                }}
                disabled={authLoading}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
        {status?.authorized && (
          <p style={{ marginTop: "1rem" }}>
            <button type="button" className="danger" onClick={handleLogout} disabled={authLoading}>
              Logout
            </button>
          </p>
        )}
      </section>

      <section className="card">
        <h2>Test callback</h2>
        <p className="muted">POST a test payload to the tenant&apos;s callback_url.</p>
        {!tenant.callback_url && <p className="muted">Set callback_url on the tenant to enable.</p>}
        {testError && <p className="error">{testError}</p>}
        {testOk && <p className="ok">Test callback sent.</p>}
        <button
          type="button"
          onClick={handleTestCallback}
          disabled={testLoading || !tenant.callback_url}
        >
          {testLoading ? "Sending…" : "Send test callback"}
        </button>
      </section>

      {status?.authorized && (
        <section className="card">
          <h2>Send message</h2>
          <form onSubmit={handleSend}>
            <div className="field">
              <label htmlFor="peer">Peer</label>
              <input
                id="peer"
                value={peer}
                onChange={(e) => setPeer(e.target.value)}
                placeholder="me, @username, id, or +79001234567"
                required
              />
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                Use &quot;me&quot; for Saved Messages, @username, numeric ID, or phone (E.164, e.g. +79001234567).
                <br />
                Phone: must be in contacts, or we import it when &quot;Allow import contact&quot; is on. Otherwise you get PHONE_NOT_IN_CONTACTS or PHONE_NOT_IN_CONTACTS_OR_NOT_ON_TELEGRAM.
              </p>
            </div>
            <div className="field">
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <input
                  type="checkbox"
                  checked={allowImportContact}
                  onChange={(e) => setAllowImportContact(e.target.checked)}
                />
                Allow import contact (phone not in contacts)
              </label>
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                If on, we import the phone as contact when not found; the number may remain in Telegram contacts (MVP).
              </p>
            </div>
            <div className="field">
              <label htmlFor="text">Text</label>
              <input
                id="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Message"
                required
              />
            </div>
            {sendError && <p className="error">{sendError}</p>}
            {sendResult && <p className="ok">Sent: {sendResult.peer_resolved} · #{sendResult.message_id}</p>}
            <button type="submit" disabled={sendLoading}>
              {sendLoading ? "Sending…" : "Send"}
            </button>
          </form>
        </section>
      )}
    </main>
  );
}
