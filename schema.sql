-- ============================================================================
-- Varner Parts — Supabase schema (Postgres + pgvector)
-- Run this ONCE in the Supabase SQL editor (Dashboard -> SQL -> New query).
-- ============================================================================

-- Extensions ----------------------------------------------------------------
create extension if not exists vector;     -- pgvector: semantic search
create extension if not exists pg_trgm;     -- trigram: fuzzy title search

-- Products table ------------------------------------------------------------
-- One row per Shopify *variant*. embedding is text-embedding-3-small @ 512 dims
-- (512 instead of the default 1536 keeps us comfortably under the 500 MB free cap).
create table if not exists products (
    id              bigint primary key,          -- Shopify variant_id
    sku             text,
    title           text not null,
    price           numeric default 0,
    inventory       integer default 0,
    tags            text[] default '{}',
    weight          text,
    weight_unit     text default 'lb',
    type            text,
    description     text default '',             -- plain text (HTML stripped at ingest)
    product_handle  text,
    product_url     text,
    search_blob     text default '',             -- title + tags + type, for model/series/category ilike
    sku_norm        text default '',             -- sku with non-alphanumeric stripped, lowercase
    embedding       vector(512),
    updated_at      timestamptz default now()
);

-- Indexes -------------------------------------------------------------------
-- Fast exact SKU lookups (we always upper() the SKU before comparing).
create index if not exists products_sku_upper_idx on products (upper(sku));

-- Normalized SKU index for dash/space-insensitive lookups.
create index if not exists products_sku_norm_idx on products (sku_norm);

-- Fuzzy title search fallback.
create index if not exists products_title_trgm_idx on products using gin (title gin_trgm_ops);

-- Trigram index on the combined blob (title + tags + type) for model / series /
-- category ilike candidate gathering in search_engine.py.
create index if not exists products_blob_trgm_idx on products using gin (search_blob gin_trgm_ops);

-- Approximate-nearest-neighbour index for the vector column (cosine distance).
create index if not exists products_embedding_idx
    on products using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Semantic match RPC --------------------------------------------------------
-- Called from the backend with the query embedding; returns the closest rows.
create or replace function match_products(query_embedding vector(512), match_count int)
returns table (
    id             bigint,
    sku            text,
    title          text,
    price          numeric,
    inventory      integer,
    tags           text[],
    weight         text,
    weight_unit    text,
    type           text,
    description    text,
    product_url    text,
    product_handle text,
    search_blob    text,
    similarity     float
)
language sql stable
as $$
    select
        p.id, p.sku, p.title, p.price, p.inventory, p.tags,
        p.weight, p.weight_unit, p.type, p.description, p.product_url,
        p.product_handle, p.search_blob,
        1 - (p.embedding <=> query_embedding) as similarity
    from products p
    where p.embedding is not null
    order by p.embedding <=> query_embedding
    limit match_count;
$$;