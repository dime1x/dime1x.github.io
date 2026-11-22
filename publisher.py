import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import _query, _rows

BATCH_SIZE = int(os.getenv("PUBLISH_BATCH_SIZE", "500"))
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://img.deople.ai").rstrip("/")
OUTPUT_FILE = Path(os.getenv("PUBLISH_OUTPUT", "data/websites.json"))


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

    separator = ";" if ";" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


def normalize_score(value):
    """Return a safe integer richness score between 0 and 100."""
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError):
        score = 0
    return max(0, min(100, score))


def normalize_website(row):
    domain = (row.get("domain") or "").strip().lower()
    image = (row.get("image_url") or "").strip()

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
        "richness_score": normalize_score(row.get("richness_score")),
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
            richness_score,
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


def build_payload(websites):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(websites),
        "websites": websites,
    }


def validate_payload(payload):
    websites = payload.get("websites")
    if not isinstance(websites, list):
        raise ValueError("JSON 格式错误：websites 必须是数组")

    if payload.get("total") != len(websites):
        raise ValueError(
            f"JSON 数量不一致：total={payload.get('total')}，"
            f"实际 websites={len(websites)}"
        )

    required_fields = {
        "domain", "url", "title", "description", "keywords",
        "image", "richness_score"
    }
    for index, item in enumerate(websites):
        missing = required_fields - set(item)
        if missing:
            raise ValueError(f"第 {index + 1} 条记录缺少字段：{sorted(missing)}")

        score = item["richness_score"]
        if not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError(
                f"第 {index + 1} 条记录 richness_score 必须是 0-100 的整数"
            )


def save_json(payload):
    """Write JSON into the checked-out website repository; Actions does the push."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = OUTPUT_FILE.with_suffix(OUTPUT_FILE.suffix + ".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")

    # Validate the exact temporary file before replacing the published file.
    with temp_file.open("r", encoding="utf-8") as file:
        saved_payload = json.load(file)
    validate_payload(saved_payload)
    os.replace(temp_file, OUTPUT_FILE)

    print(f"JSON 文件生成成功：{OUTPUT_FILE}")
    print(f"网站数量：{payload['total']}")


def main():
    print("开始从 Cloudflare D1 读取网站数据")
    print(f"D1 批次大小：{BATCH_SIZE}")
    print(f"JSON 输出位置：{OUTPUT_FILE}")

    websites = get_all_websites()
    if not websites:
        raise RuntimeError(
            "没有找到符合发布条件的网站；为避免覆盖线上 JSON，本次停止发布。"
        )

    payload = build_payload(websites)
    validate_payload(payload)
    save_json(payload)
    print("Publisher 数据生成完成，后续由 GitHub Actions 提交并推送")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Publisher 失败：{exc}", file=sys.stderr)
        raise
