"""
Deople Cloudflare D1 database helper.

Compatible with the current websites table:
id, domain, title, description, keywords, http_status, crawl_status,
image_url, image_status, first_seen, last_checked, updated_at,
url, final_url, content_type

Required environment variables:
CF_ACCOUNT_ID, D1_DATABASE_ID, CF_API_TOKEN
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("deople.database")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "").strip()
D1_DATABASE_ID = os.getenv("D1_DATABASE_ID", "").strip()
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "").strip()

D1_QUERY_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/d1/database/{D1_DATABASE_ID}/query"
)
HTTP_TIMEOUT = float(os.getenv("D1_HTTP_TIMEOUT", "30"))


def _check_config() -> None:
    missing = [
        name for name, value in (
            ("CF_ACCOUNT_ID", CF_ACCOUNT_ID),
            ("D1_DATABASE_ID", D1_DATABASE_ID),
            ("CF_API_TOKEN", CF_API_TOKEN),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def _query(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute one SQL statement through the Cloudflare D1 REST API."""
    _check_config()
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    safe_params = [
        str(value) if value is not None else None
        for value in (params or [])
    ]

    try:
        response = httpx.post(
            D1_QUERY_URL,
            headers=headers,
            json={"sql": sql, "params": safe_params},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"Cannot connect to Cloudflare D1 API: {exc}") from exc

    try:
        data = response.json()
    except Exception:
        data = {"raw_response": response.text}

    if response.is_error:
        raise RuntimeError(
            f"Cloudflare D1 HTTP {response.status_code}: {data}"
        )
    if not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"Cloudflare D1 API error: {data}")

    results = data.get("result") or []
    if not results:
        return {}
    first = results[0]
    if isinstance(first, dict) and first.get("success") is False:
        raise RuntimeError(f"Cloudflare D1 SQL error: {first}")
    return first if isinstance(first, dict) else {}


def _rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = result.get("results", []) if isinstance(result, dict) else []
    return rows if isinstance(rows, list) else []


def insert_websites(websites: List[Dict[str, Any]]) -> int:
    """
    Insert validated websites. Duplicate domains are ignored.
    Required input: domain and url.
    Optional input: final_url, http_status, content_type.
    """
    inserted_attempts = 0
    now = int(time.time())

    for item in websites or []:
        domain = str(item.get("domain") or "").strip().lower()
        url = str(item.get("url") or "").strip()
        if not domain or not url:
            logger.warning("Skipping record without domain/url: %s", item)
            continue

        _query(
            """
            INSERT OR IGNORE INTO websites (
                domain, url, final_url, http_status, content_type,
                crawl_status, image_status, first_seen, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', 'pending', ?, ?)
            """,
            [
                domain,
                url,
                str(item.get("final_url") or url),
                item.get("http_status"),
                str(item.get("content_type") or ""),
                now,
                now,
            ],
        )
        inserted_attempts += 1

    logger.info("D1 insert processed %s website records", inserted_attempts)
    return inserted_attempts


def get_pending_crawl_websites(limit: int = 100) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    result = _query(
        """
        SELECT domain, url, final_url
        FROM websites
        WHERE crawl_status = 'pending'
        ORDER BY first_seen ASC
        LIMIT ?
        """,
        [limit],
    )
    return _rows(result)


def save_crawl_result(item: Dict[str, Any]) -> None:
    """Update crawl metadata using only columns present in the current table."""
    now = int(time.time())
    _query(
        """
        UPDATE websites
        SET title = ?,
            description = ?,
            keywords = ?,
            http_status = ?,
            final_url = ?,
            content_type = ?,
            crawl_status = ?,
            last_checked = ?,
            updated_at = ?
        WHERE domain = ?
        """,
        [
            str(item.get("title") or "")[:500],
            str(item.get("description") or "")[:2000],
            str(item.get("keywords") or "")[:1000],
            item.get("http_status"),
            str(item.get("final_url") or ""),
            str(item.get("content_type") or ""),
            str(item.get("crawl_status") or "failed"),
            now,
            now,
            str(item["domain"]),
        ],
    )


def claim_pending_screenshots(limit: int = 20) -> List[Dict[str, Any]]:
    """Fetch and mark pending screenshot rows as processing."""
    limit = max(1, min(int(limit), 100))
    result = _query(
        """
        SELECT domain, url, final_url
        FROM websites
        WHERE image_status = 'pending'
        ORDER BY first_seen ASC
        LIMIT ?
        """,
        [limit],
    )

    claimed = []
    for row in _rows(result):
        domain = row.get("domain")
        if not domain:
            continue
        _query(
            """
            UPDATE websites
            SET image_status = 'processing', updated_at = ?
            WHERE domain = ? AND image_status = 'pending'
            """,
            [int(time.time()), domain],
        )
        claimed.append(row)

    logger.info("Claimed %s pending screenshot jobs", len(claimed))
    return claimed


def update_image(domain: str, image_url: str, image_key: str = "") -> None:
    """Save the screenshot URL. image_key is accepted for compatibility."""
    _query(
        """
        UPDATE websites
        SET image_url = ?, image_status = 'ready', updated_at = ?
        WHERE domain = ?
        """,
        [image_url, int(time.time()), domain],
    )
    logger.info("Screenshot saved in D1: %s", domain)


def mark_image_failed(domain: str, error: str = "") -> None:
    """Mark screenshot as failed; current schema has no image error column."""
    _query(
        """
        UPDATE websites
        SET image_status = 'failed', updated_at = ?
        WHERE domain = ?
        """,
        [int(time.time()), domain],
    )
    logger.warning("Screenshot failed: %s - %s", domain, str(error)[:300])


def test_connection() -> None:
    result = _query("SELECT COUNT(*) AS total FROM websites")
    rows = _rows(result)
    total = rows[0].get("total", 0) if rows else 0
    logger.info("Cloudflare D1 connection OK. websites rows: %s", total)


if __name__ == "__main__":
    test_connection()
