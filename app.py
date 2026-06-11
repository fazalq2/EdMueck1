#!/usr/bin/env python3
"""
Branson Tractor Parts - COMPLETE SYSTEM
AI Chatbot + Search Engine + Live Shopify Integration
"""

import sys
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import json
import os
from openai import OpenAI
import re
from dotenv import load_dotenv
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from shopify_crawler import ShopifyCrawler
from search_engine import SemanticIndex, answer_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
CORS(app)

# ============================================================================
# API KEYS & CONFIGURATION
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BING_API_KEY = os.getenv("BING_API_KEY")

# Shopify Configuration
SHOP_NAME = os.getenv("SHOP_NAME", "varner-parts")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
API_VERSION = os.getenv("API_VERSION", "2023-10")

# Cache / crawl control (development-friendly)
# How old the cached crawl file may be before a fresh crawl is triggered.
CACHE_MAX_AGE_HOURS = float(os.getenv("CACHE_MAX_AGE_HOURS", "24"))
# If True, always crawl fresh from Shopify on startup (ignores cache).
# Leave False during development so restarts are instant.
FORCE_REFRESH_ON_START = os.getenv("FORCE_REFRESH_ON_START", "false").lower() == "true"


def ask_shopify_cache_choice():
    """
    Ask on app startup whether to fetch fresh Shopify data or reuse old cache.
    Returns True = fetch fresh from Shopify
    Returns False = use existing cache with no time limit
    """

    # Optional .env override:
    # SHOPIFY_CACHE_STARTUP_MODE=ask
    # SHOPIFY_CACHE_STARTUP_MODE=refresh
    # SHOPIFY_CACHE_STARTUP_MODE=cache
    mode = os.getenv("SHOPIFY_CACHE_STARTUP_MODE", "ask").strip().lower()

    if mode in {"refresh", "fresh", "new", "force", "true", "1", "yes", "y"}:
        return True

    if mode in {"cache", "old", "reuse", "false", "0", "no", "n"}:
        return False

    # If app is running somewhere non-interactive like gunicorn/server,
    # do not block startup waiting for input.
    if not sys.stdin.isatty():
        logger.info(
            "No interactive terminal detected. Reusing Shopify cache if available."
        )
        return False

    while True:
        choice = (
            input(
                "\nShopify cache option:\n"
                "  1) Fetch NEW cache from Shopify\n"
                "  2) Use OLD cache file without time limit\n"
                "Choose 1 or 2 [2]: "
            )
            .strip()
            .lower()
        )

        if choice in {"", "2", "cache", "old", "reuse", "no", "n"}:
            return False

        if choice in {"1", "refresh", "fresh", "new", "yes", "y"}:
            return True

        print("Invalid choice. Please enter 1 or 2.")


if not OPENAI_API_KEY:
    print("ERROR: OPENAI_API_KEY not found!")
    exit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Semantic (embedding) search index — built once after inventory loads.
semantic_index = SemanticIndex(openai_client)

# ============================================================================
# BRANSON KNOWLEDGE BASE
# ============================================================================

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

COMMON_BRANSON_PARTS = {
    "glow plug": ["HT17160000A3", "glow plug"],
    "air filter": ["EA00000985A", "air filter"],
    "fuel filter": ["fuel filter", "23000-022000"],
    "oil filter": ["oil filter", "A04_002"],
    "fuel injection valve": ["HK12020000A3", "fuel injection valve"],
    "hydraulic filter": ["hydraulic filter"],
    "starter": ["starter motor"],
    "alternator": ["alternator"],
    "water pump": ["water pump"],
    "thermostat": ["thermostat"],
    "belt": ["v-belt", "drive belt"],
    "hose": ["hydraulic hose", "radiator hose"],
}

FAQ_RESPONSES = {
    "what_sell": {
        "keywords": ["what do you sell", "what kind", "what type", "what products"],
        "response": "We sell tractor parts for all models of Branson Tractors. What can I help you find?",
    },
    "shipping": {
        "keywords": ["shipping", "delivery", "free shipping", "ship"],
        "response": "Please check our Terms and Conditions for detailed shipping information. Is there a specific part you're looking for?",
    },
    "warranty": {
        "keywords": ["warranty", "guarantee", "return"],
        "response": "For warranty and return information, please refer to our Terms and Conditions. Can I help you find a specific part?",
    },
    "payment": {
        "keywords": ["payment", "pay", "credit card", "accept"],
        "response": "We accept standard payment methods. For specific payment details, please see our checkout page. What part can I help you locate?",
    },
    "hours": {
        "keywords": ["hours", "open", "business hours", "when open"],
        "response": "For our business hours and contact information, please check our Contact page. What Branson part are you looking for?",
    },
}

# ============================================================================
# DUAL DATA SOURCE: Excel + Live Shopify + SMART INDEXING
# ============================================================================

# ============================================================================
# DUAL DATA SOURCE: Shopify (PRIMARY) + Excel (Emergency Fallback Only)
# ============================================================================

inventory_index = []
shopify_crawler = None
shopify_data_loaded = False
last_shopify_sync = None

# Smart indexes for efficient searching (built from inventory_index)
product_indexes = {
    "by_sku": {},  # SKU -> product
    "by_series": {},  # "20" -> [products for 20 series]
    "by_model": {},  # "2100" -> [products for 2100]
    "by_part_category": {},  # "filter" -> [all filter products]
}

# Data source priority flag
USE_SHOPIFY_ONLY = True  # Set to False to allow Excel fallback

# NEW: Efficient indexes for fast lookups
product_indexes = {
    "by_sku": {},  # SKU -> Product
    "by_series": {},  # "20_series" -> [Products]
    "by_model": {},  # "2100" -> [Products]
    "by_part_type": {},  # "filter" -> [Products]
    "by_part_category": {},  # "roof" -> [Products]
}


def build_product_indexes():
    """
    Build efficient indexes from inventory for instant lookups
    This is the KEY to making search fast and accurate!
    """
    logger.info("🗂️  Building product indexes for fast search...")

    global product_indexes

    # Reset indexes
    product_indexes = {
        "by_sku": {},
        "by_series": {},
        "by_model": {},
        "by_part_type": {},
        "by_part_category": {},
    }

    # Series mapping for Branson
    series_map = {
        "20": ["2100", "2205", "2400", "2515", "2610", "2800"],
        "30": ["3015", "3520", "3725"],
        "40": ["4015", "4520"],
        "50": ["5220", "5825", "5835"],
        "60": ["6225"],
        "70": ["7845"],
        "80": ["8050"],
    }

    # Part type keywords for categorization
    part_categories = {
        "filter": ["filter", "strainer"],
        "plug": ["plug", "glow plug", "spark plug"],
        "roof": ["roof", "canopy", "top", "cover"],
        "belt": ["belt", "v-belt"],
        "hose": ["hose", "tube", "line"],
        "pump": ["pump"],
        "valve": ["valve"],
        "seal": ["seal", "gasket", "o-ring"],
        "bearing": ["bearing", "bushing"],
        "electrical": ["starter", "alternator", "battery", "switch"],
        "hydraulic": ["hydraulic", "cylinder"],
    }

    for product in inventory_index:
        sku = product["sku"].upper()
        title_lower = product["title"].lower()
        tags_str = " ".join(product["tags"]).lower()

        # Index by SKU
        if sku:
            product_indexes["by_sku"][sku] = product

        # Index by Series (extract from tags)
        for series_num, models in series_map.items():
            series_key = f"{series_num}_series"

            # Check if any model from this series is in tags
            if any(model in tags_str for model in models):
                if series_key not in product_indexes["by_series"]:
                    product_indexes["by_series"][series_key] = []
                product_indexes["by_series"][series_key].append(product)

            # Also check for explicit "2000 series" or "20 series" mentions
            if (
                f"{series_num}00 series" in tags_str
                or f"{series_num} series" in tags_str
            ):
                if series_key not in product_indexes["by_series"]:
                    product_indexes["by_series"][series_key] = []
                if product not in product_indexes["by_series"][series_key]:
                    product_indexes["by_series"][series_key].append(product)

        # Index by specific model
        for model in BRANSON_MODELS:
            if model.lower() in tags_str or model.lower() in title_lower:
                if model not in product_indexes["by_model"]:
                    product_indexes["by_model"][model] = []
                product_indexes["by_model"][model].append(product)

        # Index by part category
        for category, keywords in part_categories.items():
            if any(keyword in title_lower for keyword in keywords):
                if category not in product_indexes["by_part_category"]:
                    product_indexes["by_part_category"][category] = []
                product_indexes["by_part_category"][category].append(product)

    # Log index sizes
    logger.info(f"✅ Indexes built:")
    logger.info(f"   SKUs: {len(product_indexes['by_sku'])}")
    logger.info(f"   Series: {len(product_indexes['by_series'])} series")
    for series, products in product_indexes["by_series"].items():
        logger.info(f"      {series}: {len(products)} products")
    logger.info(f"   Models: {len(product_indexes['by_model'])} models")
    logger.info(f"   Categories: {len(product_indexes['by_part_category'])} categories")
    for cat, products in product_indexes["by_part_category"].items():
        logger.info(f"      {cat}: {len(products)} products")


def load_excel_inventory():
    """
    Load inventory from Excel file (EMERGENCY FALLBACK ONLY!)

    NOTE: This is NOT the live store data!
    Excel contains sample/test data only.
    Real prices and inventory come from Shopify.
    """
    logger.warning("📊 Loading Excel inventory (FALLBACK - not live store data)...")
    excel_path = "Sample_Data_Full_Export_1.xlsx"

    if not os.path.exists(excel_path):
        logger.warning(f"⚠️  Excel file not found: {excel_path}")
        return []

    df = pd.read_excel(excel_path, sheet_name="Products")
    df["Variant SKU"] = df["Variant SKU"].fillna("")
    df["Variant Price"] = pd.to_numeric(df["Variant Price"], errors="coerce").fillna(0)
    df["Tags"] = df["Tags"].fillna("")
    df["Body HTML"] = df["Body HTML"].fillna("")

    inventory = []
    for idx, row in df.iterrows():
        inventory.append(
            {
                "title": str(row["Title"]),
                "sku": str(row["Variant SKU"]),
                "price": float(row["Variant Price"]),
                "tags": str(row["Tags"]).split(","),
                "description": str(row["Body HTML"]),
                "inventory": int(row["Total Inventory Qty"]),
                "weight": str(row["Variant Weight"]),
                "weight_unit": str(row["Variant Weight Unit"]),
                "type": str(row["Type"]),
                "source": "excel",
                "is_live_data": False,  # NOT live store data!
            }
        )

    logger.warning(
        f"⚠️  Loaded {len(inventory)} products from Excel (SAMPLE DATA ONLY - not live!)"
    )
    return inventory


def init_shopify_crawler():
    """Initialize Shopify crawler"""
    global shopify_crawler

    if not ACCESS_TOKEN:
        logger.warning(
            "⚠️  Shopify ACCESS_TOKEN not configured - using Excel data only"
        )
        return False

    try:
        logger.info("🔌 Initializing Shopify crawler...")
        shopify_crawler = ShopifyCrawler(
            shop_name=SHOP_NAME, access_token=ACCESS_TOKEN, api_version=API_VERSION
        )
        logger.info("✅ Shopify crawler initialized")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to initialize Shopify crawler: {e}")
        return False


def sync_shopify_data(force_refresh=False):
    """
    Sync data from Shopify - THIS IS THE LIVE STORE DATA!

    Excel is NOT used unless Shopify completely fails.
    All prices and inventory come from varnerparts.com Shopify store.

    Args:
        force_refresh: If True, ignore the cache and crawl fresh from Shopify.
    """
    global inventory_index, shopify_data_loaded, last_shopify_sync

    if not shopify_crawler:
        logger.error("❌ Shopify not configured - cannot get live store data!")
        return False

    try:
        logger.info("🔄 Syncing with Shopify (varnerparts.com - LIVE STORE DATA)...")

        # Cache file
        cache_file = "shopify_crawl_data.json"
        cache_loaded = False

        if force_refresh:
            logger.info("♻️  User selected fresh Shopify crawl - ignoring old cache...")
        elif os.path.exists(cache_file):
            cache_age = datetime.now() - datetime.fromtimestamp(
                os.path.getmtime(cache_file)
            )
            logger.info(
                f"📂 Using existing Shopify cache "
                f"({int(cache_age.total_seconds() // 3600)}h old, no time limit)"
            )
            shopify_crawler.load_from_file(cache_file)
            cache_loaded = True
        else:
            logger.info("📂 No Shopify cache found - crawling fresh data...")

        # Crawl fresh only if user selected refresh OR cache file does not exist
        if force_refresh or not cache_loaded:
            logger.info("🚀 Crawling fresh data from Shopify (varnerparts.com)...")
            shopify_crawler.crawl_all(delay=0.3, resume=False)
            shopify_crawler.save_to_file(cache_file)

        # Convert Shopify data to our format
        shopify_inventory = []
        for variant in shopify_crawler.variants:
            # Convert tags string to list
            tags_str = variant.get("product_tags", "")
            tags = [tag.strip() for tag in tags_str.split(",")] if tags_str else []

            shopify_inventory.append(
                {
                    "title": variant.get("product_title", ""),
                    "sku": variant.get("sku", ""),
                    "price": float(variant.get("price", 0)),
                    "tags": tags,
                    "description": "",  # Not in variant data
                    "inventory": int(variant.get("inventory_quantity", 0)),
                    "weight": variant.get("weight", ""),
                    "weight_unit": variant.get("weight_unit", "lb"),
                    "type": variant.get("product_type", ""),
                    "source": "shopify",
                    "is_live_data": True,  # ✓ LIVE from varnerparts.com!
                    "product_handle": variant.get("product_handle", ""),
                    "shopify_id": variant.get("variant_id", ""),
                    # Use public store URL (varnerparts.com) instead of .myshopify.com
                    "product_url": (
                        f"https://varnerparts.com/products/{variant.get('product_handle', '')}"
                        if variant.get("product_handle")
                        else None
                    ),
                    "variant_url": (
                        f"https://varnerparts.com/products/{variant.get('product_handle', '')}?variant={variant.get('variant_id', '')}"
                        if variant.get("product_handle") and variant.get("variant_id")
                        else None
                    ),
                }
            )

        logger.info(
            f"✅ Synced {len(shopify_inventory)} products from Shopify (LIVE STORE DATA)"
        )

        # USE ONLY SHOPIFY DATA (no Excel merge!)
        if USE_SHOPIFY_ONLY:
            logger.info("✓ Using SHOPIFY ONLY (live store data)")
            inventory_index = shopify_inventory
        else:
            # Fallback mode: merge with Excel (Shopify takes priority)
            logger.warning("⚠️  Using Shopify + Excel fallback mode")
            excel_only = [p for p in inventory_index if p.get("source") == "excel"]
            merged_inventory = merge_inventories(excel_only, shopify_inventory)
            inventory_index = merged_inventory

        # BUILD INDEXES after loading data
        build_product_indexes()
        semantic_index.build(inventory_index)  # embedding index (cached to disk)

        shopify_data_loaded = True
        last_shopify_sync = datetime.now()

        logger.info(
            f"✅ Total inventory: {len(inventory_index)} products (ALL FROM LIVE STORE)"
        )
        logger.info(
            f"   - Live Shopify data: {len([p for p in inventory_index if p.get('is_live_data')])} products"
        )
        logger.info(
            f"   - Sample/fallback data: {len([p for p in inventory_index if not p.get('is_live_data')])} products"
        )

        return True

    except Exception as e:
        logger.error(f"❌ Shopify sync failed: {e}")
        logger.error("   Cannot get live store data!")
        return False


def merge_inventories(excel_inventory, shopify_inventory):
    """
    Merge Excel and Shopify inventories
    Shopify data takes priority for SKU conflicts
    """
    logger.info("🔀 Merging inventories...")

    # Build SKU map from Shopify (higher priority)
    shopify_sku_map = {
        item["sku"].upper(): item for item in shopify_inventory if item["sku"]
    }

    # Start with all Shopify items
    merged = list(shopify_inventory)

    # Add Excel items that aren't in Shopify
    for excel_item in excel_inventory:
        sku_upper = excel_item["sku"].upper()
        if sku_upper not in shopify_sku_map:
            merged.append(excel_item)

    logger.info(
        f"✅ Merged: {len(merged)} total ({len(shopify_inventory)} Shopify, {len(merged) - len(shopify_inventory)} Excel-only)"
    )
    return merged


def build_product_indexes():
    """
    Build smart indexes from inventory for fast lookups
    Organizes 21k products by: SKU, Series, Model, Part Category
    """
    global product_indexes

    logger.info("🔨 Building product indexes from inventory...")

    # Reset indexes
    product_indexes = {
        "by_sku": {},
        "by_series": {},
        "by_model": {},
        "by_part_category": {},
    }

    # Part category keywords for classification
    part_categories = {
        "filter": [
            "filter",
            "air filter",
            "oil filter",
            "fuel filter",
            "hydraulic filter",
        ],
        "plug": ["plug", "glow plug", "spark plug"],
        "roof": ["roof", "canopy", "top"],
        "hose": ["hose", "line"],
        "belt": ["belt", "v-belt", "drive belt"],
        "valve": ["valve", "injection valve"],
        "seal": ["seal", "gasket", "o-ring"],
        "pump": ["pump", "water pump", "fuel pump", "hydraulic pump"],
        "starter": ["starter"],
        "alternator": ["alternator"],
        "battery": ["battery"],
        "tire": ["tire", "wheel", "rim"],
        "light": ["light", "lamp", "headlight"],
        "mirror": ["mirror"],
        "seat": ["seat"],
        "clutch": ["clutch"],
        "brake": ["brake"],
        # Hardware parts
        "bolt": ["bolt", "hex bolt", "cap screw"],
        "nut": ["nut", "hex nut", "lock nut"],
        "screw": ["screw"],
        "washer": ["washer", "flat washer", "lock washer"],
        "fastener": ["fastener", "rivet", "pin", "clip", "clamp"],
    }

    for idx, product in enumerate(inventory_index):
        title_lower = product["title"].lower()
        sku = product["sku"]
        tags_str = " ".join(product["tags"]).lower()

        # Index by SKU
        if sku:
            product_indexes["by_sku"][sku.upper()] = product

        # Index by Series (extract from tags or title)
        # "Branson 2100" -> series "20"
        # "Branson 3520R" -> series "30"
        for model in BRANSON_MODELS:
            if (
                f"branson {model}" in tags_str
                or f"branson{model}" in tags_str
                or model in tags_str
            ):
                # Extract series prefix (first 1-2 digits)
                series = model[0:2] if len(model) >= 2 else model[0]

                if series not in product_indexes["by_series"]:
                    product_indexes["by_series"][series] = []
                product_indexes["by_series"][series].append(product)

                # Also index by specific model
                if model not in product_indexes["by_model"]:
                    product_indexes["by_model"][model] = []
                product_indexes["by_model"][model].append(product)

        # Index by Part Category
        for category, keywords in part_categories.items():
            if any(keyword in title_lower for keyword in keywords):
                if category not in product_indexes["by_part_category"]:
                    product_indexes["by_part_category"][category] = []
                product_indexes["by_part_category"][category].append(product)

    # Log index stats
    logger.info(f"✅ Indexes built:")
    logger.info(f"   - SKUs: {len(product_indexes['by_sku'])} unique")
    logger.info(f"   - Series: {len(product_indexes['by_series'])} series")
    for series, products in sorted(product_indexes["by_series"].items()):
        logger.info(f"      • {series}0 Series: {len(products)} products")
    logger.info(f"   - Models: {len(product_indexes['by_model'])} models")
    logger.info(
        f"   - Part Categories: {len(product_indexes['by_part_category'])} categories"
    )
    for category, products in sorted(
        product_indexes["by_part_category"].items(), key=lambda x: -len(x[1])
    )[:10]:
        logger.info(f"      • {category}: {len(products)} products")

    return product_indexes


# ============================================================================
# WEB SEARCH WITH VARNERPARTS.COM PRIORITY
# ============================================================================


def search_web_serper(query, prioritize_varner=True):
    """Search Google with priority for varnerparts.com"""
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set")
        return []

    try:
        # Add site filter for varnerparts when appropriate
        search_query = query
        if prioritize_varner and "varnerparts" not in query.lower():
            search_query = f"{query} site:varnerparts.com OR {query}"

        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": search_query, "num": 8})
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

        response = requests.post(url, headers=headers, data=payload, timeout=10)

        if response.status_code == 200:
            data = response.json()
            results = []

            for item in data.get("organic", [])[:8]:
                is_varner = "varnerparts.com" in item.get("link", "").lower()
                result = {
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                    "domain": item.get("displayedLink", ""),
                    "is_varner": is_varner,
                    "priority": 10 if is_varner else 1,
                }
                results.append(result)

            # Sort to prioritize varnerparts
            results.sort(key=lambda x: x["priority"], reverse=True)

            logger.info(
                f"Found {len(results)} results, {sum(1 for r in results if r['is_varner'])} from varnerparts.com"
            )
            return results
        else:
            logger.error(f"Serper API error: {response.status_code}")
            return []

    except Exception as e:
        logger.error(f"Web search error: {str(e)}")
        return []


def search_web(query, prioritize_varner=True):
    """Smart web search with fallback"""
    if SERPER_API_KEY:
        return search_web_serper(query, prioritize_varner)
    else:
        logger.warning("No search API configured")
        return []


def extract_part_numbers_from_web(search_results):
    """Extract Branson part numbers from web results - Enhanced patterns"""
    part_numbers = []

    for result in search_results:
        text = result["title"] + " " + result["snippet"]

        # Enhanced part number patterns
        patterns = [
            r"\b[A-Z]{2}\d{8}[A-Z]\d\b",  # HT17160000A3 format
            r"\b[A-Z]{2}\d{2}\d{6}\b",  # EA00000985A format
            r"\b[A-Z]{2}\d{5}\b",  # AC01672, AC02240 format (5 digits)
            r"\b[A-Z]\d{3,4}\b",  # B207, A123 format (short codes)
            r"\b\d{5}-\d{6}\b",  # 23000-022000 format
            r"\b\d{5}-\d{5}\b",  # 27241-18000 format
            r"\b[A-Z]\d{2}_\d{3}\b",  # A04_002 format
            r"\bP/N[\s#:]*([A-Z0-9\-]+)\b",  # P/N prefix
            r"\bSKU[\s#:]*([A-Z0-9\-]+)\b",  # SKU prefix
            r"\(([A-Z]{2}\d{5})\)",  # (AC01672) in parentheses
            r"\(([A-Z]\d{3,4})\)",  # (B207) in parentheses
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                # Handle tuple results from groups
                for match in matches:
                    if isinstance(match, tuple):
                        part_numbers.extend([m for m in match if m])
                    else:
                        part_numbers.append(match)

    # Remove duplicates, preserve order
    seen = set()
    unique_parts = []
    for pn in part_numbers:
        pn_upper = pn.upper()
        if pn_upper not in seen and len(pn) >= 3:  # At least 3 chars
            seen.add(pn_upper)
            unique_parts.append(pn_upper)

    logger.info(
        f"Extracted {len(unique_parts)} part numbers from web: {unique_parts[:10]}"
    )
    return unique_parts


# ============================================================================
# INTENT DETECTION & CONVERSATION HANDLING
# ============================================================================


def detect_intent(user_query):
    """
    Classify user intent with better part detection

    Returns:
        tuple: (intent_type, intent_subtype)
            - ("search", "part_search") for part searches
            - ("faq", faq_type) for FAQs
            - ("chat", "greeting") for greetings
            - ("chat", "general") for general conversation
    """
    query_lower = user_query.lower().strip()

    # Check for very short greetings only
    greetings = ["hello", "hi", "hey"]
    if query_lower in greetings or (
        any(g in query_lower for g in greetings) and len(query_lower.split()) <= 2
    ):
        return "chat", "greeting"

    # PRIORITY 1: Check for part number patterns (strongest signal)
    # These patterns indicate the user has a specific part in mind
    part_number_patterns = [
        r"\b[A-Z]\d{3,4}\b",  # B207, A123, AC01672
        r"\b[A-Z]{2}\d{5,}\b",  # AC01672, HT17160000A3, EA00000985A
        r"\bV\d{10}\b",  # V2184610025
        r"\b\d{5}-\d{5,6}\b",  # 23000-022000, 27241-18000
        r"\bP/?N[\s:]*[A-Z0-9\-]+\b",  # P/N: AC01672, PN AC01672
        r"\bSKU[\s:]*[A-Z0-9\-]+\b",  # SKU: B207, SKU B207
        r"\([A-Z]\d+\)",  # (B207), (V2184610025)
        r"\b[A-Z]\d{2}_\d{3}\b",  # A04_002
    ]

    # Use the original query (not lowercased) for part number detection
    if any(
        re.search(pattern, user_query, re.IGNORECASE)
        for pattern in part_number_patterns
    ):
        logger.info(f"   🎯 Part number pattern detected in query")
        return "search", "part_search"

    # Check for FAQ keywords (must be dominant in query)
    for faq_type, faq_data in FAQ_RESPONSES.items():
        # FAQ must be the main focus, not just mentioned
        if any(keyword == query_lower for keyword in faq_data["keywords"]):
            return "faq", faq_type
        # Or if FAQ keyword is dominant and no part-specific terms
        if any(keyword in query_lower for keyword in faq_data["keywords"]):
            # But not if it also mentions parts
            if not any(
                word in query_lower
                for word in [
                    "part",
                    "filter",
                    "plug",
                    "valve",
                    "roof",
                    "hose",
                    "belt",
                    "bolt",
                ]
            ):
                return "faq", faq_type

    # PRIORITY 2: Check for part-related keywords
    part_indicators = [
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
        # Hardware parts
        "bolt",
        "nut",
        "screw",
        "washer",
        "rivet",
        "pin",
        "clip",
        "clamp",
        "fastener",
        # Action verbs
        "need",
        "looking for",
        "look for",
        "search for",
        "searching for",
        "find",
        "want",
        "get",
        "buy",
        "purchase",
        "order",
        "where can i",
        "how much",
        "price",
        "cost",
        "sell",
        "have",
        "stock",
    ]

    if any(indicator in query_lower for indicator in part_indicators):
        return "search", "part_search"

    # PRIORITY 3: Check for Branson model numbers (CASE-INSENSITIVE FIX!)
    # Original bug: "2505H" (uppercase) != "2505h" (lowercase)
    if any(model.lower() in query_lower for model in BRANSON_MODELS):
        logger.info(f"   🎯 Branson model detected in query")
        return "search", "part_search"

    # PRIORITY 4: General tractor/Branson mentions
    if "branson" in query_lower or "tractor" in query_lower:
        return "search", "part_search"

    # Default to search. This is a parts app, so anything that isn't a clear
    # greeting or FAQ should reach the search engine — it clarifies on weak/no
    # matches itself, instead of the router dumping real part queries into chat.
    return "search", "part_search"


def handle_faq(faq_type):
    """Handle FAQ responses"""
    if faq_type in FAQ_RESPONSES:
        return {
            "type": "faq",
            "response": FAQ_RESPONSES[faq_type]["response"],
            "suggestions": ["Browse parts", "Search by model", "Contact us"],
        }
    return None


def handle_chat(chat_type, query):
    """Handle conversational messages"""
    if chat_type == "greeting":
        return {
            "type": "chat",
            "response": "Hello! Welcome to Branson Tractor Parts. I can help you find parts for any Branson tractor model. What are you looking for today?",
            "suggestions": [
                "Find glow plugs",
                "Air filter for 2100",
                "Browse by model",
            ],
        }
    else:
        return {
            "type": "chat",
            "response": "I'm here to help you find Branson tractor parts. Could you tell me which model you have and what part you need?",
            "suggestions": ["Branson 2100 parts", "Common filters", "View all models"],
        }


# ============================================================================
# ENHANCED INTERPRETATION WITH BRANSON EXPERTISE
# ============================================================================


def interpret_query_branson(user_query, use_web_search=True):
    """Enhanced interpretation specialized for Branson tractors with aggressive web search"""

    system_prompt = """You are a Branson tractor parts specialist with deep knowledge of all Branson models.

Branson models include: 2100, 2205H, 2400, 2400H, 2515H, 2515R, 2505H, 2610R, 2800, 2800H, 
3015C, 3015R, 3520H, 3520R, 3725CH, 4015C, 4015R, 4520C, 4520R, 5220C, 5220CH, 5220R, 
5825C, 5835R, 6225C, 6225R, 7845C, 7845R, 8050C, 8050R

IMPORTANT - Part Number Detection:
- Look for alphanumeric codes in parentheses like (V2184610025), (HT17160000A3), (B207)
- Codes starting with V, B, A, H, etc. followed by numbers are often part numbers or references
- Extract ALL potential part numbers/SKUs from the query

IMPORTANT - Model Series Recognition:
- "20 Series" or "2000 Series" = models 2100, 2205H, 2400, 2400H, 2515H, 2515R, 2505H, 2610R, 2800, 2800H
- "30 Series" or "3000 Series" = models 3015C, 3015R, 3520H, 3520R, 3725CH
- "40 Series" or "4000 Series" = models 4015C, 4015R, 4520C, 4520R
- "50 Series" or "5000 Series" = models 5220C, 5220CH, 5220R, 5825C, 5835R
- "60 Series" or "6000 Series" = models 6225C, 6225R
- "70 Series" or "7000 Series" = models 7845C, 7845R
- "80 Series" or "8000 Series" = models 8050C, 8050R

Extract from user query:
1. Branson model number - if they mention a series (like "20 Series"), put the series name
2. Specific part type - be VERY specific (e.g., "bolt", "hex bolt", "glow plug", "canopy")
3. ANY part numbers, SKUs, or reference codes mentioned (including codes in parentheses)

Respond ONLY with JSON (no markdown):
{
    "equipment": "Branson [model or series]",
    "model": "model number OR series name like '20 Series'",
    "part_type": "specific part name",
    "specific_sku": "primary SKU/part number if mentioned, otherwise null",
    "all_part_numbers": ["list", "of", "all", "potential", "part", "numbers", "found"],
    "search_terms": ["key", "terms", "for", "search"],
    "confidence": "high/medium/low",
    "is_series": true if model is a series range, false if specific model
}

Examples:
User: "BOLT HEX/SP (V2184610025) for B207 2505H"
Response: {
    "equipment": "Branson 2505H",
    "model": "2505H",
    "part_type": "hex bolt",
    "specific_sku": "V2184610025",
    "all_part_numbers": ["V2184610025", "B207"],
    "search_terms": ["bolt", "hex bolt", "V2184610025", "B207", "2505H"],
    "confidence": "high",
    "is_series": false
}

User: "Tractor roof for 20 Series Branson"
Response: {
    "equipment": "Branson 20 Series",
    "model": "20 Series",
    "part_type": "tractor roof",
    "specific_sku": null,
    "all_part_numbers": [],
    "search_terms": ["tractor roof", "canopy", "20 Series", "2000 series"],
    "confidence": "medium",
    "is_series": true
}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        response_text = response.choices[0].message.content
        response_text = re.sub(r"^```json\s*|\s*```$", "", response_text.strip())

        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            interpretation = json.loads(json_match.group())
        else:
            interpretation = {
                "equipment": user_query,
                "part_type": "unknown",
                "search_terms": user_query.split(),
                "confidence": "low",
            }

        logger.info(f"AI interpretation: {json.dumps(interpretation, indent=2)}")

        # Web research - MORE AGGRESSIVE
        web_info = {"web_searched": False}

        # Trigger web search if:
        # 1. Confidence is not high, OR
        # 2. Part type is unusual/uncommon, OR
        # 3. User explicitly mentioned a series
        should_search_web = use_web_search and (
            interpretation.get("confidence") != "high"
            or interpretation.get("is_series") == True
            or len(interpretation.get("search_terms", [])) > 0
        )

        if should_search_web:
            equipment = interpretation.get("equipment", "")
            part_type = interpretation.get("part_type", "")
            model = interpretation.get("model", "")

            # Build comprehensive search query
            if part_type and part_type != "unknown":
                # Include model/series if available
                if model:
                    search_query = f"Branson {model} {part_type} varnerparts.com"
                else:
                    search_query = f"Branson tractor {part_type} varnerparts.com"

                logger.info(
                    f"🔍 Web search (confidence: {interpretation.get('confidence')}): {search_query}"
                )

                web_results = search_web(search_query, prioritize_varner=True)

                if web_results:
                    part_numbers = extract_part_numbers_from_web(web_results)

                    web_info = {
                        "web_searched": True,
                        "part_numbers_found": part_numbers,
                        "sources": [
                            {
                                "title": r["title"],
                                "snippet": r["snippet"][:200],
                                "url": r["link"],
                                "is_varner": r.get("is_varner", False),
                            }
                            for r in web_results[:5]
                        ],
                        "search_query": search_query,
                    }

                    if part_numbers:
                        interpretation["web_part_numbers"] = part_numbers
                        interpretation["web_sources"] = web_info["sources"]
                        interpretation["confidence"] = "high_web_verified"
                    else:
                        # Even without part numbers, web results are valuable
                        interpretation["web_sources"] = web_info["sources"]
                        interpretation["confidence"] = "medium_web_searched"

        interpretation["web_research"] = web_info
        return interpretation

    except Exception as e:
        logger.error(f"Error in Branson interpretation: {str(e)}")
        return {
            "equipment": user_query,
            "part_type": "unknown",
            "search_terms": user_query.split(),
            "confidence": "low",
            "web_research": {"web_searched": False, "error": str(e)},
        }


# ============================================================================
# ENHANCED SEARCH WITH BRANSON + SHOPIFY SPECIALIZATION
# ============================================================================


def search_inventory_branson(interpretation):
    """
    INTELLIGENT MULTI-STAGE SEARCH with confidence-based result presentation

    Stages:
    1. Gather candidates from indexes (fast)
    2. Score with strict part type validation
    3. Calculate overall confidence
    4. Return appropriate number of results based on confidence
    """

    part_type = interpretation.get("part_type", "")
    model = interpretation.get("model", "")
    is_series = interpretation.get("is_series", False)
    web_part_numbers = interpretation.get("web_part_numbers", [])
    search_terms = interpretation.get("search_terms", [])
    specific_sku = interpretation.get("specific_sku")
    all_part_numbers = interpretation.get("all_part_numbers", [])

    logger.info(
        f"🔍 Indexed search: Model={model}, Part='{part_type}', WebParts={web_part_numbers}, SKU={specific_sku}, Series={is_series}"
    )

    # ========================================================================
    # STAGE 1: GATHER CANDIDATES
    # ========================================================================
    candidates = set()

    # Priority 1: Web-verified part numbers (HIGHEST TRUST)
    web_verified_products = []
    if web_part_numbers:
        logger.info(f"   🌐 Web verified parts: {web_part_numbers}")
        for pn in web_part_numbers:
            pn_upper = pn.upper()

            # Exact SKU match
            if pn_upper in product_indexes["by_sku"]:
                product = product_indexes["by_sku"][pn_upper]
                web_verified_products.append(product)
                candidates.add(id(product))
                logger.info(f"      ✓ Exact SKU: {pn_upper} -> {product['title'][:60]}")

            # Title/SKU contains match
            for product in inventory_index:
                if (
                    pn_upper in product["title"].upper()
                    or pn_upper in product["sku"].upper()
                ):
                    web_verified_products.append(product)
                    candidates.add(id(product))

    # Priority 2: User-mentioned part numbers
    user_mentioned_products = []
    all_mentioned_parts = []
    if specific_sku:
        all_mentioned_parts.append(specific_sku)
    if all_part_numbers:
        all_mentioned_parts.extend(all_part_numbers)

    if all_mentioned_parts:
        logger.info(f"   🎯 User mentioned: {all_mentioned_parts}")
        for part_num in all_mentioned_parts:
            part_upper = part_num.upper()

            # Exact SKU
            if part_upper in product_indexes["by_sku"]:
                product = product_indexes["by_sku"][part_upper]
                user_mentioned_products.append(product)
                candidates.add(id(product))

            # Title/SKU contains
            for product in inventory_index:
                if (
                    part_upper in product["title"].upper()
                    or part_upper in product["sku"].upper()
                ):
                    user_mentioned_products.append(product)
                    candidates.add(id(product))

    # Priority 3: Model-based candidates
    if model:
        if is_series:
            # Series search
            series_key = (
                model.lower().replace(" ", "_").replace("series", "").strip()
                + "_series"
            )
            # Try variations: "20_series", "2000_series"
            for key_variant in [
                series_key,
                model.split()[0] + "_series",
                model.replace(" ", "_").lower(),
            ]:
                if key_variant in product_indexes["by_series"]:
                    series_products = product_indexes["by_series"][key_variant]
                    candidates.update(id(p) for p in series_products)
                    logger.info(
                        f"   📂 Series '{key_variant}': {len(series_products)} products"
                    )
                    break
        else:
            # Specific model search
            if model in product_indexes["by_model"]:
                model_products = product_indexes["by_model"][model]
                candidates.update(id(p) for p in model_products)
                logger.info(f"   📂 Model '{model}': {len(model_products)} products")
            else:
                # Fallback: manual tag search
                model_lower = model.lower()
                for product in inventory_index:
                    tags_str = " ".join(product["tags"]).lower()
                    if f"branson {model_lower}" in tags_str or model_lower in tags_str:
                        candidates.add(id(product))

    # Priority 4: Part category candidates
    if part_type and part_type != "unknown":
        part_lower = part_type.lower()
        for category, products in product_indexes["by_part_category"].items():
            if category in part_lower or part_lower in category:
                candidates.update(id(p) for p in products)
                logger.info(f"   📂 Category '{category}': {len(products)} products")

    # If no candidates, search all
    if not candidates:
        logger.info("   ℹ️  No index matches, using all products")
        candidates = set(id(p) for p in inventory_index)

    candidate_products = [p for p in inventory_index if id(p) in candidates]
    logger.info(f"   📊 Total candidates: {len(candidate_products)}")

    # ========================================================================
    # STAGE 2: INTELLIGENT SCORING with STRICT PART TYPE VALIDATION
    # ========================================================================

    matches = []

    for product in candidate_products:
        score = 0
        match_reasons = []
        confidence_flags = []

        title_lower = product["title"].lower()
        sku_lower = product["sku"].lower()
        tags_str = " ".join(product["tags"]).lower()

        # ----------------------------------------------------------------
        # PART TYPE VALIDATION (CRITICAL!)
        # ----------------------------------------------------------------
        part_type_match_score = 0
        part_type_valid = False

        if part_type and part_type != "unknown":
            part_type_match_score, part_type_valid = validate_part_type_match(
                requested_type=part_type,
                product_title=title_lower,
                product_type=product.get("type", "").lower(),
            )

            if not part_type_valid:
                # HARD REJECT: Part type doesn't match at all
                # Example: User wants "filter kit" but product is "O-ring" → REJECT
                continue

        # ----------------------------------------------------------------
        # SCORING: Web-verified parts (HIGHEST PRIORITY)
        # ----------------------------------------------------------------
        is_web_verified = id(product) in [id(p) for p in web_verified_products]
        if is_web_verified:
            score += 5000  # MASSIVE boost for web verification
            match_reasons.append("🌐 Web-verified")
            confidence_flags.append("web_verified")
            logger.info(f"      ⭐ WEB VERIFIED: {product['title'][:60]}")

        # ----------------------------------------------------------------
        # SCORING: User-mentioned SKUs
        # ----------------------------------------------------------------
        is_user_mentioned = id(product) in [id(p) for p in user_mentioned_products]
        if is_user_mentioned:
            score += 3000
            match_reasons.append("🎯 User-specified SKU")
            confidence_flags.append("user_mentioned")

        # ----------------------------------------------------------------
        # SCORING: Part type match (with validation score)
        # ----------------------------------------------------------------
        score += part_type_match_score
        if part_type_match_score >= 1000:
            match_reasons.append(f"✓ Part: {part_type}")
        elif part_type_match_score >= 500:
            match_reasons.append(f"~ Part: {part_type}")

        # ----------------------------------------------------------------
        # SCORING: Model match
        # ----------------------------------------------------------------
        model_score = 0
        if model:
            model_lower = model.lower()

            if is_series:
                # Series matching
                series_prefix = (
                    model_lower.replace(" series", "").replace("series", "").strip()
                )
                if (
                    f"{series_prefix}" in tags_str
                    or f"branson {series_prefix}" in tags_str
                ):
                    model_score = 800
                    match_reasons.append(f"✓ Series: {model}")
            else:
                # Specific model matching
                if f"branson {model_lower}" in tags_str or model_lower in tags_str:
                    model_score = 1000
                    match_reasons.append(f"✓ Model: {model}")
                elif f" {model_lower} " in title_lower:
                    model_score = 800
                    match_reasons.append(f"~ Model: {model}")

        score += model_score

        # ----------------------------------------------------------------
        # SCORING: Live data bonus
        # ----------------------------------------------------------------
        if product.get("source") == "shopify":
            score += 100
            match_reasons.append("Live")

        # ----------------------------------------------------------------
        # CONFIDENCE CALCULATION
        # ----------------------------------------------------------------
        confidence_level = calculate_match_confidence(
            score=score,
            has_web_verification=is_web_verified,
            has_user_sku=is_user_mentioned,
            part_type_score=part_type_match_score,
            model_score=model_score,
        )

        # Only keep quality matches
        MIN_SCORE = 500  # Lower threshold to get more candidates
        if score >= MIN_SCORE:
            matches.append(
                {
                    **product,
                    "match_score": score,
                    "match_reason": " | ".join(match_reasons[:4]),
                    "confidence_level": confidence_level,
                    "confidence_flags": confidence_flags,
                    "is_web_verified": is_web_verified,
                }
            )

    # Sort by score
    matches.sort(key=lambda x: x["match_score"], reverse=True)

    # ========================================================================
    # STAGE 3: CONFIDENCE-BASED RESULT COUNT
    # ========================================================================

    if not matches:
        logger.info("❌ No quality matches found")
        return []

    # Calculate overall search confidence
    overall_confidence = calculate_overall_search_confidence(
        matches=matches,
        has_web_results=len(web_verified_products) > 0,
        has_model=bool(model),
        has_part_type=bool(part_type and part_type != "unknown"),
        top_score=matches[0]["match_score"],
    )

    # Determine how many results to show
    if overall_confidence >= 90:
        result_count = 1  # Very confident - show single result
        logger.info(f"✅ HIGH confidence ({overall_confidence}%) - showing 1 result")
    elif overall_confidence >= 70:
        result_count = 3  # Medium confidence - show 3 options
        logger.info(
            f"⚠️  MEDIUM confidence ({overall_confidence}%) - showing 3 results"
        )
    else:
        result_count = 5  # Low confidence - show 5 options
        logger.info(f"⚠️  LOW confidence ({overall_confidence}%) - showing 5 results")

    final_results = matches[:result_count]

    logger.info(
        f"✅ Returning {len(final_results)} results from {len(matches)} matches"
    )
    if final_results:
        for i, result in enumerate(final_results[:3], 1):
            logger.info(
                f"   {i}. {result['title'][:60]} (score: {result['match_score']}, conf: {result['confidence_level']}%)"
            )

    # Add metadata
    for result in final_results:
        result["search_confidence"] = overall_confidence
        result["total_matches"] = len(matches)

    return final_results


def validate_part_type_match(requested_type, product_title, product_type):
    """
    Validate if a product actually matches the requested part type

    Returns:
        tuple: (score, is_valid)
            - score: 0-2000 points based on match quality
            - is_valid: Boolean - whether this product is acceptable at all
    """

    requested_lower = requested_type.lower()
    title_lower = product_title.lower()
    type_lower = product_type.lower()

    # Define part type categories and their synonyms
    part_categories = {
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
            "exclude": [
                "bracket",
                "housing",
                "o-ring",
                "seal",
                "gasket",
                "line",
                "hose",
            ],
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

    # Find matching category
    category_config = None
    for category_name, config in part_categories.items():
        if category_name in requested_lower or any(
            term in requested_lower for term in config.get("exact", [])
        ):
            category_config = config
            break

    # If no specific category, use generic matching
    if not category_config:
        # Generic part type matching
        words = requested_lower.split()
        if all(word in title_lower or word in type_lower for word in words):
            return (1000, True)  # All words match
        elif any(word in title_lower or word in type_lower for word in words):
            return (500, True)  # Partial match
        else:
            return (0, False)  # No match

    # Check exclusions first (HARD REJECT)
    for exclude_term in category_config.get("exclude", []):
        if exclude_term in title_lower:
            # HARD REJECT: This is explicitly the wrong type
            # Example: User wants "filter kit" but product is "O-ring" → REJECT
            return (0, False)

    # Check exact matches (BEST)
    for exact_term in category_config.get("exact", []):
        if exact_term in title_lower:
            return (2000, True)  # Perfect match

    # Check related terms (GOOD)
    for related_term in category_config.get("related", []):
        if related_term in title_lower:
            return (1200, True)  # Related match

    # Check if base category word appears (ACCEPTABLE)
    base_words = requested_lower.split()
    if any(word in title_lower for word in base_words):
        return (800, True)  # Base category match

    # No match found
    return (0, False)


def calculate_match_confidence(
    score, has_web_verification, has_user_sku, part_type_score, model_score
):
    """
    Calculate confidence percentage for a single match

    Returns:
        int: Confidence percentage (0-100)
    """

    confidence = 0

    # Web verification = 40% base confidence
    if has_web_verification:
        confidence += 40

    # User-mentioned SKU = 30% base confidence
    if has_user_sku:
        confidence += 30

    # Part type match contribution (max 30%)
    if part_type_score >= 2000:
        confidence += 30  # Perfect match
    elif part_type_score >= 1200:
        confidence += 20  # Good match
    elif part_type_score >= 800:
        confidence += 10  # Acceptable match

    # Model match contribution (max 20%)
    if model_score >= 1000:
        confidence += 20  # Exact model
    elif model_score >= 800:
        confidence += 10  # Series/close match

    # Score-based bonus (max 10%)
    if score >= 5000:
        confidence += 10
    elif score >= 3000:
        confidence += 5

    return min(confidence, 100)  # Cap at 100%


def calculate_overall_search_confidence(
    matches, has_web_results, has_model, has_part_type, top_score
):
    """
    Calculate overall confidence in the search results

    Returns:
        int: Overall confidence percentage (0-100)
    """

    if not matches:
        return 0

    # Start with top match confidence
    top_confidence = matches[0].get("confidence_level", 0)

    # Adjust based on search quality
    confidence = top_confidence

    # Penalty if top match has low confidence
    if top_confidence < 60:
        confidence = max(confidence - 10, 30)

    # Bonus for web verification
    if has_web_results:
        confidence = min(confidence + 10, 100)

    # Bonus if we have both model and part type
    if has_model and has_part_type:
        confidence = min(confidence + 5, 100)

    # Penalty if score gap between #1 and #2 is small (ambiguous)
    if len(matches) >= 2:
        score_gap = matches[0]["match_score"] - matches[1]["match_score"]
        if score_gap < 500:  # Very close scores = ambiguous
            confidence = max(confidence - 15, 40)

    # Penalty if top score is low
    if top_score < 2000:
        confidence = max(confidence - 10, 30)

    return max(min(confidence, 100), 0)


# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Unified chat/search endpoint with intent detection
    """
    data = request.get_json()
    user_query = data.get("query", "").strip()
    use_web = data.get("use_web_search", True)

    if not user_query:
        return jsonify({"error": "No query provided"}), 400

    logger.info(f"\n{'='*80}\nUSER QUERY: {user_query}\n{'='*80}")

    # Detect intent
    intent_type, intent_subtype = detect_intent(user_query)
    logger.info(f"Intent: {intent_type} / {intent_subtype}")

    # Handle FAQ
    if intent_type == "faq":
        response = handle_faq(intent_subtype)
        return jsonify(
            {
                "query": user_query,
                "intent": intent_type,
                "response": response["response"],
                "suggestions": response["suggestions"],
                "is_conversational": True,
            }
        )

    # Handle chat/greeting
    if intent_type == "chat":
        response = handle_chat(intent_subtype, user_query)
        return jsonify(
            {
                "query": user_query,
                "intent": intent_type,
                "response": response["response"],
                "suggestions": response["suggestions"],
                "is_conversational": True,
            }
        )

    # Handle search — RAG pipeline (exact SKU -> semantic retrieval -> grounded chat).
    # answer_query() returns the same JSON shape the frontend already reads, and now
    # includes a conversational `response` string so the chat actually talks.
    # NOTE: interpret_query_branson / search_inventory_branson below are now unused
    # (kept for reference; safe to delete once you're happy with the new pipeline).
    return jsonify(answer_query(semantic_index, openai_client, user_query))


@app.route("/api/search", methods=["POST"])
def search():
    """
    Backward compatibility endpoint - redirects to chat endpoint
    """
    return chat()


@app.route("/api/sync", methods=["POST"])
def api_sync_shopify():
    """Manually trigger Shopify data sync. POST {"force": true} to bypass cache."""
    if not shopify_crawler:
        return (
            jsonify(
                {
                    "error": "Shopify not configured",
                    "message": "Set SHOP_NAME and ACCESS_TOKEN in .env",
                }
            ),
            400,
        )

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))

    try:
        success = sync_shopify_data(force_refresh=force)

        return jsonify(
            {
                "success": success,
                "forced": force,
                "products_count": len(inventory_index),
                "shopify_enabled": shopify_data_loaded,
                "last_sync": (
                    last_shopify_sync.isoformat() if last_shopify_sync else None
                ),
                "data_sources": {
                    "excel": len(
                        [p for p in inventory_index if p.get("source") == "excel"]
                    ),
                    "shopify": len(
                        [p for p in inventory_index if p.get("source") == "shopify"]
                    ),
                },
                "message": "Sync completed successfully" if success else "Sync failed",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cache-status", methods=["GET"])
def cache_status():
    """Report cache age, freshness, and last sync time (dev convenience)."""
    cache_file = "shopify_crawl_data.json"
    exists = os.path.exists(cache_file)

    age_seconds = None
    is_fresh = None
    if exists:
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(cache_file))
        age_seconds = int(age.total_seconds())
        is_fresh = age < timedelta(hours=CACHE_MAX_AGE_HOURS)

    return jsonify(
        {
            "cache_file": cache_file,
            "exists": exists,
            "age_seconds": age_seconds,
            "age_human": (
                f"{age_seconds // 3600}h {(age_seconds % 3600) // 60}m"
                if age_seconds is not None
                else None
            ),
            "max_age_hours": CACHE_MAX_AGE_HOURS,
            "is_fresh": is_fresh,
            "would_crawl_on_restart": (not is_fresh) if exists else True,
            "force_refresh_on_start": FORCE_REFRESH_ON_START,
            "last_sync": (last_shopify_sync.isoformat() if last_shopify_sync else None),
            "products_loaded": len(inventory_index),
        }
    )


@app.route("/api/models", methods=["GET"])
def get_models():
    """Get list of Branson models"""
    return jsonify({"models": BRANSON_MODELS, "total": len(BRANSON_MODELS)})


@app.route("/api/health", methods=["GET"])
def health():
    """Health check with live data status"""
    live_products = [p for p in inventory_index if p.get("is_live_data")]
    sample_products = [p for p in inventory_index if not p.get("is_live_data")]

    return jsonify(
        {
            "status": "ok",
            "specialization": "Branson Tractors (varnerparts.com)",
            "products_loaded": len(inventory_index),
            "models_supported": len(BRANSON_MODELS),
            # Data source status
            "live_data": {
                "enabled": shopify_data_loaded,
                "configured": shopify_crawler is not None,
                "last_sync": (
                    last_shopify_sync.isoformat() if last_shopify_sync else None
                ),
                "product_count": len(live_products),
                "is_primary": USE_SHOPIFY_ONLY,
            },
            "data_sources": {
                "shopify_live": len(live_products),  # Real store prices & inventory
                "excel_sample": len(sample_products),  # NOT live data
            },
            # Data quality indicator
            "using_live_prices": len(live_products) > 0,
            "all_data_is_live": len(sample_products) == 0,
            "version": "branson_shopify_primary_v2",
            "search_api": (
                "serper" if SERPER_API_KEY else "bing" if BING_API_KEY else "none"
            ),
            "features": [
                "live_shopify_data" if len(live_products) > 0 else "sample_data_only",
                "conversational_ai",
                "web_verification",
                "smart_indexing",
                "varnerparts_priority",
                "model_aware_search",
                "series_aware_search",
            ],
        }
    )


# ============================================================================
# INITIALIZATION
# ============================================================================

print("\n" + "=" * 80)
print("🚜 BRANSON TRACTOR PARTS - COMPLETE AI SYSTEM")
print("=" * 80)
print("Initializing...")

# PRIMARY: Try to get LIVE data from Shopify first
shopify_success = False
if init_shopify_crawler():
    try:
        print("\n🎯 Priority: Loading Shopify data for varnerparts.com...")

        force_refresh_choice = FORCE_REFRESH_ON_START or ask_shopify_cache_choice()

        shopify_success = sync_shopify_data(force_refresh=force_refresh_choice)
        if shopify_success:
            print("✅ SUCCESS: Using live Shopify data (real prices & inventory)")
    except Exception as e:
        logger.error(f"❌ Shopify sync failed: {e}")
        shopify_success = False

# FALLBACK: Only use Excel if Shopify completely failed
if not shopify_success and not USE_SHOPIFY_ONLY:
    print("\n⚠️  FALLBACK: Shopify unavailable, loading Excel sample data...")
    inventory_index = load_excel_inventory()
    if inventory_index:
        build_product_indexes()
        semantic_index.build(inventory_index)  # embedding index (cached to disk)
        print("⚠️  WARNING: Using sample data from Excel (NOT live store data!)")
elif not shopify_success:
    print("\n❌ ERROR: Cannot connect to Shopify and Excel fallback is disabled!")
    print("   Set USE_SHOPIFY_ONLY = False to enable Excel fallback")
    print("   Or check your Shopify credentials in .env file")

print("\n" + "=" * 80)
print("✅ SYSTEM READY")
print("=" * 80)
print(f"Inventory: {len(inventory_index)} products")
print(f"Models: {len(BRANSON_MODELS)} Branson models supported")

# Data source status
live_count = len([p for p in inventory_index if p.get("is_live_data")])
sample_count = len(inventory_index) - live_count

print(f"\n📊 Data Sources:")
if live_count > 0:
    print(
        f"  ✅ LIVE from varnerparts.com: {live_count} products (real prices & inventory)"
    )
else:
    print(f"  ❌ No live Shopify data")
if sample_count > 0:
    print(f"  ⚠️  Sample from Excel: {sample_count} products (NOT live!)")

# Show index statistics
print(f"\n🗂️  Search Indexes:")
print(f"  - SKU index: {len(product_indexes['by_sku'])} unique SKUs")
print(f"  - Series index: {len(product_indexes['by_series'])} series")
for series_key, products in sorted(product_indexes["by_series"].items()):
    series_display = series_key.replace("_", " ").title()
    print(f"      • {series_display}: {len(products)} products")
print(f"  - Model index: {len(product_indexes['by_model'])} specific models")
print(f"  - Category index: {len(product_indexes['by_part_category'])} part types")

if shopify_data_loaded:
    print(
        f"\n🔌 Shopify: ✓ Active (Last sync: {last_shopify_sync.strftime('%Y-%m-%d %H:%M:%S')})"
    )
    print(f"Data Sources:")
    print(
        f"  - Shopify (live): {len([p for p in inventory_index if p.get('source') == 'shopify'])} products"
    )
    print(
        f"  - Excel (backup): {len([p for p in inventory_index if p.get('source') == 'excel'])} products"
    )
else:
    print(f"\n🔌 Shopify: ✗ Not configured (using Excel only)")

print("\nFeatures:")
print("  ✓ Conversational AI (handles questions & searches)")
print("  ✓ Web verification via varnerparts.com")
if shopify_data_loaded:
    print("  ✓ Live Shopify data integration")
print("  ✓ Smart indexing (instant series/model/part lookups)")
print("  ✓ Model-aware part matching")
print("  ✓ FAQ handling")
print("  ✓ 30+ Branson models")

print("\nEndpoints:")
print("  POST /api/chat - Main endpoint (chat + search)")
print("  POST /api/search - Backward compatible search")
print("  POST /api/sync - Manual Shopify sync")
print("  GET  /api/models - List Branson models")
print("  GET  /api/health - System status")
print("=" * 80 + "\n")


if __name__ == "__main__":
    app.run(debug=True, port=5000, host="127.0.0.1", use_reloader=False)
