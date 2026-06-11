#!/usr/bin/env python3
"""
search_engine.py — full Branson parts search/answer logic (imported by app.py).

Keeps ALL of the original search-quality logic, but runs it against a bounded
candidate set pulled from Supabase instead of an in-memory catalogue:

  * interpret_query_branson()   LLM parse -> equipment / model / part_type /
                                SKUs / is_series  (feeds the UI + validation)
  * web_search_serper()         optional varnerparts.com-first verification
  * validate_part_type_match()  HARD-REJECT wrong categories (filter kit != O-ring)
  * series expansion            "20 Series" -> 2100/2205/2400/...
  * COMMON_BRANSON_PARTS         known part -> SKU seed map
  * multi-factor scoring         web-verified > user SKU > part-type > model
  * calculate_*_confidence()     5-factor confidence -> show 1 / 3 / 5 results

Return shape is exactly what App.js reads (conversational vs. results, with
interpretation.web_research.sources and per-row is_web_verified).
"""

import os
import re
import json
import logging

import requests

logger = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "512"))
INTERP_MODEL = os.getenv("INTERP_MODEL", "gpt-4o-mini")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
STORE_DOMAIN = os.getenv("STORE_DOMAIN", "varnerparts.com")
SEMANTIC_K = int(os.getenv("SEMANTIC_K", "30"))  # recall pool size from pgvector

# ---------------------------------------------------------------------------
# Knowledge base (ported from the original app.py)
# ---------------------------------------------------------------------------
BRANSON_MODELS = [
    "2100",
    "2205H",
    "2400",
    "2400H",
    "2515H",
    "2515R",
    "2505H",
    "2610R",
    "2800",
    "2800H",
    "3015C",
    "3015R",
    "3520H",
    "3520R",
    "3725CH",
    "4015C",
    "4015R",
    "4520C",
    "4520R",
    "5220C",
    "5220CH",
    "5220R",
    "5825C",
    "5835R",
    "6225C",
    "6225R",
    "7845C",
    "7845R",
    "8050C",
    "8050R",
]

SERIES_MAP = {
    "20": ["2100", "2205", "2400", "2515", "2610", "2800"],
    "30": ["3015", "3520", "3725"],
    "40": ["4015", "4520"],
    "50": ["5220", "5825", "5835"],
    "60": ["6225"],
    "70": ["7845"],
    "80": ["8050"],
}

COMMON_BRANSON_PARTS = {
    "glow plug": ["HT17160000A3"],
    "air filter": ["EA00000985A"],
    "fuel filter": ["23000-022000"],
    "oil filter": ["A04_002"],
    "fuel injection valve": ["HK12020000A3"],
}

# Category validation table (intent ported from validate_part_type_match).
PART_CATEGORIES = {
    "air filter": {
        "exact": ["air filter", "air cleaner"],
        "related": ["filter element", "air element"],
        "exclude": [
            "bracket",
            "housing",
            "cover",
            "o-ring",
            "seal",
            "gasket",
            "hose",
            "clamp",
        ],
    },
    "oil filter": {
        "exact": ["oil filter"],
        "related": ["filter element"],
        "exclude": ["bracket", "housing", "o-ring", "seal", "adapter", "gasket"],
    },
    "fuel filter": {
        "exact": ["fuel filter"],
        "related": ["filter element", "fuel strainer"],
        "exclude": ["bracket", "housing", "o-ring", "seal", "gasket", "line", "hose"],
    },
    "hydraulic filter": {
        "exact": ["hydraulic filter", "hyd filter"],
        "related": ["filter element", "suction filter"],
        "exclude": ["bracket", "o-ring", "seal", "gasket"],
    },
    "filter kit": {
        "exact": [
            "filter kit",
            "maintenance kit",
            "service kit",
            "filter package",
            "filter set",
        ],
        "related": ["complete filter"],
        "exclude": [
            "bracket",
            "o-ring",
            "seal",
            "gasket",
            "single filter",
            "individual filter",
        ],
    },
    "glow plug": {
        "exact": ["glow plug"],
        "related": ["heating plug"],
        "exclude": ["wire", "harness", "relay"],
    },
    "belt": {
        "exact": ["v-belt", "drive belt", "belt"],
        "related": ["transmission belt", "fan belt"],
        "exclude": ["pulley", "tensioner", "idler"],
    },
    "hose": {
        "exact": ["hose", "tube", "line"],
        "related": ["hydraulic hose", "fuel line", "coolant hose"],
        "exclude": ["clamp", "fitting", "adapter", "connector"],
    },
    "roof": {
        "exact": ["roof", "canopy", "top", "cab roof"],
        "related": ["cover"],
        "exclude": ["bracket", "seal", "gasket"],
    },
    "bolt": {
        "exact": ["bolt", "hex bolt", "cap screw"],
        "related": ["fastener", "screw"],
        "exclude": ["nut", "washer"],
    },
}

GREETINGS = {"hi", "hey", "hello", "yo", "hiya"}

FAQ_RESPONSES = {
    "what_sell": {
        "keywords": ["what do you sell", "what kind", "what type", "what products"],
        "response": "We sell parts for all models of Branson tractors. What can I help you find?",
    },
    "shipping": {
        "keywords": ["shipping", "delivery", "free shipping", "ship"],
        "response": "Please check our Terms & Conditions for shipping details. Which part are you looking for?",
    },
    "warranty": {
        "keywords": ["warranty", "guarantee", "return"],
        "response": "Warranty and return info is in our Terms & Conditions. Can I help you find a part?",
    },
    "payment": {
        "keywords": ["payment", "pay", "credit card", "accept"],
        "response": "We accept standard payment methods; see checkout for details. What part can I locate for you?",
    },
    "hours": {
        "keywords": ["hours", "open", "business hours", "when open"],
        "response": "Business hours and contact info are on our Contact page. What Branson part do you need?",
    },
}

PART_INDICATORS = [
    "part",
    "filter",
    "plug",
    "valve",
    "roof",
    "hose",
    "belt",
    "seal",
    "gasket",
    "pump",
    "starter",
    "alternator",
    "battery",
    "tire",
    "wheel",
    "mirror",
    "light",
    "seat",
    "clutch",
    "brake",
    "hydraulic",
    "engine",
    "transmission",
    "pto",
    "axle",
    "bearing",
    "bushing",
    "kit",
    "assembly",
    "bolt",
    "nut",
    "screw",
    "washer",
    "rivet",
    "pin",
    "clip",
    "clamp",
    "fastener",
    "need",
    "looking for",
    "look for",
    "search for",
    "find",
    "want",
    "buy",
    "purchase",
    "order",
    "price",
    "cost",
    "sell",
    "stock",
]

SKU_PATTERNS = [
    r"\b[A-Z]{2}\d{8}[A-Z]\d\b",
    r"\bV\d{10}\b",
    r"\b\d{5}-\d{5,6}\b",
    r"\b[A-Z]\d{2}_\d{3}\b",
    r"\b[A-Z]{2}\d{5}\b",
    r"\b[A-Z]\d{3,4}\b",
]
WEB_PART_PATTERNS = SKU_PATTERNS + [
    r"\(([A-Z]{2}\d{5})\)",
    r"\(([A-Z]\d{3,4})\)",
    r"\bP/?N[\s:#]*([A-Z0-9\-]+)\b",
    r"\bSKU[\s:#]*([A-Z0-9\-]+)\b",
]


# ---------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------
def detect_intent(query: str) -> str:
    ql = query.lower().strip()
    if ql in GREETINGS or (any(g in ql for g in GREETINGS) and len(ql.split()) <= 2):
        return "greeting"
    if any(re.search(p, query, re.IGNORECASE) for p in SKU_PATTERNS):
        return "search"
    for faq_type, data in FAQ_RESPONSES.items():
        if any(k == ql for k in data["keywords"]):
            return f"faq:{faq_type}"
        if any(k in ql for k in data["keywords"]) and not any(
            w in ql for w in PART_INDICATORS
        ):
            return f"faq:{faq_type}"
    if any(i in ql for i in PART_INDICATORS):
        return "search"
    if any(m.lower() in ql for m in BRANSON_MODELS):
        return "search"
    return "search"


# ---------------------------------------------------------------------------
# LLM interpretation (ported)
# ---------------------------------------------------------------------------
INTERP_SYSTEM = """You are a Branson tractor parts specialist.

Models: 2100, 2205H, 2400, 2400H, 2515H, 2515R, 2505H, 2610R, 2800, 2800H,
3015C, 3015R, 3520H, 3520R, 3725CH, 4015C, 4015R, 4520C, 4520R, 5220C, 5220CH,
5220R, 5825C, 5835R, 6225C, 6225R, 7845C, 7845R, 8050C, 8050R.

Series: "20 Series"=2100..2800; "30"=3015..3725; "40"=4015,4520; "50"=5220..5835;
"60"=6225; "70"=7845; "80"=8050.

Extract part numbers/SKUs anywhere in the query, including codes in parentheses
like (V2184610025), (B207). Be specific about the part type (e.g. "hex bolt",
"glow plug", "canopy").

Respond ONLY with JSON, no markdown:
{"equipment":"Branson <model or series>","model":"model or series name",
"part_type":"specific part","specific_sku":"primary SKU or null",
"all_part_numbers":["..."],"search_terms":["..."],
"confidence":"high|medium|low","is_series":true|false}"""


def interpret_query_branson(openai_client, query: str) -> dict:
    fallback = {
        "equipment": query,
        "model": "",
        "part_type": "unknown",
        "specific_sku": None,
        "all_part_numbers": [],
        "search_terms": query.split(),
        "confidence": "low",
        "is_series": False,
    }
    try:
        resp = openai_client.chat.completions.create(
            model=INTERP_MODEL,
            temperature=0.1,
            max_tokens=400,
            messages=[
                {"role": "system", "content": INTERP_SYSTEM},
                {"role": "user", "content": query},
            ],
        )
        txt = re.sub(
            r"^```json\s*|\s*```$", "", resp.choices[0].message.content.strip()
        )
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        interp = json.loads(m.group()) if m else dict(fallback)
        interp.setdefault("model", "")
        interp.setdefault("part_type", "unknown")
        interp.setdefault("all_part_numbers", [])
        interp.setdefault("is_series", False)
        interp.setdefault("search_terms", query.split())
        logger.info("interpretation: %s", json.dumps(interp))
        return interp
    except Exception as e:
        logger.warning("interpret failed: %s", e)
        return fallback


# ---------------------------------------------------------------------------
# web search (optional, ported)
# ---------------------------------------------------------------------------
def web_search_serper(query: str, num: int = 8, prioritize_varner: bool = True):
    if not SERPER_API_KEY:
        return []
    sq = (
        query
        if (not prioritize_varner or "varnerparts" in query.lower())
        else f"{query} site:{STORE_DOMAIN} OR {query}"
    )
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": sq, "num": num},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("Serper %s", r.status_code)
            return []
        out = [
            {
                "title": it.get("title", ""),
                "snippet": it.get("snippet", ""),
                "url": it.get("link", ""),
                "is_varner": STORE_DOMAIN in it.get("link", "").lower(),
            }
            for it in r.json().get("organic", [])[:num]
        ]
        out.sort(key=lambda x: 0 if x["is_varner"] else 1)
        return out
    except Exception as e:
        logger.warning("Serper failed: %s", e)
        return []


def _dedupe_upper(items):
    seen, out = set(), []
    for s in items:
        s = (s or "").upper()
        if s and s not in seen and len(s) >= 3:
            seen.add(s)
            out.append(s)
    return out


def extract_part_numbers_from_web(results):
    found = []
    for r in results:
        text = f"{r.get('title','')} {r.get('snippet','')}"
        for pat in WEB_PART_PATTERNS:
            for m in re.findall(pat, text, re.IGNORECASE):
                found.append(m if isinstance(m, str) else next((x for x in m if x), ""))
    return _dedupe_upper(found)


def extract_skus(query: str):
    found = []
    for pat in SKU_PATTERNS:
        found += re.findall(pat, query, re.IGNORECASE)
    return _dedupe_upper(found)


# ---------------------------------------------------------------------------
# part-type validation (ported)
# ---------------------------------------------------------------------------
def validate_part_type_match(requested_type, product_title, product_type):
    """Returns (score 0-2000, is_valid). is_valid False == HARD REJECT."""
    requested = (requested_type or "").lower()
    title = (product_title or "").lower()
    ptype = (product_type or "").lower()

    config = None
    for name, cfg in PART_CATEGORIES.items():
        if name in requested or any(t in requested for t in cfg["exact"]):
            config = cfg
            break

    if not config:
        words = requested.split()
        if words and all(w in title or w in ptype for w in words):
            return (1000, True)
        if any(w in title or w in ptype for w in words):
            return (500, True)
        return (0, False)

    for ex in config.get("exclude", []):
        if ex in title:
            return (0, False)  # HARD REJECT wrong category
    for term in config.get("exact", []):
        if term in title:
            return (2000, True)
    for term in config.get("related", []):
        if term in title:
            return (1200, True)
    if any(w in title for w in requested.split()):
        return (800, True)
    return (0, False)


def calculate_match_confidence(
    score, has_web, has_user_sku, part_type_score, model_score
):
    c = 0
    if has_web:
        c += 40
    if has_user_sku:
        c += 30
    if part_type_score >= 2000:
        c += 30
    elif part_type_score >= 1200:
        c += 20
    elif part_type_score >= 800:
        c += 10
    if model_score >= 1000:
        c += 20
    elif model_score >= 800:
        c += 10
    if score >= 5000:
        c += 10
    elif score >= 3000:
        c += 5
    return min(c, 100)


def calculate_overall_confidence(matches, has_web, has_model, has_part_type, top_score):
    if not matches:
        return 0
    c = matches[0].get("confidence_level", 0)
    if c < 60:
        c = max(c - 10, 30)
    if has_web:
        c = min(c + 10, 100)
    if has_model and has_part_type:
        c = min(c + 5, 100)
    if (
        len(matches) >= 2
        and (matches[0]["match_score"] - matches[1]["match_score"]) < 500
    ):
        c = max(c - 15, 40)
    if top_score < 2000:
        c = max(c - 10, 30)
    return max(min(c, 100), 0)


# ---------------------------------------------------------------------------
# DB access (Supabase)
# ---------------------------------------------------------------------------
def _exact_sku(supabase, skus):
    if not skus:
        return []
    variants = list({*skus, *[s.lower() for s in skus]})
    return (
        supabase.table("products").select("*").in_("sku", variants).execute().data or []
    )


def _text_candidates(supabase, terms, limit=150):
    """ilike on search_blob (title + tags + type) for model/series/part terms."""
    terms = [re.sub(r"[(),*%]", " ", t).strip() for t in terms if t and t.strip()]
    terms = [t for t in terms if len(t) >= 2]
    if not terms:
        return []
    ors = ",".join(f"search_blob.ilike.*{t}*" for t in terms[:8])
    try:
        return (
            supabase.table("products").select("*").or_(ors).limit(limit).execute().data
            or []
        )
    except Exception as e:
        logger.warning("text candidate query failed: %s", e)
        return []


def _semantic(supabase, openai_client, query, k=SEMANTIC_K):
    emb = (
        openai_client.embeddings.create(
            model=EMBED_MODEL, dimensions=EMBED_DIMS, input=query
        )
        .data[0]
        .embedding
    )
    return (
        supabase.rpc("match_products", {"query_embedding": emb, "match_count": k})
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------------
# shaping
# ---------------------------------------------------------------------------
def _row(p, reason, web_verified, confidence_level):
    return {
        "title": p.get("title", ""),
        "sku": p.get("sku", ""),
        "price": float(p.get("price") or 0),
        "inventory": int(p.get("inventory") or 0),
        "tags": p.get("tags") or [],
        "weight": p.get("weight", ""),
        "weight_unit": p.get("weight_unit", "lb"),
        "type": p.get("type", ""),
        "description": p.get("description", ""),
        "product_url": p.get("product_url"),
        "match_reason": reason,
        "is_web_verified": web_verified,
        "is_live_data": True,
        "confidence_level": confidence_level,
    }


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def answer_query(
    supabase, openai_client, query: str, use_web_search: bool = True
) -> dict:
    intent = detect_intent(query)

    if intent == "greeting":
        return {
            "is_conversational": True,
            "response": "Hello! I can help you find parts for any Branson tractor. "
            "Tell me the model and part, or paste a SKU.",
            "suggestions": [
                "Find glow plugs",
                "Air filter for 2100",
                "Browse by model",
            ],
        }
    if intent.startswith("faq:"):
        ft = intent.split(":", 1)[1]
        return {
            "is_conversational": True,
            "response": FAQ_RESPONSES[ft]["response"],
            "suggestions": ["Browse parts", "Search by model", "Contact us"],
        }

    # ---- interpret --------------------------------------------------------
    interp = interpret_query_branson(openai_client, query)
    model = interp.get("model") or ""
    part_type = interp.get("part_type") or "unknown"
    is_series = bool(interp.get("is_series"))
    user_skus = _dedupe_upper(
        ([interp.get("specific_sku")] if interp.get("specific_sku") else [])
        + interp.get("all_part_numbers", [])
        + extract_skus(query)
    )

    # ---- web verification (optional) -------------------------------------
    web_sources, web_part_numbers, web_used = [], [], False
    low_conf = interp.get("confidence") != "high"
    if use_web_search and SERPER_API_KEY and (low_conf or is_series):
        wr = web_search_serper(query)
        if wr:
            web_used = True
            web_sources = [
                {"title": r["title"], "url": r["url"], "is_varner": r["is_varner"]}
                for r in wr[:5]
            ]
            web_part_numbers = extract_part_numbers_from_web(wr)

    # ---- gather candidates from the DB -----------------------------------
    web_hit_rows = _exact_sku(supabase, web_part_numbers)
    user_hit_rows = _exact_sku(supabase, user_skus)
    common_skus = (
        COMMON_BRANSON_PARTS.get(part_type.lower(), [])
        if part_type != "unknown"
        else []
    )

    model_terms = []
    if model:
        if is_series:
            prefix = re.sub(r"\D", "", model)[:2]
            model_terms += SERIES_MAP.get(prefix, []) + [prefix]
        else:
            model_terms.append(model)
    text_terms = model_terms + (
        [part_type] if part_type and part_type != "unknown" else []
    )

    candidates, seen = [], set()

    def add(rows):
        for p in rows:
            pid = p.get("id")
            if pid not in seen:
                seen.add(pid)
                candidates.append(p)

    add(web_hit_rows)
    add(user_hit_rows)
    add(_exact_sku(supabase, common_skus))
    add(_text_candidates(supabase, text_terms))
    add(_semantic(supabase, openai_client, query))

    web_ids = {p.get("id") for p in web_hit_rows}
    user_ids = {p.get("id") for p in user_hit_rows}

    # ---- score with validation -------------------------------------------
    matches = []
    for p in candidates:
        title = (p.get("title") or "").lower()
        tags_str = " ".join(p.get("tags") or []).lower()
        pid = p.get("id")
        score, reasons = 0, []

        pt_score, pt_valid = 0, True
        if part_type and part_type != "unknown":
            pt_score, pt_valid = validate_part_type_match(
                part_type, title, (p.get("type") or "").lower()
            )
            if not pt_valid:
                continue  # HARD REJECT

        is_web = pid in web_ids
        is_user = pid in user_ids
        if is_web:
            score += 5000
            reasons.append("🌐 Web-verified")
        if is_user:
            score += 3000
            reasons.append("🎯 Your SKU")
        score += pt_score
        if pt_score >= 1000:
            reasons.append(f"✓ {part_type}")

        model_score = 0
        if model:
            ml = model.lower()
            if is_series:
                prefix = re.sub(r"\D", "", model)[:2]
                if any(
                    m2 in tags_str or m2 in title for m2 in SERIES_MAP.get(prefix, [])
                ):
                    model_score = 800
                    reasons.append(f"✓ {model}")
            elif f"branson {ml}" in tags_str or ml in tags_str:
                model_score = 1000
                reasons.append(f"✓ Model {model}")
            elif f" {ml} " in title:
                model_score = 800
                reasons.append(f"~ Model {model}")
        score += model_score

        conf = calculate_match_confidence(score, is_web, is_user, pt_score, model_score)
        if score >= 500:
            row = _row(p, " | ".join(reasons[:4]) or "Related match", is_web, conf)
            row["match_score"] = score
            matches.append(row)

    matches.sort(key=lambda x: x["match_score"], reverse=True)

    interpretation = {
        "equipment": interp.get("equipment", f"Branson {model}" if model else ""),
        "part_type": part_type if part_type != "unknown" else "",
        "confidence": interp.get("confidence", "low"),
        "web_research": {"sources": web_sources},
    }

    if not matches:
        return {
            "query": query,
            "results": [],
            "search_confidence": 0,
            "web_search_used": web_used,
            "message": "No matches found. Try the model number, a different part name, or a SKU.",
            "suggestions": ["Search by SKU", "Search by model", "Browse common parts"],
            "interpretation": interpretation,
        }

    overall = calculate_overall_confidence(
        matches,
        bool(web_ids),
        bool(model),
        part_type not in ("", "unknown"),
        matches[0]["match_score"],
    )
    count = 1 if overall >= 90 else 3 if overall >= 70 else 5
    results = matches[:count]
    for r in results:
        r.pop("match_score", None)
        r.pop("confidence_level", None)

    return {
        "query": query,
        "results": results,
        "search_confidence": overall,
        "web_search_used": web_used,
        "interpretation": interpretation,
    }
