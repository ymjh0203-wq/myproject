# ============================================================
# db.py
# Supabase(PostgreSQL) 연결과 products_raw 저장(upsert)을 담당합니다.
# ============================================================

import os

import psycopg
from psycopg.types.json import Json  # jsonb 컬럼에 dict/list 를 넣을 때 사용
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def get_conn() -> psycopg.Connection:
    """.env 의 DATABASE_URL 로 Supabase 에 연결합니다."""
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            ".env 에 DATABASE_URL 이 없습니다. "
            "Supabase → Settings → Database → Connection string(URI) 값을 넣어주세요."
        )
    return psycopg.connect(url)


def upsert_product_raw(conn: psycopg.Connection, data: dict) -> int:
    """
    수집한 상품 원본을 products_raw 에 저장합니다.
    같은 source_url 이 이미 있으면 내용을 갱신(upsert)합니다.
    반환값: 저장된 행의 id
    """
    sql = """
        INSERT INTO products_raw
            (source_platform, source_url, source_item_id, title_original,
             price_original, currency, options, image_urls,
             sales_count, review_count, shop_name, raw_payload)
        VALUES
            (%(source_platform)s, %(source_url)s, %(source_item_id)s, %(title_original)s,
             %(price_original)s, %(currency)s, %(options)s, %(image_urls)s,
             %(sales_count)s, %(review_count)s, %(shop_name)s, %(raw_payload)s)
        ON CONFLICT (source_url) DO UPDATE SET
            source_item_id = EXCLUDED.source_item_id,
            title_original = EXCLUDED.title_original,
            price_original = EXCLUDED.price_original,
            currency       = EXCLUDED.currency,
            options        = EXCLUDED.options,
            image_urls     = EXCLUDED.image_urls,
            sales_count    = EXCLUDED.sales_count,
            review_count   = EXCLUDED.review_count,
            shop_name      = EXCLUDED.shop_name,
            raw_payload    = EXCLUDED.raw_payload,
            collected_at   = now()
        RETURNING id;
    """
    params = {
        "source_platform": data.get("source_platform", "taobao"),
        "source_url": data["source_url"],
        "source_item_id": data.get("source_item_id"),
        "title_original": data.get("title_original"),
        "price_original": data.get("price_original"),
        "currency": data.get("currency", "CNY"),
        # jsonb 컬럼: dict/list 는 Json() 으로 감싸서 넣습니다.
        "options": Json(data["options"]) if data.get("options") is not None else None,
        "image_urls": Json(data["image_urls"]) if data.get("image_urls") is not None else None,
        "sales_count": data.get("sales_count"),
        "review_count": data.get("review_count"),
        "shop_name": data.get("shop_name"),
        "raw_payload": Json(data["raw_payload"]) if data.get("raw_payload") is not None else None,
    }
    with conn.cursor() as cur:
        cur.execute(sql, params)
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id
