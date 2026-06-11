#!/usr/bin/env python3
"""
app.py — slim Branson parts backend.

Stateless and tiny: no Shopify crawl, no .pkl, no catalogue in memory. Every
request embeds the query and reads from Supabase, so it fits a free 512 MB host
(Render free / Cloud Run / your e2-medium).

Endpoints:
  POST /api/chat    main endpoint (chat + search)
  POST /api/search  alias of /api/chat (back-compat with the old frontend)
  GET  /api/health  health + keep-alive ping target
"""

import os
import logging

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

from search_engine import answer_query

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=os.getenv("ALLOWED_ORIGIN", "*"))

for key in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY"):
    if not os.getenv(key):
        raise SystemExit(f"❌ Missing required env var: {key}")

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# Anon (read-only) key is enough for the web app; the service key stays in ingest.
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    use_web = bool(data.get("use_web_search", True))
    if not query:
        return jsonify({"error": "No query provided"}), 400

    logger.info("QUERY: %s (web=%s)", query, use_web)
    try:
        return jsonify(
            answer_query(supabase, openai_client, query, use_web_search=use_web)
        )
    except Exception as e:
        logger.exception("query failed")
        return (
            jsonify(
                {
                    "query": query,
                    "results": [],
                    "search_confidence": 0,
                    "web_search_used": False,
                    "message": "Search is temporarily unavailable. Please try again.",
                    "error": str(e),
                }
            ),
            500,
        )


@app.route("/api/search", methods=["POST"])
def search():
    return chat()


@app.route("/api/models", methods=["GET"])
def models():
    from search_engine import BRANSON_MODELS

    return jsonify({"models": BRANSON_MODELS, "total": len(BRANSON_MODELS)})


@app.route("/api/health", methods=["GET"])
def health():
    # Cheap query doubles as the keep-alive that stops Supabase pausing.
    try:
        supabase.table("products").select("id").limit(1).execute()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({"status": "ok", "db": db_ok})


if __name__ == "__main__":
    # Local dev only. In production gunicorn runs the app (see render.yaml / Dockerfile).
    app.run(debug=True, port=5000, host="127.0.0.1")
