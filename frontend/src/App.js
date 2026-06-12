import React, { useState, useRef, useEffect } from "react";

/**
 * Branson Parts AI — Varner Parts
 * Redesigned UI: light, professional, fixed-bottom composer, compact results.
 * Backend data contract is unchanged (POST /api/chat).
 */

export default function BransonPartsAI() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversation, setConversation] = useState([]);
  const [hasStarted, setHasStarted] = useState(false);
  const chatEndRef = useRef(null);
  const inputRef = useRef(null);

  const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:5000";

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation, loading]);

  const sendMessage = async (messageText) => {
    const userMessage = (messageText || query).trim();
    if (!userMessage || loading) return;

    setHasStarted(true);
    setQuery("");
    setLoading(true);

    setConversation((prev) => [
      ...prev,
      { id: Date.now(), type: "user", text: userMessage },
    ]);

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: userMessage, use_web_search: true }),
      });

      if (!response.ok)
        throw new Error("The search service didn't respond. Try again.");
      const data = await response.json();

      setConversation((prev) => [
        ...prev,
        { id: Date.now() + 1, type: "ai", data },
      ]);
    } catch (err) {
      setConversation((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          type: "error",
          text: err.message || "Something went wrong. Try again.",
        },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    sendMessage();
  };

  return (
    <div className="vp-app">
      <Styles />

      <header className="vp-header">
        <div className="vp-header-inner">
          <div className="vp-brand">
            <span className="vp-mark" aria-hidden>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
                <path
                  d="M5 16.5a2.5 2.5 0 105 0 2.5 2.5 0 00-5 0zM15.5 17a2 2 0 104 0 2 2 0 00-4 0z"
                  stroke="currentColor"
                  strokeWidth="1.6"
                />
                <path
                  d="M7.5 14V8.5h4l1.5 3h4.5V17M11.5 8.5l1 2.5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <div className="vp-brand-text">
              <span className="vp-brand-name">Varner Parts</span>
              <span className="vp-brand-sub">Branson tractor parts finder</span>
            </div>
          </div>
          <div className="vp-status">
            <span className="vp-dot" />
            Live catalog
          </div>
        </div>
      </header>

      <main className="vp-main">
        <div className="vp-thread">
          {!hasStarted && <Welcome onPick={(q) => sendMessage(q)} />}

          {conversation.map((m) => (
            <Message key={m.id} message={m} />
          ))}

          {loading && <Typing />}

          <div ref={chatEndRef} />
        </div>
      </main>

      <div className="vp-composer">
        <form className="vp-composer-inner" onSubmit={handleSubmit}>
          {!hasStarted && (
            <div className="vp-chips" role="list">
              {[
                "Glow plug for Branson 2100",
                "Air filter for 2400",
                "Hydraulic filter",
                "Roof for 20 Series",
              ].map((c) => (
                <button
                  type="button"
                  key={c}
                  className="vp-chip"
                  onClick={() => sendMessage(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          )}
          <div className="vp-input-row">
            <input
              ref={inputRef}
              className="vp-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Describe the part, model, or paste a SKU…"
              disabled={loading}
              autoFocus
            />
            <button
              type="submit"
              className="vp-send"
              disabled={loading || !query.trim()}
              aria-label="Search"
            >
              {loading ? (
                <Spinner />
              ) : (
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none">
                  <path
                    d="M5 12h13M13 6l6 6-6 6"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
            </button>
          </div>
          <p className="vp-disclaimer">
            Matches are best-effort. Confirm fitment with your model before
            ordering.
          </p>
        </form>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Welcome                                                             */
/* ------------------------------------------------------------------ */

function Welcome({ onPick }) {
  return (
    <div className="vp-welcome">
      <div className="vp-welcome-mark" aria-hidden>
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none">
          <path
            d="M5 16.5a2.5 2.5 0 105 0 2.5 2.5 0 00-5 0zM15.5 17a2 2 0 104 0 2 2 0 00-4 0z"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path
            d="M7.5 14V8.5h4l1.5 3h4.5V17M11.5 8.5l1 2.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <h1 className="vp-welcome-title">Find the right Branson part</h1>
      <p className="vp-welcome-sub">
        Ask in plain words, search by model, or paste a part number. Every
        result is checked against the live varnerparts.com catalog.
      </p>
      <div className="vp-welcome-grid">
        {[
          { k: "By model", v: "Glow plug for Branson 2100" },
          { k: "By series", v: "Tractor roof for 20 Series" },
          { k: "By part", v: "Hydraulic filter element" },
          { k: "By SKU", v: "Look up HT17160000A3" },
        ].map((x) => (
          <button
            key={x.v}
            className="vp-welcome-card"
            onClick={() => onPick(x.v)}
          >
            <span className="vp-welcome-card-k">{x.k}</span>
            <span className="vp-welcome-card-v">{x.v}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Message router                                                      */
/* ------------------------------------------------------------------ */

function Message({ message }) {
  if (message.type === "user") {
    return (
      <div className="vp-row vp-row-user">
        <div className="vp-user-bubble">{message.text}</div>
      </div>
    );
  }

  if (message.type === "error") {
    return (
      <div className="vp-row">
        <div className="vp-error">
          <strong>Couldn't complete that.</strong> {message.text}
        </div>
      </div>
    );
  }

  const data = message.data;

  if (data.is_conversational) {
    return (
      <div className="vp-row">
        <div className="vp-assistant-card">
          <p className="vp-assistant-text">{data.response}</p>
          {data.suggestions?.length > 0 && (
            <div className="vp-suggest-row">
              {data.suggestions.map((s, i) => (
                <span key={i} className="vp-suggest">
                  {s}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="vp-row">
      <SearchResults data={data} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Search results                                                      */
/* ------------------------------------------------------------------ */

function SearchResults({ data }) {
  const results = data.results || [];
  const confidence = data.search_confidence ?? 0;

  if (results.length === 0) {
    return (
      <div className="vp-assistant-card">
        <p className="vp-assistant-text">
          {data.message || "No matches found."}
        </p>
        {data.suggestions?.length > 0 && (
          <ul className="vp-empty-list">
            {data.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const tier = confidence >= 90 ? "high" : confidence >= 70 ? "mid" : "low";
  const tierLabel =
    tier === "high"
      ? "Best match"
      : tier === "mid"
        ? "Likely matches"
        : "Possible matches";

  const primary = results[0];
  const alternates = results.slice(1);

  return (
    <div className="vp-results">
      {data.response && (
        <p className="vp-assistant-text" style={{ margin: "0 0 6px" }}>
          {data.response}
        </p>
      )}
      <div className={`vp-confidence vp-confidence-${tier}`}>
        <span className="vp-confidence-dot" />
        <span className="vp-confidence-label">{tierLabel}</span>
        <span className="vp-confidence-pct">{confidence}% confidence</span>
        {data.web_search_used && (
          <span className="vp-confidence-web">web-checked</span>
        )}
      </div>

      <PrimaryResult product={primary} interpretation={data.interpretation} />

      {alternates.length > 0 && (
        <div className="vp-alts">
          <div className="vp-alts-label">
            Other options ({alternates.length})
          </div>
          {alternates.map((p, i) => (
            <AlternateRow key={i} product={p} />
          ))}
        </div>
      )}

      {data.web_search_used &&
        data.interpretation?.web_research?.sources?.length > 0 && (
          <WebSources sources={data.interpretation.web_research.sources} />
        )}
    </div>
  );
}

function PrimaryResult({ product, interpretation }) {
  const [showWhy, setShowWhy] = useState(false);
  const [copied, setCopied] = useState(false);
  const inStock = Number(product.inventory) > 0;
  const price = Number(product.price || 0);

  const modelTags = (product.tags || [])
    .filter((t) => t && t.toLowerCase().includes("branson"))
    .map((t) => t.replace(/branson/i, "").trim())
    .filter((t) => t.length > 0)
    .slice(0, 8);

  const copySku = () => {
    navigator.clipboard?.writeText(product.sku || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="vp-card">
      <div className="vp-card-head">
        <div className="vp-card-head-main">
          <h3 className="vp-card-title">{product.title}</h3>
          <div className="vp-meta-row">
            <button className="vp-sku" onClick={copySku} title="Copy SKU">
              <span className="vp-sku-label">SKU</span>
              <span className="vp-sku-val">{product.sku || "—"}</span>
              <span className="vp-sku-copy">{copied ? "Copied" : "Copy"}</span>
            </button>
            {product.is_web_verified && (
              <span className="vp-badge vp-badge-web">Web verified</span>
            )}
            {product.is_live_data ? (
              <span className="vp-badge vp-badge-live">Live data</span>
            ) : (
              <span className="vp-badge vp-badge-sample">Sample data</span>
            )}
          </div>
        </div>
        <div className="vp-card-price">
          <div className="vp-price">${price.toFixed(2)}</div>
          <div
            className={`vp-stock ${inStock ? "vp-stock-in" : "vp-stock-out"}`}
          >
            {inStock ? `${product.inventory} in stock` : "Out of stock"}
          </div>
        </div>
      </div>

      {modelTags.length > 0 && (
        <div className="vp-fitment">
          <span className="vp-fitment-label">Fits</span>
          <div className="vp-fitment-tags">
            {modelTags.map((t, i) => (
              <span key={i} className="vp-fit-tag">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="vp-card-actions">
        {product.product_url ? (
          <a
            className="vp-btn-primary"
            href={product.product_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            View on varnerparts.com
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none">
              <path
                d="M7 17L17 7M17 7H9M17 7v8"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </a>
        ) : (
          <span className="vp-btn-disabled">Link unavailable</span>
        )}
        <button
          className="vp-btn-ghost"
          onClick={() => setShowWhy((v) => !v)}
          aria-expanded={showWhy}
        >
          {showWhy ? "Hide details" : "Why this match"}
        </button>
      </div>

      {showWhy && (
        <div className="vp-why">
          <div className="vp-why-grid">
            {interpretation?.equipment && (
              <Detail k="Equipment" v={interpretation.equipment} />
            )}
            {interpretation?.part_type && (
              <Detail k="Part type" v={interpretation.part_type} />
            )}
            {product.weight && (
              <Detail
                k="Weight"
                v={`${product.weight} ${product.weight_unit || ""}`.trim()}
              />
            )}
            {product.type && <Detail k="Category" v={product.type} />}
          </div>
          {product.match_reason && (
            <div className="vp-why-reason">{product.match_reason}</div>
          )}
          {product.description && (
            <div
              className="vp-why-desc"
              dangerouslySetInnerHTML={{ __html: product.description }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function Detail({ k, v }) {
  return (
    <div className="vp-detail">
      <span className="vp-detail-k">{k}</span>
      <span className="vp-detail-v">{v}</span>
    </div>
  );
}

function AlternateRow({ product }) {
  const inStock = Number(product.inventory) > 0;
  const price = Number(product.price || 0);
  const content = (
    <>
      <div className="vp-alt-main">
        <div className="vp-alt-title">{product.title}</div>
        <div className="vp-alt-sku">{product.sku}</div>
      </div>
      <div className="vp-alt-side">
        <span className="vp-alt-price">${price.toFixed(2)}</span>
        <span
          className={`vp-alt-stock ${inStock ? "vp-stock-in" : "vp-stock-out"}`}
        >
          {inStock ? "In stock" : "Out"}
        </span>
      </div>
    </>
  );

  if (product.product_url) {
    return (
      <a
        className="vp-alt vp-alt-link"
        href={product.product_url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {content}
      </a>
    );
  }
  return <div className="vp-alt">{content}</div>;
}

function WebSources({ sources }) {
  const ranked = [...sources].sort(
    (a, b) => (b.is_varner ? 1 : 0) - (a.is_varner ? 1 : 0),
  );
  return (
    <div className="vp-sources">
      <div className="vp-sources-label">Sources</div>
      {ranked.slice(0, 4).map((s, i) => (
        <a
          key={i}
          className="vp-source"
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <span className="vp-source-title">{s.title}</span>
          {s.is_varner && <span className="vp-source-tag">varnerparts</span>}
        </a>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Bits                                                                */
/* ------------------------------------------------------------------ */

function Typing() {
  return (
    <div className="vp-row">
      <div className="vp-typing">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="vp-spin"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="3"
      />
      <path
        d="M21 12a9 9 0 00-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Styles                                                              */
/* ------------------------------------------------------------------ */

function Styles() {
  return (
    <style>{`
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap');

:root {
  --paper: #F6F7F4;
  --surface: #FFFFFF;
  --ink: #18211C;
  --muted: #61706A;
  --faint: #8A968F;
  --line: #E5E9E3;
  --line-soft: #EEF1EC;
  --green: #1E6B43;
  --green-700: #16512F;
  --green-soft: #EAF2EC;
  --amber: #A8631C;
  --amber-soft: #F6EEDF;
  --red: #B0413B;
  --red-soft: #F6E9E7;
  --blue: #2C5C8A;
  --blue-soft: #EAF0F6;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(24,33,28,.04), 0 8px 24px rgba(24,33,28,.05);
}

* { box-sizing: border-box; }

.vp-app {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: var(--ink);
  background: var(--paper);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  -webkit-font-smoothing: antialiased;
}

/* Header */
.vp-header {
  position: sticky; top: 0; z-index: 30;
  background: rgba(246,247,244,.85);
  backdrop-filter: saturate(160%) blur(10px);
  border-bottom: 1px solid var(--line);
}
.vp-header-inner {
  max-width: 820px; margin: 0 auto;
  padding: 12px 20px;
  display: flex; align-items: center; justify-content: space-between;
}
.vp-brand { display: flex; align-items: center; gap: 11px; }
.vp-mark {
  width: 36px; height: 36px; border-radius: 10px;
  display: grid; place-items: center;
  background: var(--green); color: #fff;
}
.vp-brand-text { display: flex; flex-direction: column; line-height: 1.15; }
.vp-brand-name { font-weight: 700; font-size: 15px; letter-spacing: -.01em; }
.vp-brand-sub { font-size: 12px; color: var(--muted); }
.vp-status {
  display: flex; align-items: center; gap: 7px;
  font-size: 12px; font-weight: 500; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line);
  padding: 6px 11px; border-radius: 999px;
}
.vp-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 0 3px var(--green-soft);
}

/* Main thread */
.vp-main { flex: 1; overflow-y: auto; }
.vp-thread {
  max-width: 820px; margin: 0 auto;
  padding: 28px 20px 40px;
  display: flex; flex-direction: column; gap: 18px;
}

.vp-row { display: flex; animation: vp-fade .35s ease both; }
.vp-row-user { justify-content: flex-end; }

@keyframes vp-fade {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}

/* User bubble */
.vp-user-bubble {
  background: var(--green); color: #fff;
  padding: 11px 16px; border-radius: 16px 16px 4px 16px;
  max-width: 78%; font-size: 15px; line-height: 1.45;
  box-shadow: var(--shadow);
}

/* Assistant conversational card */
.vp-assistant-card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 16px 18px;
  max-width: 78%; box-shadow: var(--shadow);
}
.vp-assistant-text { margin: 0; font-size: 15px; line-height: 1.55; color: var(--ink); }
.vp-suggest-row { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 13px; }
.vp-suggest {
  font-size: 12.5px; color: var(--green-700);
  background: var(--green-soft); border: 1px solid #D6E6DB;
  padding: 5px 10px; border-radius: 999px;
}
.vp-empty-list {
  margin: 12px 0 0; padding-left: 18px; color: var(--muted);
  font-size: 14px; line-height: 1.7;
}

.vp-error {
  background: var(--red-soft); border: 1px solid #EBC9C5;
  color: #7A2622; padding: 13px 16px; border-radius: 12px;
  font-size: 14px; max-width: 78%;
}

/* Results block */
.vp-results { width: 100%; display: flex; flex-direction: column; gap: 12px; }

.vp-confidence {
  display: flex; align-items: center; gap: 9px;
  font-size: 13px; padding: 2px 2px;
}
.vp-confidence-dot { width: 8px; height: 8px; border-radius: 50%; }
.vp-confidence-label { font-weight: 600; }
.vp-confidence-pct { color: var(--muted); }
.vp-confidence-web {
  margin-left: auto; font-size: 11px; font-weight: 600;
  color: var(--blue); background: var(--blue-soft);
  padding: 3px 9px; border-radius: 999px;
}
.vp-confidence-high .vp-confidence-dot { background: var(--green); }
.vp-confidence-high .vp-confidence-label { color: var(--green-700); }
.vp-confidence-mid .vp-confidence-dot { background: var(--amber); }
.vp-confidence-mid .vp-confidence-label { color: var(--amber); }
.vp-confidence-low .vp-confidence-dot { background: var(--faint); }
.vp-confidence-low .vp-confidence-label { color: var(--muted); }

/* Primary card */
.vp-card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 18px;
  box-shadow: var(--shadow);
}
.vp-card-head {
  display: flex; justify-content: space-between; gap: 16px; align-items: flex-start;
}
.vp-card-head-main { flex: 1; min-width: 0; }
.vp-card-title {
  margin: 0 0 9px; font-size: 17px; font-weight: 600;
  line-height: 1.35; letter-spacing: -.01em;
}
.vp-meta-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }

.vp-sku {
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--paper); border: 1px solid var(--line);
  border-radius: 8px; padding: 4px 4px 4px 9px; cursor: pointer;
  font-family: inherit;
}
.vp-sku-label { font-size: 10px; font-weight: 700; letter-spacing: .06em; color: var(--faint); }
.vp-sku-val { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; font-weight: 600; color: var(--ink); }
.vp-sku-copy {
  font-size: 11px; font-weight: 600; color: var(--green-700);
  background: var(--green-soft); padding: 3px 8px; border-radius: 5px;
}

.vp-badge {
  font-size: 11px; font-weight: 600; padding: 4px 9px; border-radius: 999px;
}
.vp-badge-web { color: var(--blue); background: var(--blue-soft); }
.vp-badge-live { color: var(--green-700); background: var(--green-soft); }
.vp-badge-sample { color: var(--amber); background: var(--amber-soft); }

.vp-card-price { text-align: right; flex-shrink: 0; }
.vp-price { font-size: 24px; font-weight: 700; letter-spacing: -.02em; }
.vp-stock { font-size: 12px; font-weight: 600; margin-top: 4px; }
.vp-stock-in { color: var(--green); }
.vp-stock-out { color: var(--red); }

.vp-fitment {
  display: flex; gap: 10px; align-items: baseline;
  margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--line-soft);
}
.vp-fitment-label {
  font-size: 11px; font-weight: 700; letter-spacing: .06em;
  color: var(--faint); text-transform: uppercase; flex-shrink: 0; padding-top: 2px;
}
.vp-fitment-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.vp-fit-tag {
  font-size: 12px; font-weight: 500; color: var(--ink);
  background: var(--paper); border: 1px solid var(--line);
  padding: 3px 9px; border-radius: 6px;
}

.vp-card-actions { display: flex; gap: 10px; margin-top: 16px; }
.vp-btn-primary {
  flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  background: var(--green); color: #fff; text-decoration: none;
  font-size: 14px; font-weight: 600; padding: 11px 16px; border-radius: 10px;
  transition: background .15s ease;
}
.vp-btn-primary:hover { background: var(--green-700); }
.vp-btn-disabled {
  flex: 1; text-align: center; color: var(--faint);
  border: 1px dashed var(--line); border-radius: 10px; padding: 11px;
  font-size: 14px;
}
.vp-btn-ghost {
  background: transparent; border: 1px solid var(--line); color: var(--muted);
  font-size: 13px; font-weight: 600; padding: 11px 14px; border-radius: 10px;
  cursor: pointer; font-family: inherit; white-space: nowrap;
  transition: border-color .15s ease, color .15s ease;
}
.vp-btn-ghost:hover { border-color: var(--green); color: var(--green-700); }

.vp-why {
  margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line-soft);
  animation: vp-fade .25s ease both;
}
.vp-why-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px;
}
.vp-detail { display: flex; flex-direction: column; gap: 2px; }
.vp-detail-k { font-size: 11px; color: var(--faint); font-weight: 600; letter-spacing: .03em; }
.vp-detail-v { font-size: 14px; color: var(--ink); }
.vp-why-reason {
  margin-top: 12px; font-size: 12.5px; color: var(--muted);
  background: var(--paper); border-radius: 8px; padding: 9px 12px;
}
.vp-why-desc {
  margin-top: 12px; font-size: 13.5px; line-height: 1.6; color: var(--muted);
}
.vp-why-desc img { max-width: 100%; border-radius: 8px; }

/* Alternates */
.vp-alts {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden;
}
.vp-alts-label {
  font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  color: var(--faint); padding: 12px 16px 8px;
}
.vp-alt {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 12px 16px; border-top: 1px solid var(--line-soft);
  text-decoration: none; color: inherit;
}
.vp-alt-link { transition: background .12s ease; cursor: pointer; }
.vp-alt-link:hover { background: var(--paper); }
.vp-alt-main { min-width: 0; }
.vp-alt-title {
  font-size: 14px; font-weight: 500; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.vp-alt-sku { font-family: 'JetBrains Mono', monospace; font-size: 11.5px; color: var(--faint); margin-top: 2px; }
.vp-alt-side { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.vp-alt-price { font-size: 15px; font-weight: 600; }
.vp-alt-stock { font-size: 11px; font-weight: 600; }

/* Sources */
.vp-sources {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 14px 16px;
}
.vp-sources-label {
  font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  color: var(--faint); margin-bottom: 10px;
}
.vp-source {
  display: flex; align-items: center; gap: 9px; padding: 7px 0;
  text-decoration: none; border-top: 1px solid var(--line-soft);
}
.vp-source:first-of-type { border-top: none; }
.vp-source-title {
  font-size: 13px; color: var(--blue); font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.vp-source-tag {
  font-size: 10px; font-weight: 700; color: var(--green-700);
  background: var(--green-soft); padding: 2px 7px; border-radius: 5px; flex-shrink: 0;
}

/* Welcome */
.vp-welcome { text-align: center; padding: 36px 0 8px; animation: vp-fade .4s ease both; }
.vp-welcome-mark {
  width: 52px; height: 52px; border-radius: 14px; margin: 0 auto 18px;
  display: grid; place-items: center; background: var(--green); color: #fff;
  box-shadow: var(--shadow);
}
.vp-welcome-title { margin: 0 0 10px; font-size: 26px; font-weight: 700; letter-spacing: -.02em; }
.vp-welcome-sub { margin: 0 auto 26px; max-width: 440px; font-size: 14.5px; line-height: 1.6; color: var(--muted); }
.vp-welcome-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; max-width: 520px; margin: 0 auto;
}
.vp-welcome-card {
  text-align: left; background: var(--surface); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 15px; cursor: pointer;
  display: flex; flex-direction: column; gap: 4px; font-family: inherit;
  transition: border-color .15s ease, transform .15s ease;
}
.vp-welcome-card:hover { border-color: var(--green); transform: translateY(-1px); }
.vp-welcome-card-k { font-size: 11px; font-weight: 700; letter-spacing: .04em; color: var(--green-700); text-transform: uppercase; }
.vp-welcome-card-v { font-size: 14px; color: var(--ink); }

/* Composer */
.vp-composer {
  position: sticky; bottom: 0; z-index: 20;
  background: linear-gradient(to top, var(--paper) 72%, rgba(246,247,244,0));
  padding: 10px 20px 16px;
}
.vp-composer-inner { max-width: 820px; margin: 0 auto; }
.vp-chips { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 10px; justify-content: center; }
.vp-chip {
  font-size: 12.5px; font-weight: 500; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line);
  padding: 6px 12px; border-radius: 999px; cursor: pointer; font-family: inherit;
  transition: all .15s ease;
}
.vp-chip:hover { border-color: var(--green); color: var(--green-700); }
.vp-input-row {
  display: flex; align-items: center; gap: 8px;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 14px; padding: 7px 7px 7px 16px;
  box-shadow: var(--shadow);
  transition: border-color .15s ease, box-shadow .15s ease;
}
.vp-input-row:focus-within {
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-soft);
}
.vp-input {
  flex: 1; border: none; outline: none; background: transparent;
  font-size: 15px; color: var(--ink); font-family: inherit; padding: 8px 0;
}
.vp-input::placeholder { color: var(--faint); }
.vp-send {
  width: 40px; height: 40px; border-radius: 10px; border: none;
  background: var(--green); color: #fff; cursor: pointer;
  display: grid; place-items: center; flex-shrink: 0;
  transition: background .15s ease;
}
.vp-send:hover:not(:disabled) { background: var(--green-700); }
.vp-send:disabled { background: #C3CEC7; cursor: not-allowed; }
.vp-disclaimer { text-align: center; font-size: 11.5px; color: var(--faint); margin: 9px 0 0; }

/* Typing */
.vp-typing {
  display: inline-flex; gap: 5px; align-items: center;
  background: var(--surface); border: 1px solid var(--line);
  padding: 14px 18px; border-radius: var(--radius); box-shadow: var(--shadow);
}
.vp-typing span {
  width: 7px; height: 7px; border-radius: 50%; background: var(--faint);
  animation: vp-bounce 1.2s infinite ease-in-out;
}
.vp-typing span:nth-child(2) { animation-delay: .15s; }
.vp-typing span:nth-child(3) { animation-delay: .3s; }
@keyframes vp-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: .5; }
  30% { transform: translateY(-5px); opacity: 1; }
}

.vp-spin { animation: vp-rotate .8s linear infinite; }
@keyframes vp-rotate { to { transform: rotate(360deg); } }

/* Mobile */
@media (max-width: 600px) {
  .vp-welcome-grid { grid-template-columns: 1fr; }
  .vp-why-grid { grid-template-columns: 1fr; }
  .vp-card-head { flex-direction: column; }
  .vp-card-price { text-align: left; }
  .vp-user-bubble, .vp-assistant-card { max-width: 90%; }
  .vp-card-actions { flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
`}</style>
  );
}
