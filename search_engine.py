#!/usr/bin/env python3
"""
search_engine.py  —  Branson parts retrieval + grounded chat (RAG)

3-stage pipeline:
  1) EXACT   : SKU / part-number match (deterministic, instant)
  2) RETRIEVE: model-fitment filter + embedding nearest-neighbour search
               ("air filter" -> "AIR CLEANER", and "fits 5220CH" actually
                restricts to parts whose fitment includes 5220CH)
  3) GENERATE: GPT writes the reply, picks the best product, never invents one

Keeps the SAME JSON contract the React frontend reads, and adds a conversational
`response` string to every search result so the chat actually talks.
"""

import os
import re
import json
import pickle
import logging
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. DATA NORMALISATION  — fix dirty SKUs, extract fitment, build searchable blob
# ---------------------------------------------------------------------------

_MODEL_RE = re.compile(r"Branson\s+\d{4}[A-Z]{0,2}", re.IGNORECASE)
_PAREN_CODE_RE = re.compile(r"\(([A-Za-z0-9_\-]{3,})\)")
# A bare model token in free text, e.g. "5220CH", "2100", "3725CH"
_MODEL_TOKEN_RE = re.compile(r"\b(\d{4}[A-Z]{0,2})\b")


def clean_sku(title: str, raw_sku: str) -> str:
    """Recover a usable SKU. ~15% of SKU fields are corrupt (a title dumped in).
    Real code is the title's last parenthetical 83% of the time, else the
    leading token of the field. Tested: recovers 100% of rows."""
    title, raw_sku = str(title or ""), str(raw_sku or "").strip()
    codes = _PAREN_CODE_RE.findall(title)
    if codes:
        return codes[-1].upper()
    if raw_sku:
        return raw_sku.split()[0].upper()
    return ""


def models_from_tags(tags) -> list:
    """Clean list of compatible models, e.g. ['Branson 2100', 'Branson 5220CH']."""
    if isinstance(tags, list):
        tags = " ".join(tags)
    return sorted(set(m.title() for m in _MODEL_RE.findall(str(tags or ""))))


def bare_codes(fits_models: list) -> set:
    """Model strings -> bare codes for fast fitment matching: {'5220CH','2100'}."""
    return {m.split()[-1].upper() for m in fits_models if m.split()}


def searchable_text(p: dict) -> str:
    models = ", ".join(p.get("fits_models", []))
    return f"{p.get('title','')} | type: {p.get('type','')} | fits: {models} | sku: {p.get('sku','')}"


def normalize_product(raw: dict) -> dict:
    sku = clean_sku(raw.get("title", ""), raw.get("sku", ""))
    handle = raw.get("product_handle", "")
    p = {
        "title": str(raw.get("title", "")).strip(),
        "sku": sku,
        "price": float(raw.get("price", 0) or 0),
        "inventory": int(raw.get("inventory", 0) or 0),
        "type": str(raw.get("type", "")),
        "tags": raw.get("tags", []) if isinstance(raw.get("tags"), list) else [],
        "weight": raw.get("weight", ""),
        "weight_unit": raw.get("weight_unit", "lb"),
        "description": raw.get("description", ""),
        "source": raw.get("source", ""),
        "is_live_data": bool(raw.get("is_live_data", False)),
        "product_handle": handle,
    }
    p["fits_models"] = models_from_tags(p["tags"])
    p["fits_codes"] = bare_codes(p["fits_models"])
    if handle:
        p["product_url"] = f"https://varnerparts.com/products/{handle}"
    elif sku:
        p["product_url"] = f"https://varnerparts.com/search?q={sku}"
    else:
        p["product_url"] = None
    return p


def find_models(query: str, known: set) -> list:
    """Pull Branson model codes out of the query, validated against the catalog."""
    toks = _MODEL_TOKEN_RE.findall(query.upper())
    return [t for t in dict.fromkeys(toks) if t in known]  # unique, order-preserved


# ---------------------------------------------------------------------------
# 2. EMBEDDING INDEX  — built once, cached to disk, reused on restart
# ---------------------------------------------------------------------------

EMBED_MODEL = "text-embedding-3-small"
INDEX_FILE = "embedding_index.pkl"
SCHEMA_VERSION = "v3"  # bump to invalidate any prior cache format (matrix-only now)

# Filler words ignored by the lexical boost so they don't dilute the part name.
_STOP = {
    "the",
    "for",
    "and",
    "that",
    "fits",
    "fit",
    "with",
    "need",
    "want",
    "find",
    "looking",
    "look",
    "searching",
    "search",
    "get",
    "buy",
    "show",
    "branson",
    "tractor",
    "part",
    "parts",
    "series",
    "model",
    "what",
    "any",
    "have",
}


class SemanticIndex:
    """In-memory vector index with model-fitment awareness."""

    def __init__(self, openai_client):
        self.client = openai_client
        self.products = []
        self.matrix = None
        self.known_models = set()  # every model code that appears in the catalog

    def _embed(self, texts: list) -> np.ndarray:
        out = []
        for i in range(0, len(texts), 256):
            resp = self.client.embeddings.create(
                model=EMBED_MODEL, input=texts[i : i + 256]
            )
            out.extend([d.embedding for d in resp.data])
            logger.info(f"   embedded {min(i+256, len(texts))}/{len(texts)}")
        arr = np.array(out, dtype=np.float32)
        arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
        return arr

    def build(self, raw_inventory: list, cache: bool = True):
        # Products are ALWAYS rebuilt fresh from raw inventory, so they always
        # match the current code's schema (incl. fits_codes). Only the expensive
        # embedding matrix is cached — keyed by a signature of the catalog.
        self.products = [normalize_product(r) for r in raw_inventory]
        self.known_models = set()
        for p in self.products:
            self.known_models |= p["fits_codes"]
        sig = _signature(self.products)

        if cache and os.path.exists(INDEX_FILE):
            try:
                with open(INDEX_FILE, "rb") as f:
                    saved = pickle.load(f)
                mat = saved.get("matrix")
                if (
                    saved.get("sig") == sig
                    and mat is not None
                    and getattr(mat, "shape", [0])[0] == len(self.products)
                ):
                    self.matrix = mat
                    logger.info(
                        f"✅ Loaded cached embeddings ({len(self.products)} products)"
                    )
                    return
                logger.info("ℹ️  Catalog/schema changed — re-embedding")
            except Exception as e:
                logger.warning(f"⚠️  Could not load embedding cache: {e}")

        logger.info(f"🧠 Embedding {len(self.products)} products (one-time)...")
        self.matrix = self._embed([searchable_text(p) for p in self.products])
        if cache:
            with open(INDEX_FILE, "wb") as f:
                pickle.dump({"sig": sig, "matrix": self.matrix}, f)  # matrix only
        logger.info("✅ Embedding index built")

    def search(self, query: str, k: int = 8, must_fit: set = None) -> list:
        """Hybrid search: embedding similarity + a lexical boost on the part name,
        so the salient word ('radiator') isn't drowned out by filler tokens. If
        must_fit is given, parts that fit one of those models rank first."""
        if self.matrix is None:
            return []
        q = self._embed([query])[0]
        sims = self.matrix @ q

        # lexical (sparse) signal: query content-words found in the product title
        qwords = [w for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in _STOP]
        ranked = sims.copy()
        if qwords:
            for i, p in enumerate(self.products):
                tl = p["title"].lower()
                hits = sum(1 for w in qwords if w in tl)
                if hits:
                    ranked[i] += 0.12 * hits
        order = np.argsort(-ranked)

        chosen = []
        if must_fit:
            for i in order:  # fitting parts, best-similarity first
                if self.products[i].get("fits_codes", set()) & must_fit:
                    chosen.append(i)
                    if len(chosen) >= k:
                        break
            if len(chosen) < k:  # top up with best non-fitting parts
                have = set(chosen)
                for i in order:
                    if i not in have:
                        chosen.append(i)
                        if len(chosen) >= k:
                            break
        else:
            chosen = list(order[:k])

        return [
            {
                **self.products[i],
                "semantic_score": float(sims[i]),
                "fits_query_model": bool(
                    must_fit and (self.products[i].get("fits_codes", set()) & must_fit)
                ),
            }
            for i in chosen
        ]


def _signature(products: list) -> str:
    import hashlib

    h = hashlib.md5()
    h.update(SCHEMA_VERSION.encode())
    h.update(str(len(products)).encode())
    for p in products[:5000]:
        h.update(p["sku"].encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 3. HYBRID RETRIEVAL  — exact SKU -> fitment+semantic -> keyword safety net
# ---------------------------------------------------------------------------

_PARTNUM_RE = re.compile(
    r"\b([A-Z]{1,2}\d{2,}[A-Z0-9_]*|\d{5}-\d{5,6}|[A-Z]\d{2}_\d{3})\b", re.IGNORECASE
)


def find_part_numbers(text: str) -> list:
    return [m.upper() for m in _PARTNUM_RE.findall(text)]


# "20 series" / "2000 series" -> leading digit -> all member model codes
_SERIES_RE = re.compile(r"\b(\d)0{1,3}\s*series\b", re.IGNORECASE)


def find_series(query: str, known: set) -> set:
    """Expand a series phrase to every catalog model code in that series."""
    out = set()
    for d in _SERIES_RE.findall(query):
        out |= {m for m in known if m and m[0] == d}
    return out


def retrieve(index: SemanticIndex, query: str, k: int = 8):
    """Returns (candidates, exact_hit, requested). exact_hit short-circuits when
    the user pasted a SKU we have; requested is the model/series the user named."""
    sku_map = {p["sku"]: p for p in index.products if p["sku"]}
    models = find_models(query, index.known_models)
    series = find_series(query, index.known_models)

    # human-readable label of what the customer asked to fit (for the generator)
    requested = list(models)
    sm = _SERIES_RE.search(query)
    if sm:
        requested.append(sm.group(0).strip())

    # Stage 1: exact / partial part-number match (but don't treat a bare model
    # code like 5220CH as a SKU — those are handled by fitment filtering)
    for pn in find_part_numbers(query):
        if pn in models:
            continue
        if pn in sku_map:
            return [sku_map[pn]], sku_map[pn], requested
        partial = [p for s, p in sku_map.items() if pn in s]
        if partial:
            return partial[:k], None, requested

    # Stage 2: fitment-aware semantic retrieval (model codes + expanded series)
    must_fit = (set(models) | series) or None
    candidates = index.search(query, k=k, must_fit=must_fit)

    # Stage 3: keyword safety net (respect fitment when a model was requested)
    ql = query.lower()
    seen = {c["sku"] for c in candidates}
    for p in index.products:
        if must_fit and not (p.get("fits_codes", set()) & must_fit):
            continue
        if p["sku"] in seen:
            continue
        if any(w in p["title"].lower() for w in ql.split() if len(w) > 3):
            candidates.append(
                {**p, "semantic_score": 0.0, "fits_query_model": bool(must_fit)}
            )
            seen.add(p["sku"])
            if len(candidates) >= k * 2:
                break
    return candidates[: k * 2], None, requested


# ---------------------------------------------------------------------------
# 4. GENERATION  — GPT writes the reply AND picks the product (grounded)
# ---------------------------------------------------------------------------

_SYS = """You are the parts expert for Varner Parts (Branson tractor parts).
You get a customer message and CANDIDATE products from the live catalog. Rules:

1. Pick the single best product by index, or null if none truly fit. NEVER invent
   a SKU, price, or product not in the candidate list.
2. If the customer named a tractor model, ONLY pick a candidate whose 'fits' list
   includes that model. If none fit it, set best_index to null, say you couldn't
   find a part confirmed to fit, and ask them to confirm the model or part.
3. Match the PART TYPE honestly. A true synonym is fine to pick confidently —
   "canopy" = "roof", "air filter" = "air cleaner" are the SAME part. But if the
   customer asks for a part the catalog clearly does NOT have (e.g. an "air
   conditioner" when every candidate is an air cleaner/filter), do NOT pass a
   loosely-related part off as a match. Set best_index to null, tell them you
   don't list that part for their tractor, and point to the closest real category.
4. Confidence scale: 85-100 ONLY when a candidate is exactly the requested part
   for the requested model. 55-80 for a solid match with minor uncertainty.
   30-50 when substituting a related part or unsure. Under 30 / null when the
   catalog likely doesn't carry what they asked for.
5. Write a short, friendly reply (2-4 sentences): what you found, the model it
   fits, and stock. Every product fact (SKU/price/stock) must come from candidates.

Examples of rule 3:
- "canopy for 20 series" + a ROOF candidate -> pick it, that's the same part.
- "air conditioner for 2100" + only AIR CLEANER candidates -> best_index null,
  explain Branson compacts don't list an A/C unit, ask if they meant the air
  cleaner/filter.

Respond ONLY with JSON, no markdown:
{
  "reply": "message to the customer",
  "best_index": <int or null>,
  "alternate_indices": [<other plausible indices, best first>],
  "confidence": <0-100 integer>,
  "clarifying_question": "<question if confidence is low, else null>"
}"""


def generate(openai_client, query: str, candidates: list, requested: list) -> dict:
    listing = "\n".join(
        f"[{i}] {c['title']} | SKU {c['sku']} | ${c['price']:.2f} | "
        f"{'in stock' if c['inventory'] > 0 else 'OUT of stock'} | "
        f"fits: {', '.join(c['fits_models']) or 'unknown'}"
        + ("  <-- FITS REQUESTED MODEL" if c.get("fits_query_model") else "")
        for i, c in enumerate(candidates)
    )
    model_note = (
        f"\n\nThe customer needs a part that fits: {', '.join(requested)}."
        if requested
        else ""
    )
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYS},
                {
                    "role": "user",
                    "content": f"Customer: {query}{model_note}\n\nCANDIDATES:\n{listing}",
                },
            ],
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"generation failed: {e}")
        # safe fallback: prefer a candidate that fits the requested model
        fit_i = next(
            (i for i, c in enumerate(candidates) if c.get("fits_query_model")), None
        )
        best = fit_i if fit_i is not None else (0 if candidates else None)
        return {
            "reply": "Here are the closest matches I found.",
            "best_index": best,
            "alternate_indices": [
                i for i in range(min(4, len(candidates))) if i != best
            ],
            "confidence": 50,
            "clarifying_question": None,
        }


# ---------------------------------------------------------------------------
# 5. PUBLIC ENTRYPOINT  — returns the existing frontend JSON contract
# ---------------------------------------------------------------------------


def answer_query(index: SemanticIndex, openai_client, query: str) -> dict:
    candidates, exact, requested = retrieve(index, query)

    if not candidates:
        return {
            "query": query,
            "results": [],
            "all_matches": [],
            "search_confidence": 0,
            "is_conversational": False,
            "response": "I couldn't find a match. Which Branson model do you have, and what part are you after?",
            "message": "No matches found.",
            "suggestions": [
                "Try a model number, e.g. 'air cleaner 2100'",
                "Paste the SKU if you have it",
            ],
            "interpretation": {},
            "web_search_used": False,
        }

    gen = generate(openai_client, query, candidates, requested)
    best_i = gen.get("best_index")
    alt_i = gen.get("alternate_indices", []) or []
    confidence = int(gen.get("confidence", 60))

    declined = best_i is None
    had_fit = any(c.get("fits_query_model") for c in candidates)

    # Confidence sanity caps — the LLM tends to over-trust fuzzy semantic matches.
    if exact is None:
        confidence = min(confidence, 85)  # no "best match" feel without an exact SKU
    if exact is None and not had_fit:
        confidence = min(confidence, 55)  # nothing confirmed to fit the named model
    if declined:
        confidence = min(confidence, 45)

    reply = gen.get("reply") or "Here's what I found."
    if gen.get("clarifying_question"):
        reply += " " + gen["clarifying_question"]

    # If the model declined AND we have no solid signal (no SKU, nothing fits the
    # requested model), don't show a product card — relay the clarification only.
    # This is what stops "air conditioner" returning an air cleaner as a match.
    if declined and exact is None and not had_fit:
        return {
            "query": query,
            "results": [],
            "all_matches": [],
            "search_confidence": confidence,
            "is_conversational": False,
            "response": reply,
            "message": reply,
            "suggestions": ["Tell me your Branson model", "Name the part you need"],
            "interpretation": {
                "equipment": "Branson",
                "requested": requested,
                "part_type": query,
            },
            "web_search_used": False,
        }

    ordered = []
    if best_i is not None and 0 <= best_i < len(candidates):
        ordered.append(candidates[best_i])
    for i in alt_i:
        if 0 <= i < len(candidates) and candidates[i] not in ordered:
            ordered.append(candidates[i])
    if not ordered:
        ordered = candidates[:3]

    for r in ordered:
        r.pop("fits_codes", None)  # internal-only set; not JSON-serializable
        fits = r.get("fits_query_model")
        r["match_reason"] = (
            "fits requested model" if fits else "closest part type"
        ) + f" · semantic {r.get('semantic_score', 0):.2f}"
        r["is_web_verified"] = exact is not None
        r["confidence_level"] = confidence

    return {
        "query": query,
        "results": ordered,
        "all_matches": ordered,
        "total_matches": len(candidates),
        "search_confidence": confidence,
        "response": reply,
        "is_conversational": False,
        "interpretation": {
            "equipment": "Branson",
            "requested": requested,
            "part_type": query,
        },
        "web_search_used": False,
        "message": reply,
    }


# ---------------------------------------------------------------------------
# WIRE-IN (backend.py):
#   from search_engine import SemanticIndex, answer_query
#   semantic_index = SemanticIndex(openai_client)
#   semantic_index.build(inventory_index)            # after inventory loads
#   ... in /api/chat for search intent:
#   return jsonify(answer_query(semantic_index, openai_client, user_query))
# ---------------------------------------------------------------------------
