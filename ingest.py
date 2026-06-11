#!/usr/bin/env python3
"""
Offline ingest pipeline  ──  run this whenever the catalogue changes.

    crawl Shopify  ->  flatten to variants  ->  embed (OpenAI)  ->  upsert to Supabase

Run locally or in a scheduled GitHub Action. The web app reads the result from
Supabase; it never runs any of this at request time.

Usage:
    python ingest.py              # crawl fresh, embed, upsert
    python ingest.py --cache out.json   # reuse a saved crawl instead of re-crawling

Required env (see .env.example):
    SHOP_NAME, ACCESS_TOKEN          Shopify admin API
    OPENAI_API_KEY                   embeddings
    SUPABASE_URL, SUPABASE_SERVICE_KEY   destination DB (service key = write access)
"""

import os
import re
import sys
import html
import argparse
import logging

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

from shopify_crawler import ShopifyCrawler

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "512"))
STORE_DOMAIN = os.getenv("STORE_DOMAIN", "varnerparts.com")
BATCH = 100  # OpenAI embeddings + Supabase upsert batch size

_TAG = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    """Body HTML -> plain text, so descriptions stay small in the 500 MB DB."""
    if not s:
        return ""
    return html.unescape(_TAG.sub(" ", s)).strip()[:2000]


def flatten(products) -> list[dict]:
    """Shopify products -> one record per variant, in our DB shape."""
    rows = []
    for p in products:
        title = p.get("title", "")
        handle = p.get("handle", "")
        ptype = p.get("product_type", "")
        tags = [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()]
        desc = strip_html(p.get("body_html", ""))
        for v in p.get("variants", []):
            vid = v.get("id")
            if vid is None:
                continue
            rows.append(
                {
                    "id": int(vid),
                    "sku": (v.get("sku") or "").strip(),
                    "title": title,
                    "price": float(v.get("price") or 0),
                    "inventory": int(v.get("inventory_quantity") or 0),
                    "tags": tags,
                    "weight": str(v.get("weight") or ""),
                    "weight_unit": v.get("weight_unit") or "lb",
                    "type": ptype,
                    "description": desc,
                    "product_handle": handle,
                    "product_url": (
                        f"https://{STORE_DOMAIN}/products/{handle}" if handle else None
                    ),
                    # title + tags + type, lower-cased, for model/series/category ilike
                    "search_blob": " ".join([title, " ".join(tags), ptype]).lower(),
                }
            )
    return rows


def embed_text(row: dict) -> str:
    """What we actually feed the embedding model — title + tags + type + desc."""
    parts = [row["title"], " ".join(row["tags"]), row["type"], row["description"]]
    return " | ".join(p for p in parts if p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", help="path to a saved crawl json (skip crawling)")
    ap.add_argument("--save", help="save the crawl to this path after fetching")
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    for key in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if not os.getenv(key):
            sys.exit(f"❌ Missing env var: {key}")

    crawler = ShopifyCrawler(
        shop_name=os.getenv("SHOP_NAME", "varner-parts"),
        access_token=os.getenv("ACCESS_TOKEN", ""),
        api_version=os.getenv("API_VERSION", "2023-10"),
    )

    if args.cache and os.path.exists(args.cache):
        crawler.load_from_file(args.cache)
    else:
        if not os.getenv("ACCESS_TOKEN"):
            sys.exit("❌ ACCESS_TOKEN required to crawl (or pass --cache).")
        crawler.crawl_all(delay=args.delay)
        if args.save:
            crawler.save_to_file(args.save)

    rows = flatten(crawler.products)
    logger.info("Flattened to %d variants", len(rows))
    if not rows:
        sys.exit("No variants found — nothing to ingest.")

    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    supabase = create_client(
        os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    )

    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        resp = openai_client.embeddings.create(
            model=EMBED_MODEL,
            dimensions=EMBED_DIMS,
            input=[embed_text(r) for r in chunk],
        )
        for r, e in zip(chunk, resp.data):
            r["embedding"] = e.embedding

        supabase.table("products").upsert(chunk).execute()
        total += len(chunk)
        logger.info("Upserted %d / %d", total, len(rows))

    logger.info("✅ Ingest complete: %d variants in Supabase", total)


if __name__ == "__main__":
    main()
