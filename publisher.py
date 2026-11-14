import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import _query, _rows

BATCH_SIZE = int(os.getenv("PUBLISH_BATCH_SIZE", "500"))
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://img.deople.ai").rstrip("/")
OUTPUT_FILE = Path(os.getenv("PUBLISH_OUTPUT", "website/data/websites.json"))


def normalize_keywords(value):
    """Convert D1 keyword values into a JSON array."""
    if not value:
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (json.JSONDecodeError, TypeError):
        pass

    # Support comma- or semicolon-separated legacy values.
    separator = ";" if ";" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


def normalize_website(row):
    domain = (row.get("domain") or "").strip().lower()
    image = (row.get("image_url") or "").strip()

    # New screenshot format: one {domain}.jpg image per website.
    if not image and domain:
        image = f"{IMAGE_BASE_URL}/{domain}.jpg"

    url = (row.get("url") or row.get("final_url") or "").strip()
    if not url and domain:
        url = f"https://{domain}"

    return {
        "domain": domain,
        "url": url,
        "title": (row.get("title") or domain).strip(),
        "description": (row.get("description") or "").strip(),
        "keywords": normalize_keywords(row.get("keywords")),
        "image": image,
    }


def get_publish_batch(offset, limit):
    sql = """
        SELECT
            domain,
            url,
            final_url,
            title,
            description,
            keywords,
            image_url,
            first_seen
        FROM websites
        WHERE quality_status = 'qualified'
          AND crawl_status = 'success'
          AND image_status = 'ready'
          AND image_url IS NOT NULL
          AND image_url != ''
        ORDER BY first_seen DESC, domain ASC
        LIMIT ? OFFSET ?
    """
    result = _query(sql, [limit, offset])
    return _rows(result)


def get_all_websites():
    websites = []
    seen_domains = set()
    offset = 0

    while True:
        rows = get_publish_batch(offset, BATCH_SIZE)
        if not rows:
            break

        for row in rows:
            item = normalize_website(row)
            domain = item["domain"]
            if domain and domain not in seen_domains:
                websites.append(item)
                seen_domains.add(domain)

        if len(rows) < BATCH_SIZE:
            break

        offset += BATCH_SIZE
        print(f"已读取 {offset} 条数据库记录")

    return websites


def save_json(websites):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(websites),
        "websites": websites,
    }

    temp_file = OUTPUT_FILE.with_suffix(OUTPUT_FILE.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")

    os.replace(temp_file, OUTPUT_FILE)
    print(f"JSON 已生成：{OUTPUT_FILE}")
    print(f"网站数量：{len(websites)}")


def verify_json():
    with OUTPUT_FILE.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    websites = payload.get("websites")
    if not isinstance(websites, list):
        raise ValueError("JSON 格式错误：websites 必须是数组")

    if payload.get("total") != len(websites):
        raise ValueError(
            f"JSON 数量不一致：total={payload.get('total')}，"
            f"实际 websites={len(websites)}"
        )

    required_fields = {"domain", "url", "title", "description", "keywords", "image"}
    for index, item in enumerate(websites):
        missing = required_fields - set(item)
        if missing:
            raise ValueError(f"第 {index + 1} 条记录缺少字段：{sorted(missing)}")

    print(f"JSON 校验通过：{len(websites)} 条网站记录")


def main():
    print("开始从 Cloudflare D1 发布网站数据")

    websites = get_all_websites()

    # Avoid accidentally replacing the live site's JSON with an empty file.
    if not websites:
        raise RuntimeError(
            "没有找到符合发布条件的网站；为避免覆盖线上 JSON，本次停止发布。"
        )

    save_json(websites)
    verify_json()
    print("Publisher 完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Publisher 失败：{exc}", file=sys.stderr)
        raise
