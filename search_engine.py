#!/usr/bin/env python3
"""
search_engine.py — Branson parts search/answer logic for app.py.

Compatible with:
    from search_engine import answer_query
    from search_engine import BRANSON_MODELS

Main fixes included:
  - Normalized SKU lookup: V2184610025 matches V218-461-0025
  - Removes fake NULL/null SKU searches
  - Prevents exact model "2100" from becoming fake series prefix "21"
  - Searches title, description, type, sku, and search_blob
  - Better model scoring using title/tags/description/search_blob
  - Safer candidate logging
  - Keeps same response shape expected by frontend/App.js
"""

import os
import re
import json
import logging
from typing import Any, Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "512"))
INTERP_MODEL = os.getenv("INTERP_MODEL", "gpt-4o-mini")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
STORE_DOMAIN = os.getenv("STORE_DOMAIN", "varnerparts.com")
SEMANTIC_K = int(os.getenv("SEMANTIC_K", "30"))

# ---------------------------------------------------------------------------
# Branson knowledge base
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
    "20": [
        "2100",
        "2205",
        "2205H",
        "2400",
        "2400H",
        "2505H",
        "2515",
        "2515H",
        "2515R",
        "2610",
        "2610R",
        "2800",
        "2800H",
    ],
    "30": ["3015", "3015C", "3015R", "3520", "3520H", "3520R", "3725", "3725CH"],
    "40": ["4015", "4015C", "4015R", "4520", "4520C", "4520R"],
    "50": ["5220", "5220C", "5220CH", "5220R", "5825", "5825C", "5835", "5835R"],
    "60": ["6225", "6225C", "6225R"],
    "70": ["7845", "7845C", "7845R"],
    "80": ["8050", "8050C", "8050R"],
}

COMMON_BRANSON_PARTS = {
    "glow plug": ["HT17160000A3", "V2184610025", "V218-461-0025"],
    "air filter": ["EA00000985A"],
    "fuel filter": ["23000-022000"],
    "oil filter": ["A04_002"],
    "fuel injection valve": ["HK12020000A3"],
}

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
        "related": ["heating plug", "heater plug"],
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
    r"\bV\d{3}[-\s]?\d{3}[-\s]?\d{4}\b",
    r"\b\d{5}-\d{5,6}\b",
    r"\b[A-Z]\d{2}_\d{3}\b",
    r"\b[A-Z]{2}\d{5}\b",
    r"\b[A-Z]\d{3,4}\b",
]

WEB_PART_PATTERNS = SKU_PATTERNS + [
    r"\(([A-Z]{2}\d{5})\)",
    r"\(([A-Z]\d{3,4})\)",
    r"\bP/?N[\s:#]*([A-Z0-9\-_]+)\b",
    r"\bSKU[\s:#]*([A-Z0-9\-_]+)\b",
]

NULLISH = {"", "NULL", "NONE", "N/A", "NA", "NIL", "UNDEFINED"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_code(value: Any) -> str:
    """
    Normalize part/SKU numbers.

    V2184610025      -> v2184610025
    V218-461-0025   -> v2184610025
    A04_002         -> a04002
    """
    value = _safe_str(value)
    if value.upper() in NULLISH:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", value).lower()


def _dedupe_upper(items: List[Any]) -> List[str]:
    seen, out = set(), []
    for item in items or []:
        s = _safe_str(item)
        if s.upper() in NULLISH:
            continue
        s = s.upper()
        if s and len(s) >= 3 and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _clean_search_term(value: Any) -> str:
    value = _safe_str(value)
    value = re.sub(r"[(),*%;]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _literal_model_from_query(query: str) -> str | None:
    """
    Find exact Branson model mentioned by the user.

    This prevents this bad case:
        query: Branson 2100 glow plug
        LLM: model=2100, is_series=true
        old code: prefix=21, search_blob.ilike.*21*

    Now exact 2100 remains exact 2100.
    """
    q = query.lower()

    for model in sorted(BRANSON_MODELS, key=len, reverse=True):
        m = model.lower()

        if re.search(rf"\b{re.escape(m)}\b", q):
            return model

        # User may write 2205 while catalogue model is 2205H.
        base = re.sub(r"[a-z]+$", "", m)
        if len(base) >= 4 and re.search(rf"\b{re.escape(base)}\b", q):
            return model

    return None


def _series_prefix_from_text(value: str) -> str:
    """
    Extract series prefix safely.

    Accepts:
      20 Series -> 20
      Branson 20 -> 20
      20 -> 20

    Does not treat exact model 2100 as prefix 21.
    """
    value = _safe_str(value).lower()

    # Prefer explicit "20 series" style.
    m = re.search(r"\b(20|30|40|50|60|70|80)\s*(series)?\b", value)
    if m:
        return m.group(1)

    digits = re.sub(r"\D", "", value)
    if digits in SERIES_MAP:
        return digits

    return ""


def _product_haystack(product: Dict[str, Any]) -> str:
    tags = product.get("tags") or []
    if isinstance(tags, list):
        tags_str = " ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)

    parts = [
        product.get("sku"),
        product.get("title"),
        product.get("type"),
        product.get("description"),
        product.get("search_blob"),
        product.get("product_handle"),
        product.get("product_url"),
        tags_str,
    ]
    return " ".join(_safe_str(p).lower() for p in parts if p)


def _contains_model(haystack: str, model: str) -> bool:
    if not model:
        return False

    m = model.lower()
    base = re.sub(r"[a-z]+$", "", m)

    patterns = [
        rf"\bbranson\s+{re.escape(m)}\b",
        rf"\b{re.escape(m)}\b",
    ]

    if base and base != m and len(base) >= 4:
        patterns.extend(
            [
                rf"\bbranson\s+{re.escape(base)}\b",
                rf"\b{re.escape(base)}\b",
            ]
        )

    return any(re.search(p, haystack) for p in patterns)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Intent
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
# LLM interpretation
# ---------------------------------------------------------------------------

INTERP_SYSTEM = """You are a Branson tractor parts specialist.

Models:
2100, 2205H, 2400, 2400H, 2515H, 2515R, 2505H, 2610R, 2800, 2800H,
3015C, 3015R, 3520H, 3520R, 3725CH, 4015C, 4015R, 4520C, 4520R,
5220C, 5220CH, 5220R, 5825C, 5835R, 6225C, 6225R, 7845C, 7845R,
8050C, 8050R.

Series:
"20 Series" = 2100, 2205H, 2400, 2400H, 2505H, 2515H, 2515R, 2610R, 2800, 2800H.
"30 Series" = 3015C, 3015R, 3520H, 3520R, 3725CH.
"40 Series" = 4015C, 4015R, 4520C, 4520R.
"50 Series" = 5220C, 5220CH, 5220R, 5825C, 5835R.
"60 Series" = 6225C, 6225R.
"70 Series" = 7845C, 7845R.
"80 Series" = 8050C, 8050R.

Important:
- If the user mentions an exact model like 2100, 2400, 2515H, 3725CH, etc., set is_series=false.
- Only set is_series=true when the user explicitly asks for a series, like "20 series".
- Extract part numbers/SKUs anywhere in the query, including codes in parentheses like (V2184610025), (B207).
- Be specific about part_type, e.g. "hex bolt", "glow plug", "canopy", "air filter".

Respond ONLY with JSON, no markdown:
{
  "equipment": "Branson <model or series>",
  "model": "model or series name",
  "part_type": "specific part",
  "specific_sku": "primary SKU or null",
  "all_part_numbers": ["..."],
  "search_terms": ["..."],
  "confidence": "high|medium|low",
  "is_series": true|false
}
"""


def interpret_query_branson(openai_client, query: str) -> Dict[str, Any]:
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

        txt = resp.choices[0].message.content.strip()
        txt = re.sub(r"^```json\s*|\s*```$", "", txt)
        m = re.search(r"\{.*\}", txt, re.DOTALL)

        interp = json.loads(m.group()) if m else dict(fallback)

        interp.setdefault("model", "")
        interp.setdefault("part_type", "unknown")
        interp.setdefault("specific_sku", None)
        interp.setdefault("all_part_numbers", [])
        interp.setdefault("is_series", False)
        interp.setdefault("search_terms", query.split())
        interp.setdefault("confidence", "low")

        if _safe_str(interp.get("specific_sku")).upper() in NULLISH:
            interp["specific_sku"] = None

        if not isinstance(interp.get("all_part_numbers"), list):
            interp["all_part_numbers"] = []

        if not isinstance(interp.get("search_terms"), list):
            interp["search_terms"] = query.split()

        logger.info("interpretation: %s", json.dumps(interp))
        return interp

    except Exception as e:
        logger.warning("interpret failed: %s", e)
        return fallback


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


def web_search_serper(
    query: str, num: int = 8, prioritize_varner: bool = True
) -> List[Dict[str, Any]]:
    if not SERPER_API_KEY:
        return []

    sq = query
    if prioritize_varner and "varnerparts" not in query.lower():
        sq = f"{query} site:{STORE_DOMAIN} OR {query}"

    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": sq, "num": num},
            timeout=10,
        )

        if r.status_code != 200:
            logger.warning(
                "Serper returned status=%s body=%s", r.status_code, r.text[:300]
            )
            return []

        out = []
        for it in r.json().get("organic", [])[:num]:
            url = it.get("link", "") or ""
            out.append(
                {
                    "title": it.get("title", "") or "",
                    "snippet": it.get("snippet", "") or "",
                    "url": url,
                    "is_varner": STORE_DOMAIN.lower() in url.lower(),
                }
            )

        out.sort(key=lambda x: 0 if x["is_varner"] else 1)
        return out

    except Exception as e:
        logger.warning("Serper failed: %s", e)
        return []


def extract_part_numbers_from_web(results: List[Dict[str, Any]]) -> List[str]:
    found = []

    for r in results or []:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"

        for pat in WEB_PART_PATTERNS:
            for m in re.findall(pat, text, re.IGNORECASE):
                if isinstance(m, str):
                    found.append(m)
                else:
                    found.append(next((x for x in m if x), ""))

    return _dedupe_upper(found)


def extract_skus(query: str) -> List[str]:
    found = []

    for pat in SKU_PATTERNS:
        found.extend(re.findall(pat, query, re.IGNORECASE))

    return _dedupe_upper(found)


# ---------------------------------------------------------------------------
# Part-type validation
# ---------------------------------------------------------------------------


def validate_part_type_match(
    requested_type: str,
    product_title: str,
    product_type: str,
    product_haystack: str = "",
) -> Tuple[int, bool]:
    """
    Returns (score, is_valid).

    is_valid=False means hard reject for clearly wrong categories.
    """
    requested = (requested_type or "").lower().strip()
    title = (product_title or "").lower()
    ptype = (product_type or "").lower()
    haystack = (product_haystack or f"{title} {ptype}").lower()

    if not requested or requested == "unknown":
        return (0, True)

    config = None

    for name, cfg in PART_CATEGORIES.items():
        if name in requested or any(t in requested for t in cfg.get("exact", [])):
            config = cfg
            break

    if not config:
        words = [w for w in requested.split() if len(w) > 1]

        if words and all(w in haystack for w in words):
            return (1000, True)

        if any(w in haystack for w in words):
            return (500, True)

        # Do not hard reject unknown category too aggressively.
        return (0, True)

    # Exclusions should mainly check title/type, not full description,
    # because description may contain accessory/related text.
    title_type = f"{title} {ptype}"

    for ex in config.get("exclude", []):
        if re.search(rf"\b{re.escape(ex)}\b", title_type):
            return (0, False)

    for term in config.get("exact", []):
        if term in title_type or term in haystack:
            return (2000, True)

    for term in config.get("related", []):
        if term in title_type or term in haystack:
            return (1200, True)

    words = [w for w in requested.split() if len(w) > 1]
    if any(w in title_type for w in words):
        return (800, True)

    if any(w in haystack for w in words):
        return (500, True)

    return (0, False)


def calculate_match_confidence(
    score: int,
    has_web: bool,
    has_user_sku: bool,
    part_type_score: int,
    model_score: int,
) -> int:
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


def calculate_overall_confidence(
    matches: List[Dict[str, Any]],
    has_web: bool,
    has_model: bool,
    has_part_type: bool,
    top_score: int,
) -> int:
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
# Supabase DB access
# ---------------------------------------------------------------------------


def _exact_sku(supabase, skus: List[str]) -> List[Dict[str, Any]]:
    """
    Robust SKU lookup.

    Uses sku_norm if your DB has it. Falls back to raw sku lookup if not.

    Recommended SQL:
        alter table products add column if not exists sku_norm text;
        update products
        set sku_norm = lower(regexp_replace(coalesce(sku, ''), '[^a-zA-Z0-9]', '', 'g'));
        create index if not exists products_sku_norm_idx on products (sku_norm);
    """
    skus = _dedupe_upper(skus)

    if not skus:
        return []

    rows, seen = [], set()

    def add(found):
        for p in found or []:
            pid = p.get("id")
            if pid not in seen:
                seen.add(pid)
                rows.append(p)

    norm_values = list({_norm_code(s) for s in skus if _norm_code(s)})

    # Best path: sku_norm.
    if norm_values:
        try:
            data = (
                supabase.table("products")
                .select("*")
                .in_("sku_norm", norm_values)
                .execute()
                .data
                or []
            )
            add(data)
        except Exception as e:
            # This allows your app to keep working even before sku_norm exists.
            logger.warning(
                "sku_norm lookup failed; falling back to raw sku. error=%s", e
            )

    # Fallback: exact sku variants.
    variants = list(
        {
            *skus,
            *[s.lower() for s in skus],
            *[s.upper() for s in skus],
        }
    )

    try:
        data = (
            supabase.table("products").select("*").in_("sku", variants).execute().data
            or []
        )
        add(data)
    except Exception as e:
        logger.warning("raw sku lookup failed: %s", e)

    logger.info(
        "exact sku lookup skus=%s norm=%s rows=%s", skus, norm_values, len(rows)
    )
    return rows


def _text_candidates(
    supabase, terms: List[str], limit: int = 200
) -> List[Dict[str, Any]]:
    """
    Text search across:
      - search_blob
      - title
      - description
      - type
      - sku

    This is intentionally broad because product data may store compatibility
    in different fields.
    """
    cleaned = []

    for t in terms or []:
        t = _clean_search_term(t)

        if len(t) >= 2:
            cleaned.append(t)

    cleaned = list(dict.fromkeys(cleaned))[:8]

    if not cleaned:
        return []

    ors = []

    for t in cleaned:
        ors.extend(
            [
                f"search_blob.ilike.*{t}*",
                f"title.ilike.*{t}*",
                f"description.ilike.*{t}*",
                f"type.ilike.*{t}*",
                f"sku.ilike.*{t}*",
            ]
        )

    try:
        rows = (
            supabase.table("products")
            .select("*")
            .or_(",".join(ors))
            .limit(limit)
            .execute()
            .data
            or []
        )

        logger.info("text candidates terms=%s rows=%s", cleaned, len(rows))
        return rows

    except Exception as e:
        logger.warning("text candidate query failed: %s", e)
        return []


def _semantic(
    supabase, openai_client, query: str, k: int = SEMANTIC_K
) -> List[Dict[str, Any]]:
    try:
        emb = (
            openai_client.embeddings.create(
                model=EMBED_MODEL,
                dimensions=EMBED_DIMS,
                input=query,
            )
            .data[0]
            .embedding
        )

        rows = (
            supabase.rpc("match_products", {"query_embedding": emb, "match_count": k})
            .execute()
            .data
            or []
        )

        logger.info("semantic candidates rows=%s", len(rows))
        return rows

    except Exception as e:
        logger.warning("semantic search failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _row(
    product: Dict[str, Any], reason: str, web_verified: bool, confidence_level: int
) -> Dict[str, Any]:
    return {
        "title": product.get("title", "") or "",
        "sku": product.get("sku", "") or "",
        "price": _to_float(product.get("price"), 0.0),
        "inventory": _to_int(product.get("inventory"), 0),
        "tags": product.get("tags") or [],
        "weight": product.get("weight", "") or "",
        "weight_unit": product.get("weight_unit", "lb") or "lb",
        "type": product.get("type", "") or "",
        "description": product.get("description", "") or "",
        "product_url": product.get("product_url"),
        "match_reason": reason,
        "is_web_verified": web_verified,
        "is_live_data": True,
        "confidence_level": confidence_level,
    }


def _no_matches_response(
    query: str, interpretation: Dict[str, Any], web_used: bool
) -> Dict[str, Any]:
    return {
        "query": query,
        "results": [],
        "search_confidence": 0,
        "web_search_used": web_used,
        "message": "No matches found. Try the model number, a different part name, or a SKU.",
        "suggestions": ["Search by SKU", "Search by model", "Browse common parts"],
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Main entry point used by app.py
# ---------------------------------------------------------------------------


def answer_query(
    supabase, openai_client, query: str, use_web_search: bool = True
) -> Dict[str, Any]:
    intent = detect_intent(query)

    if intent == "greeting":
        return {
            "is_conversational": True,
            "response": (
                "Hello! I can help you find parts for any Branson tractor. "
                "Tell me the model and part, or paste a SKU."
            ),
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

    # -----------------------------------------------------------------------
    # 1) Interpret query
    # -----------------------------------------------------------------------
    interp = interpret_query_branson(openai_client, query)

    model = _safe_str(interp.get("model"))
    part_type = _safe_str(interp.get("part_type")) or "unknown"
    is_series = bool(interp.get("is_series"))

    # Strong rule: user exact model beats LLM series mistake.
    literal_model = _literal_model_from_query(query)

    if literal_model:
        model = literal_model
        is_series = False
        interp["model"] = literal_model
        interp["equipment"] = f"Branson {literal_model}"
        interp["is_series"] = False

    specific_sku = interp.get("specific_sku")
    all_part_numbers = interp.get("all_part_numbers") or []

    user_skus = _dedupe_upper(
        ([specific_sku] if specific_sku else [])
        + all_part_numbers
        + extract_skus(query)
    )

    # -----------------------------------------------------------------------
    # 2) Optional web verification
    # -----------------------------------------------------------------------
    web_sources, web_part_numbers, web_used = [], [], False
    low_conf = interp.get("confidence") != "high"

    if use_web_search and SERPER_API_KEY and (low_conf or is_series):
        wr = web_search_serper(query)

        if wr:
            web_used = True
            web_sources = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "is_varner": bool(r.get("is_varner")),
                }
                for r in wr[:5]
            ]
            web_part_numbers = extract_part_numbers_from_web(wr)

    # -----------------------------------------------------------------------
    # 3) Candidate gathering
    # -----------------------------------------------------------------------
    web_hit_rows = _exact_sku(supabase, web_part_numbers)
    user_hit_rows = _exact_sku(supabase, user_skus)

    common_skus = []
    if part_type and part_type != "unknown":
        common_skus = COMMON_BRANSON_PARTS.get(part_type.lower(), [])

    common_hit_rows = _exact_sku(supabase, common_skus)

    model_terms = []

    if model:
        if is_series:
            prefix = _series_prefix_from_text(model)

            if prefix:
                model_terms.extend(SERIES_MAP.get(prefix, []))
                model_terms.append(prefix)
                model_terms.append(f"{prefix} Series")
            else:
                model_terms.append(model)
        else:
            model_terms.append(model)

            # Useful phrase search
            if not model.lower().startswith("branson"):
                model_terms.append(f"Branson {model}")

    part_terms = []

    if part_type and part_type != "unknown":
        part_terms.append(part_type)

    for term in interp.get("search_terms") or []:
        cleaned = _clean_search_term(term)
        if cleaned and cleaned.lower() not in {"branson", "tractor", "series"}:
            part_terms.append(cleaned)

    # Include SKU text terms too, in case SKU exists in title/description/search_blob instead of sku.
    text_terms = model_terms + part_terms + user_skus + web_part_numbers

    text_hit_rows = _text_candidates(supabase, text_terms)
    semantic_rows = _semantic(supabase, openai_client, query)

    logger.info(
        "candidate counts: web=%s user=%s common=%s text=%s semantic=%s",
        len(web_hit_rows),
        len(user_hit_rows),
        len(common_hit_rows),
        len(text_hit_rows),
        len(semantic_rows),
    )

    candidates, seen = [], set()

    def add(rows: List[Dict[str, Any]]):
        for p in rows or []:
            pid = p.get("id")

            if pid is None:
                # Very rare fallback if id is missing.
                pid = f"{p.get('sku', '')}|{p.get('title', '')}"

            if pid not in seen:
                seen.add(pid)
                candidates.append(p)

    add(web_hit_rows)
    add(user_hit_rows)
    add(common_hit_rows)
    add(text_hit_rows)
    add(semantic_rows)

    logger.info("total deduped candidates=%s", len(candidates))

    web_ids = {p.get("id") for p in web_hit_rows}
    user_ids = {p.get("id") for p in user_hit_rows}
    common_ids = {p.get("id") for p in common_hit_rows}

    # -----------------------------------------------------------------------
    # 4) Score and validate
    # -----------------------------------------------------------------------
    matches = []

    for p in candidates:
        pid = p.get("id")
        title = (p.get("title") or "").lower()
        ptype = (p.get("type") or "").lower()
        haystack = _product_haystack(p)

        score = 0
        reasons = []

        pt_score, pt_valid = 0, True

        if part_type and part_type != "unknown":
            pt_score, pt_valid = validate_part_type_match(
                part_type,
                title,
                ptype,
                haystack,
            )

            if not pt_valid:
                logger.info(
                    "reject wrong part type: sku=%s title=%s requested=%s",
                    p.get("sku"),
                    p.get("title"),
                    part_type,
                )
                continue

        is_web = pid in web_ids
        is_user = pid in user_ids
        is_common = pid in common_ids

        if is_web:
            score += 5000
            reasons.append("🌐 Web-verified")

        if is_user:
            score += 3500
            reasons.append("🎯 SKU match")

        if is_common:
            score += 1800
            reasons.append("✓ Common Branson part")

        score += pt_score

        if pt_score >= 2000:
            reasons.append(f"✓ Exact {part_type}")
        elif pt_score >= 1200:
            reasons.append(f"✓ Related {part_type}")
        elif pt_score >= 500:
            reasons.append(f"~ Possible {part_type}")

        model_score = 0

        if model:
            if is_series:
                prefix = _series_prefix_from_text(model)
                series_models = SERIES_MAP.get(prefix, []) if prefix else []

                if any(_contains_model(haystack, m) for m in series_models):
                    model_score = 900
                    reasons.append(f"✓ {model}")

                elif prefix and re.search(
                    rf"\b{re.escape(prefix)}\s*series\b", haystack
                ):
                    model_score = 800
                    reasons.append(f"✓ {prefix} Series")
            else:
                if _contains_model(haystack, model):
                    model_score = 1000
                    reasons.append(f"✓ Model {model}")

        score += model_score

        # Semantic similarity from RPC, if present.
        similarity = p.get("similarity")
        if similarity is not None:
            try:
                sim = float(similarity)
                if sim >= 0.80:
                    score += 500
                    reasons.append("✓ Semantic match")
                elif sim >= 0.70:
                    score += 250
            except Exception:
                pass

        # If SKU was directly requested/found, exact SKU match should be enough.
        # If not, require at least some part/model/text confidence.
        if score >= 500:
            conf = calculate_match_confidence(
                score, is_web, is_user, pt_score, model_score
            )

            row = _row(
                p,
                " | ".join(reasons[:5]) or "Related match",
                is_web,
                conf,
            )
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
        logger.info(
            "no matches after scoring query=%s model=%s part_type=%s user_skus=%s text_terms=%s",
            query,
            model,
            part_type,
            user_skus,
            text_terms,
        )
        return _no_matches_response(query, interpretation, web_used)

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
