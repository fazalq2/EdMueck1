#!/usr/bin/env python3
"""
Shopify Product Crawler for varnerparts.com
Crawls all products, variants, collections, and creates a searchable map
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShopifyCrawler:
    """
    Intelligent crawler for Shopify stores
    Maps products, variants, collections, and metadata
    """

    def __init__(self, shop_name: str, access_token: str, api_version: str = "2023-10"):
        """
        Initialize Shopify crawler

        Args:
            shop_name: Shopify shop name (e.g., "varner-parts")
            access_token: Shopify Admin API access token
            api_version: Shopify API version (default: 2023-10)
        """
        self.shop_name = shop_name
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://{shop_name}.myshopify.com/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

        # Storage for crawled data
        self.products = []
        self.variants = []
        self.collections = []
        self.product_map = {}  # SKU -> Product mapping

    def crawl_all(self, delay: float = 0.5, resume: bool = True) -> Dict:
        """
        Crawl all Shopify data (products, collections, variants)

        Args:
            delay: Delay between API calls in seconds (rate limiting)
            resume: If True, will try to resume from cached data if available

        Returns:
            Dictionary with all crawled data
        """
        logger.info("🚀 Starting complete Shopify crawl...")
        start_time = time.time()

        # Check for existing partial crawl to resume
        cache_file = "shopify_crawl_data.json"
        if resume and os.path.exists(cache_file):
            try:
                logger.info("📂 Found existing crawl data, checking if usable...")
                self.load_from_file(cache_file)

                # Check if data is recent (less than 24 hours old)
                cache_age = datetime.now() - datetime.fromtimestamp(
                    os.path.getmtime(cache_file)
                )
                if cache_age < timedelta(hours=24) and len(self.products) > 0:
                    logger.info(
                        f"✅ Using cached data from {cache_age.seconds//3600}h ago ({len(self.products)} products)"
                    )

                    elapsed = time.time() - start_time
                    return {
                        "crawl_timestamp": datetime.now().isoformat(),
                        "shop_name": self.shop_name,
                        "products_count": len(self.products),
                        "variants_count": len(self.variants),
                        "collections_count": len(self.collections),
                        "elapsed_seconds": round(elapsed, 2),
                        "products": self.products,
                        "variants": self.variants,
                        "collections": self.collections,
                        "product_map": self.product_map,
                        "source": "cache",
                    }
            except Exception as e:
                logger.warning(f"⚠️  Could not load cache: {e}, starting fresh crawl")

        # Step 1: Crawl products
        self.products = self.crawl_products(delay=delay)

        # Save partial results after products (in case of later failure)
        logger.info("💾 Saving partial crawl results...")
        self.save_to_file(cache_file)

        # Step 2: Extract variants from products
        self.variants = self.extract_variants()

        # Step 3: Build SKU mapping
        self.product_map = self.build_product_map()

        # Step 4: Crawl collections (optional, less critical)
        try:
            self.collections = self.crawl_collections(delay=delay)
        except Exception as e:
            logger.warning(
                f"⚠️  Collection crawl failed: {e}, continuing without collections"
            )
            self.collections = []

        elapsed = time.time() - start_time

        result = {
            "crawl_timestamp": datetime.now().isoformat(),
            "shop_name": self.shop_name,
            "products_count": len(self.products),
            "variants_count": len(self.variants),
            "collections_count": len(self.collections),
            "elapsed_seconds": round(elapsed, 2),
            "products": self.products,
            "variants": self.variants,
            "collections": self.collections,
            "product_map": self.product_map,
            "source": "fresh_crawl",
        }

        # Save final results
        self.save_to_file(cache_file)

        logger.info(
            f"✅ Crawl complete! {len(self.products)} products, {len(self.variants)} variants in {elapsed:.1f}s"
        )

        return result

    def crawl_products(self, delay: float = 0.5, max_retries: int = 3) -> List[Dict]:
        """
        Crawl all products with pagination and retry logic

        Args:
            delay: Delay between API calls
            max_retries: Maximum retry attempts for failed requests

        Returns:
            List of all products with full details
        """
        logger.info("📦 Crawling products...")

        all_products = []
        page_info = None
        page_count = 0

        # Use 250 items per page (Shopify max)
        url = f"{self.base_url}/products.json?limit=250"

        while True:
            page_count += 1

            # Add page_info for pagination if available
            fetch_url = f"{url}&page_info={page_info}" if page_info else url

            logger.info(f"   Fetching page {page_count}...")

            # Retry logic for failed requests
            for attempt in range(max_retries):
                try:
                    response = requests.get(fetch_url, headers=self.headers, timeout=30)
                    response.raise_for_status()

                    data = response.json()
                    products = data.get("products", [])

                    if not products:
                        logger.info(f"   ℹ️  No more products on page {page_count}")
                        return all_products  # End of products

                    all_products.extend(products)
                    logger.info(
                        f"   ✓ Page {page_count}: {len(products)} products (total: {len(all_products)})"
                    )

                    # Check for next page
                    page_info = self._extract_next_page_info(
                        response.headers.get("Link", "")
                    )

                    if not page_info:
                        logger.info(f"   ℹ️  No more pages (reached end)")
                        return all_products  # End of pagination

                    # Rate limiting
                    time.sleep(delay)
                    break  # Success, exit retry loop

                except requests.exceptions.HTTPError as e:
                    if response.status_code in [502, 503, 504]:  # Server errors
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2  # Exponential backoff
                            logger.warning(
                                f"   ⚠️  Server error {response.status_code}, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(wait_time)
                        else:
                            logger.error(
                                f"   ✗ Max retries reached on page {page_count}, stopping crawl"
                            )
                            return all_products  # Return what we have so far
                    else:
                        logger.error(f"   ✗ HTTP error on page {page_count}: {e}")
                        return all_products

                except requests.exceptions.RequestException as e:
                    logger.error(f"   ✗ Error fetching page {page_count}: {e}")
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"   ⚠️  Retrying in 3s... (attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(3)
                    else:
                        return all_products

        logger.info(f"✅ Crawled {len(all_products)} products from {page_count} pages")
        return all_products

    def crawl_collections(self, delay: float = 0.5) -> List[Dict]:
        """
        Crawl all custom collections

        Args:
            delay: Delay between API calls

        Returns:
            List of all collections
        """
        logger.info("📚 Crawling collections...")

        all_collections = []
        page_info = None
        page_count = 0

        url = f"{self.base_url}/custom_collections.json?limit=250"

        while True:
            page_count += 1
            fetch_url = f"{url}&page_info={page_info}" if page_info else url

            logger.info(f"   Fetching collections page {page_count}...")

            try:
                response = requests.get(fetch_url, headers=self.headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                collections = data.get("custom_collections", [])

                if not collections:
                    break

                all_collections.extend(collections)
                logger.info(f"   ✓ Page {page_count}: {len(collections)} collections")

                page_info = self._extract_next_page_info(
                    response.headers.get("Link", "")
                )

                if not page_info:
                    break

                time.sleep(delay)

            except requests.exceptions.RequestException as e:
                logger.error(f"   ✗ Error fetching collections page {page_count}: {e}")
                break

        logger.info(f"✅ Crawled {len(all_collections)} collections")
        return all_collections

    def extract_variants(self) -> List[Dict]:
        """
        Extract all variants from crawled products

        Returns:
            List of variants with product context
        """
        logger.info("🔍 Extracting variants from products...")

        all_variants = []

        for product in self.products:
            product_title = product.get("title", "")
            product_handle = product.get("handle", "")
            product_id = product.get("id", "")
            product_tags = product.get("tags", "")
            product_type = product.get("product_type", "")

            variants = product.get("variants", [])

            for variant in variants:
                # Safely handle None values
                sku = variant.get("sku")
                sku = str(sku) if sku is not None else ""

                price = variant.get("price")
                price = str(price) if price is not None else "0"

                inventory_qty = variant.get("inventory_quantity")
                inventory_qty = int(inventory_qty) if inventory_qty is not None else 0

                weight = variant.get("weight")
                weight = weight if weight is not None else ""

                variant_data = {
                    # Variant info
                    "variant_id": variant.get("id"),
                    "sku": sku,
                    "price": price,
                    "inventory_quantity": inventory_qty,
                    "weight": weight,
                    "weight_unit": variant.get("weight_unit", "lb"),
                    # Product context
                    "product_id": product_id,
                    "product_title": product_title,
                    "product_handle": product_handle,
                    "product_tags": product_tags,
                    "product_type": product_type,
                    # Additional metadata
                    "created_at": variant.get("created_at"),
                    "updated_at": variant.get("updated_at"),
                }

                all_variants.append(variant_data)

        logger.info(f"✅ Extracted {len(all_variants)} variants")
        return all_variants

    def build_product_map(self) -> Dict[str, Dict]:
        """
        Build SKU -> Product mapping for fast lookups

        Returns:
            Dictionary mapping SKU to product info
        """
        logger.info("🗺️  Building SKU product map...")

        product_map = {}

        for variant in self.variants:
            sku = variant.get("sku")
            # Handle None and empty SKUs
            if sku and isinstance(sku, str):
                sku_normalized = sku.strip().upper()
                if sku_normalized:  # Only add non-empty SKUs
                    product_map[sku_normalized] = variant

        logger.info(f"✅ Built map with {len(product_map)} SKUs")
        return product_map

    def search_by_sku(self, sku: str) -> Optional[Dict]:
        """
        Search for product by SKU

        Args:
            sku: Product SKU to search for

        Returns:
            Product variant data or None
        """
        sku_normalized = sku.strip().upper()
        return self.product_map.get(sku_normalized)

    def search_by_title(self, query: str) -> List[Dict]:
        """
        Search products by title (fuzzy match)

        Args:
            query: Search query

        Returns:
            List of matching variants
        """
        query_lower = query.lower()
        matches = []

        for variant in self.variants:
            title_lower = variant.get("product_title", "").lower()
            if query_lower in title_lower:
                matches.append(variant)

        return matches

    def get_branson_parts(self) -> List[Dict]:
        """
        Filter and return only Branson tractor parts

        Returns:
            List of Branson-specific variants
        """
        branson_parts = []

        for variant in self.variants:
            tags = variant.get("product_tags", "").lower()
            title = variant.get("product_title", "").lower()

            if "branson" in tags or "branson" in title:
                branson_parts.append(variant)

        logger.info(f"🚜 Found {len(branson_parts)} Branson parts")
        return branson_parts

    def save_to_file(self, filename: str = "shopify_crawl_data.json"):
        """
        Save crawled data to JSON file

        Args:
            filename: Output filename
        """
        data = {
            "crawl_timestamp": datetime.now().isoformat(),
            "shop_name": self.shop_name,
            "products_count": len(self.products),
            "variants_count": len(self.variants),
            "collections_count": len(self.collections),
            "products": self.products,
            "variants": self.variants,
            "collections": self.collections,
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Saved crawl data to {filename}")

    def load_from_file(self, filename: str = "shopify_crawl_data.json"):
        """
        Load previously crawled data from file

        Args:
            filename: Input filename
        """
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.products = data.get("products", [])
        self.variants = data.get("variants", [])
        self.collections = data.get("collections", [])
        self.product_map = self.build_product_map()

        logger.info(f"📂 Loaded {len(self.products)} products from {filename}")

    def _extract_next_page_info(self, link_header: str) -> Optional[str]:
        """
        Extract page_info for next page from Link header

        Args:
            link_header: Link header from response

        Returns:
            page_info token or None
        """
        if not link_header:
            return None

        # Parse Link header for rel="next"
        # Format: <url>; rel="next"
        import re

        match = re.search(r'<[^>]+page_info=([^>]+)>;\s*rel="next"', link_header)
        if match:
            return match.group(1)

        return None


def main():
    """
    Example usage of Shopify crawler
    """
    # Load credentials from environment or .env file
    from dotenv import load_dotenv

    load_dotenv()

    shop_name = os.getenv("SHOP_NAME", "varner-parts")
    access_token = os.getenv("ACCESS_TOKEN")
    api_version = os.getenv("API_VERSION", "2023-10")

    if not access_token:
        logger.error("❌ ACCESS_TOKEN not found in environment")
        return

    # Initialize crawler
    crawler = ShopifyCrawler(
        shop_name=shop_name, access_token=access_token, api_version=api_version
    )

    # Crawl all data
    result = crawler.crawl_all(delay=0.5)

    # Save to file
    crawler.save_to_file("shopify_crawl_data.json")

    # Example searches
    logger.info("\n📊 Sample Data:")
    logger.info(f"Total products: {result['products_count']}")
    logger.info(f"Total variants: {result['variants_count']}")
    logger.info(f"Total collections: {result['collections_count']}")

    # Find Branson parts
    branson_parts = crawler.get_branson_parts()
    logger.info(f"\n🚜 Branson Parts: {len(branson_parts)}")

    if branson_parts:
        logger.info("\nSample Branson parts:")
        for part in branson_parts[:5]:
            logger.info(f"  - {part['product_title']} (SKU: {part['sku']})")

    # Example SKU search
    if crawler.variants:
        sample_sku = crawler.variants[0].get("sku")
        if sample_sku:
            result = crawler.search_by_sku(sample_sku)
            logger.info(f"\n🔍 Search by SKU '{sample_sku}':")
            logger.info(f"  Found: {result['product_title']}")


if __name__ == "__main__":
    main()
