#!/usr/bin/env python3
"""
Shopify crawler — OFFLINE INGEST ONLY.

This is NOT imported by the web app. It runs on your machine (or a GitHub
Action) to pull the catalogue, after which ingest.py embeds + pushes the data
to the vector DB. The web app never crawls Shopify and never holds the
catalogue in memory.

Two fixes vs. the original:
  1. delay defaults to 0.6s  -> stays under Shopify's ~2 req/s REST limit
     (the old 0.3s caused the 429 you saw on page 10).
  2. cache_path is configurable and defaults to the current working directory,
     so it never tries to write to a read-only location.
"""

import os
import re
import json
import time
import logging
from typing import List, Dict, Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShopifyCrawler:
    def __init__(self, shop_name: str, access_token: str, api_version: str = "2023-10"):
        self.shop_name = shop_name
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        self.products: List[Dict] = []

    # -- crawling -----------------------------------------------------------
    def crawl_products(self, delay: float = 0.6, max_retries: int = 5) -> List[Dict]:
        logger.info("📦 Crawling products (delay=%.2fs)...", delay)
        all_products: List[Dict] = []
        url = f"{self.base_url}/products.json?limit=250"
        fetch_url = url
        page = 0

        while True:
            page += 1
            for attempt in range(max_retries):
                try:
                    r = requests.get(fetch_url, headers=self.headers, timeout=30)

                    # Respect Shopify's rate-limit signal explicitly.
                    if r.status_code == 429:
                        wait = float(r.headers.get("Retry-After", 2 * (attempt + 1)))
                        logger.warning("   429 throttled, sleeping %.1fs", wait)
                        time.sleep(wait)
                        continue

                    r.raise_for_status()
                    batch = r.json().get("products", [])
                    if not batch:
                        logger.info("✅ Done: %d products", len(all_products))
                        return all_products

                    all_products.extend(batch)
                    logger.info(
                        "   page %d: +%d (total %d)",
                        page,
                        len(batch),
                        len(all_products),
                    )

                    nxt = self._next_link(r.headers.get("Link", ""))
                    if not nxt:
                        logger.info("✅ Done: %d products", len(all_products))
                        return all_products
                    fetch_url = nxt
                    time.sleep(delay)
                    break
                except requests.exceptions.RequestException as e:
                    if attempt < max_retries - 1:
                        time.sleep(2 * (attempt + 1))
                    else:
                        logger.error("   giving up on page %d: %s", page, e)
                        return all_products

    def crawl_all(self, delay: float = 0.6) -> List[Dict]:
        self.products = self.crawl_products(delay=delay)
        return self.products

    # -- persistence --------------------------------------------------------
    def save_to_file(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"products": self.products}, f, ensure_ascii=False)
        logger.info("💾 Saved %d products to %s", len(self.products), path)

    def load_from_file(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self.products = json.load(f).get("products", [])
        logger.info("📂 Loaded %d products from %s", len(self.products), path)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _next_link(link_header: str) -> Optional[str]:
        if not link_header:
            return None
        m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        return m.group(1) if m else None
