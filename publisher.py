import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from database import _query, _rows


# D1 read batch size and frontend JSON shard size.
BATCH_SIZE = int(os.getenv("PUBLISH_BATCH_SIZE", "500"))
SHARD_SIZE = int(os.getenv("PUBLISH_SHARD_SIZE", "500"))

IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://img.deople.ai").rstrip("/")
OUTPUT_FILE = Path(os.getenv("PUBLISH_OUTPUT", "website/data/websites.json"))
OUTPUT_DIR = Path(os.getenv("PUBLISH_OUTPUT_DIR", str(OUTPUT_FILE.parent)))
SHARDS_DIR = OUTPUT_DIR / "websites"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"


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


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(path.suffix + ".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")

    os.replace(temp_file, path)


def save_shards(websites):
    if SHARD_SIZE < 1:
        raise ValueError("PUBLISH_SHARD_SIZE 必须大于 0")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).isoformat()
    files = []

    total = len(websites)
    total_shards = (total + SHARD_SIZE - 1) // SHARD_SIZE

    for shard_index in range(total_shards):
        start = shard_index * SHARD_SIZE
        batch = websites[start:start + SHARD_SIZE]
        filename = f"{shard_index + 1:03d}.json"
        shard_path = SHARDS_DIR / filename

        payload = {
            "generated_at": generated_at,
            "shard": shard_index + 1,
            "count": len(batch),
            "websites": batch,
        }
        write_json_atomic(shard_path, payload)

        files.append({
            "id": shard_index + 1,
            "path": f"/data/websites/{filename}",
            "count": len(batch),
        })

        print(f"已生成分片：{shard_path}（{len(batch)} 条）")

    manifest = {
        "version": 1,
        "generated_at": generated_at,
        "total": total,
        "shard_size": SHARD_SIZE,
        "total_shards": total_shards,
        "files": files,
    }

    # Publish the manifest last so it never points to shards that have not
    # finished being written.
    write_json_atomic(MANIFEST_FILE, manifest)

    # Remove obsolete numbered shard files left by a previous larger publish.
    active_names = {entry["path"].rsplit("/", 1)[-1] for entry in files}
    for old_file in SHARDS_DIR.glob("*.json"):
        if old_file.name not in active_names and old_file.stem.isdigit():
            old_file.unlink()

    print(f"Manifest 已生成：{MANIFEST_FILE}")
    print(f"网站总数：{total}")
    print(f"分片数量：{total_shards}")
    print(f"每份最多：{SHARD_SIZE} 条")


def verify_output(expected_total):
    with MANIFEST_FILE.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    if manifest.get("total") != expected_total:
        raise ValueError(
            f"Manifest 总数不一致：{manifest.get('total')}，预期 {expected_total}"
        )

    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("Manifest 格式错误：files 必须是数组")

    if len(entries) != manifest.get("total_shards"):
        raise ValueError("Manifest 分片数量不一致")

    required_fields = {"domain", "url", "title", "description", "keywords", "image"}
    seen_domains = set()
    verified_count = 0

    for entry in entries:
        filename = entry.get("path", "").rsplit("/", 1)[-1]
        shard_path = SHARDS_DIR / filename

        with shard_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        websites = payload.get("websites")
        if not isinstance(websites, list):
            raise ValueError(f"{shard_path} 格式错误：websites 必须是数组")

        if payload.get("count") != len(websites):
            raise ValueError(f"{shard_path} 数量不一致")

        if entry.get("count") != len(websites):
            raise ValueError(f"Manifest 与分片数量不一致：{filename}")

        if len(websites) > SHARD_SIZE:
            raise ValueError(f"分片超过 {SHARD_SIZE} 条：{filename}")

        for index, item in enumerate(websites):
            missing = required_fields - set(item)
            if missing:
                raise ValueError(
                    f"{filename} 第 {index + 1} 条记录缺少字段：{sorted(missing)}"
                )

            domain = item.get("domain", "")
            if domain in seen_domains:
                raise ValueError(f"发现重复域名：{domain}")
            seen_domains.add(domain)

        verified_count += len(websites)

    if verified_count != expected_total:
        raise ValueError(
            f"分片记录总数不一致：{verified_count}，预期 {expected_total}"
        )

    print(f"JSON 分片校验通过：{verified_count} 条网站记录")


def main():
    print("开始从 Cloudflare D1 发布网站数据")
    print(f"D1 读取批次：{BATCH_SIZE}")
    print(f"JSON 分片大小：{SHARD_SIZE}")

    websites = get_all_websites()

    # Avoid accidentally replacing the live site's data with an empty publish.
    if not websites:
        raise RuntimeError(
            "没有找到符合发布条件的网站；为避免覆盖线上数据，本次停止发布。"
        )

    save_shards(websites)
    verify_output(len(websites))
    print("Publisher 分片发布完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Publisher 失败：{exc}", file=sys.stderr)
        raise
