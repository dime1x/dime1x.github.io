import json
import os
import sys
from datetime import datetime, timedelta, timezone
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

# Re-read a small time window on every incremental run to avoid missing
# updates when updated_at has only second-level precision.
LOOKBACK_SECONDS = int(os.getenv("PUBLISH_LOOKBACK_SECONDS", "600"))


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


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
    except (TypeError, ValueError, OverflowError):
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


def is_publishable(row):
    return (
        row.get("quality_status") == "qualified"
        and row.get("crawl_status") == "success"
        and row.get("image_status") == "ready"
        and bool((row.get("image_url") or "").strip())
    )


def select_columns():
    return """
        domain,
        url,
        final_url,
        title,
        description,
        keywords,
        image_url,
        richness_score,
        first_seen,
        updated_at,
        quality_status,
        crawl_status,
        image_status
    """


def get_full_batch(cursor_first_seen=None, cursor_domain=None):
    """Initial one-time full export, using keyset pagination (no OFFSET)."""
    where = """
        WHERE quality_status = 'qualified'
          AND crawl_status = 'success'
          AND image_status = 'ready'
          AND image_url IS NOT NULL
          AND image_url != ''
    """
    params = []

    if cursor_first_seen is not None:
        where += """
          AND (
                first_seen < ?
                OR (first_seen = ? AND domain > ?)
          )
        """
        params.extend([cursor_first_seen, cursor_first_seen, cursor_domain])

    sql = f"""
        SELECT {select_columns()}
        FROM websites
        {where}
        ORDER BY first_seen DESC, domain ASC
        LIMIT ?
    """
    params.append(BATCH_SIZE)
    return _rows(_query(sql, params))


def get_changed_batch(cutoff, cursor_updated_at=None, cursor_domain=None):
    """
    Read only rows changed since the previous publish watermark.
    Assumes websites.updated_at is maintained whenever a row changes.
    """
    where = "WHERE updated_at IS NOT NULL AND updated_at >= ?"
    params = [cutoff]

    if cursor_updated_at is not None:
        where += """
          AND (
                updated_at > ?
                OR (updated_at = ? AND domain > ?)
          )
        """
        params.extend([cursor_updated_at, cursor_updated_at, cursor_domain])

    sql = f"""
        SELECT {select_columns()}
        FROM websites
        {where}
        ORDER BY updated_at ASC, domain ASC
        LIMIT ?
    """
    params.append(BATCH_SIZE)
    return _rows(_query(sql, params))


def load_existing():
    """Load the currently published JSON shards from the checked-out repo."""
    if not MANIFEST_FILE.exists():
        return None, None

    try:
        with MANIFEST_FILE.open("r", encoding="utf-8") as file:
            manifest = json.load(file)

        entries = manifest.get("files")
        if not isinstance(entries, list):
            return None, None

        websites = []
        seen = set()

        for entry in entries:
            filename = Path(entry.get("path", "")).name
            if not filename or not filename.endswith(".json"):
                return None, None

            shard_path = SHARDS_DIR / filename
            if not shard_path.exists():
                return None, None

            with shard_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)

            rows = payload.get("websites")
            if not isinstance(rows, list):
                return None, None

            for item in rows:
                domain = (item.get("domain") or "").strip().lower()
                if domain and domain not in seen:
                    websites.append(item)
                    seen.add(domain)

        watermark = manifest.get("sync_watermark")
        if not watermark:
            return None, None

        return websites, watermark

    except (OSError, ValueError, TypeError, KeyError):
        # A missing/corrupt old manifest triggers a safe full rebuild.
        return None, None


def full_rebuild():
    print("未找到可用的增量发布水位线，执行一次完整初始化发布")
    websites = []
    seen_domains = set()
    cursor_first_seen = None
    cursor_domain = None

    while True:
        rows = get_full_batch(cursor_first_seen, cursor_domain)
        if not rows:
            break

        for row in rows:
            item = normalize_website(row)
            domain = item["domain"]
            if domain and domain not in seen_domains:
                websites.append(item)
                seen_domains.add(domain)

        last = rows[-1]
        cursor_first_seen = last.get("first_seen")
        cursor_domain = last.get("domain")

        if len(rows) < BATCH_SIZE:
            break

        print(f"完整初始化已读取 {len(websites)} 条符合条件的网站")

    if not websites:
        raise RuntimeError("没有找到符合发布条件的网站；为避免覆盖线上数据，本次停止发布。")

    return websites


def apply_incremental_updates(websites, previous_watermark, run_started_at):
    """Upsert changed eligible rows and remove rows that no longer qualify."""
    by_domain = {
        (item.get("domain") or "").strip().lower(): item
        for item in websites
        if (item.get("domain") or "").strip()
    }

    try:
        previous_dt = datetime.fromisoformat(previous_watermark.replace("Z", "+00:00"))
        if previous_dt.tzinfo is None:
            previous_dt = previous_dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise RuntimeError(
            "manifest.json 中的 sync_watermark 格式无效；请删除旧 manifest 后执行一次完整初始化。"
        )

    cutoff = iso_utc(previous_dt - timedelta(seconds=LOOKBACK_SECONDS))
    cursor_updated_at = None
    cursor_domain = None
    changed_count = 0
    removed_count = 0

    while True:
        rows = get_changed_batch(cutoff, cursor_updated_at, cursor_domain)
        if not rows:
            break

        for row in rows:
            domain = (row.get("domain") or "").strip().lower()
            if not domain:
                continue

            if is_publishable(row):
                by_domain[domain] = normalize_website(row)
            elif domain in by_domain:
                del by_domain[domain]
                removed_count += 1

            changed_count += 1

        last = rows[-1]
        cursor_updated_at = last.get("updated_at")
        cursor_domain = last.get("domain")

        if len(rows) < BATCH_SIZE:
            break

        print(f"增量处理中：已处理 {changed_count} 条变更记录")

    # Preserve the existing published order; newly discovered domains are
    # appended. This avoids reshuffling every card on each small update.
    merged = list(by_domain.values())
    print(
        f"增量完成：处理 {changed_count} 条变更，"
        f"移除 {removed_count} 条不再符合条件的网站"
    )
    return merged


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(path.suffix + ".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")

    os.replace(temp_file, path)


def save_shards(websites, sync_watermark):
    if SHARD_SIZE < 1:
        raise ValueError("PUBLISH_SHARD_SIZE 必须大于 0")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = iso_utc(utc_now())
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
        "sync_watermark": sync_watermark,
        "total": total,
        "shard_size": SHARD_SIZE,
        "total_shards": total_shards,
        "files": files,
    }

    write_json_atomic(MANIFEST_FILE, manifest)

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

    required_fields = {
        "domain", "url", "title", "description", "keywords", "image",
        "richness_score",
    }
    seen_domains = set()
    verified_count = 0
    scored_count = 0

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

            score = item["richness_score"]
            if not isinstance(score, int) or not 0 <= score <= 100:
                raise ValueError(
                    f"{filename} 第 {index + 1} 条记录 richness_score 必须是 0-100 的整数"
                )

            if score > 0:
                scored_count += 1

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
    print(f"其中 richness_score 大于 0 的记录：{scored_count} 条")


def main():
    if BATCH_SIZE < 1:
        raise ValueError("PUBLISH_BATCH_SIZE 必须大于 0")

    print("开始从 Cloudflare D1 增量发布网站数据")
    print(f"D1 读取批次：{BATCH_SIZE}")
    print(f"JSON 分片大小：{SHARD_SIZE}")
    print(f"变更回看窗口：{LOOKBACK_SECONDS} 秒")

    # Capture the watermark before querying. Any changes made during this
    # run will be picked up by the next run.
    run_started_at = iso_utc(utc_now())

    existing, previous_watermark = load_existing()

    if existing is None:
        websites = full_rebuild()
    else:
        print(f"读取本地已发布数据：{len(existing)} 条")
        websites = apply_incremental_updates(
            existing, previous_watermark, run_started_at
        )

        if not websites:
            raise RuntimeError(
                "增量处理后没有可发布的网站；为避免清空线上数据，本次停止发布。"
            )

    save_shards(websites, run_started_at)
    verify_output(len(websites))
    print("Publisher 增量发布完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Publisher 失败：{exc}", file=sys.stderr)
        raise
