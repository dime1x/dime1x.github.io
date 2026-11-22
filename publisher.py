import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from database import _query, _rows

BATCH_SIZE = int(os.getenv("PUBLISH_BATCH_SIZE", "500"))
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://img.deople.ai").rstrip("/")

# The destination GitHub repository. publisher.py itself clones, writes and pushes.
TARGET_REPO = os.getenv("PUBLISH_REPO", "dime1x/dime1x.github.io")
TARGET_BRANCH = os.getenv("PUBLISH_BRANCH", "main")
TARGET_FILE = os.getenv("PUBLISH_PATH", "data/websites.json")

# Required GitHub PAT: Contents read/write permission on TARGET_REPO.
GITHUB_TOKEN = os.getenv("DATA_REPO_TOKEN", "").strip()


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
    """Return a safe 0-100 richness score."""
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError):
        score = 0
    return max(0, min(100, score))


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
        # Richness is independent from quality_score; it measures homepage
        # text, media, navigation, structure and interaction richness.
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


def run_git(args, cwd=None, env=None):
    command = ["git"] + args
    print("$ " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        raise RuntimeError(
            f"Git 命令执行失败（exit={result.returncode}）：{' '.join(args)}"
        )
    return result.stdout.strip()


def build_git_env():
    # Avoid putting the PAT in the repository URL or command-line arguments.
    auth = base64.b64encode(
        f"x-access-token:{GITHUB_TOKEN}".encode("utf-8")
    ).decode("ascii")
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {auth}"
    return env


def push_json_to_github(payload):
    if not GITHUB_TOKEN:
        raise RuntimeError(
            "缺少 DATA_REPO_TOKEN。请在 GitHub Actions Secrets 中配置，"
            "并授予目标仓库 Contents: Read and write 权限。"
        )

    if "/" not in TARGET_REPO:
        raise ValueError("PUBLISH_REPO 格式错误，应为 owner/repository")

    if TARGET_FILE.startswith("/") or ".." in Path(TARGET_FILE).parts:
        raise ValueError("PUBLISH_PATH 必须是目标仓库内的相对路径")

    repo_url = f"https://github.com/{TARGET_REPO}.git"
    git_env = build_git_env()

    with tempfile.TemporaryDirectory(prefix="deople-publisher-") as temp_dir:
        repo_dir = Path(temp_dir) / "target-repo"

        run_git(
            ["clone", "--depth", "1", "--branch", TARGET_BRANCH, repo_url, str(repo_dir)],
            env=git_env,
        )

        output_path = repo_dir / TARGET_FILE
        output_path.parent.mkdir(parents=True, exist_ok=True)

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        output_path.write_text(serialized, encoding="utf-8")

        # Validate the exact file written into the destination repository.
        with output_path.open("r", encoding="utf-8") as file:
            saved_payload = json.load(file)
        validate_payload(saved_payload)

        run_git(
            ["config", "user.name", "github-actions[bot]"],
            cwd=repo_dir,
        )
        run_git(
            ["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=repo_dir,
        )
        run_git(["add", TARGET_FILE], cwd=repo_dir)

        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir,
            env=git_env,
        )

        if changed.returncode == 0:
            print(f"{TARGET_REPO}/{TARGET_FILE} 内容没有变化，无需提交")
            return

        if changed.returncode != 1:
            raise RuntimeError("无法检查目标 JSON 文件是否发生变化")

        run_git(
            ["commit", "-m", "Update website directory data"],
            cwd=repo_dir,
        )
        run_git(
            ["push", "origin", TARGET_BRANCH],
            cwd=repo_dir,
            env=git_env,
        )

        print(f"发布成功：{TARGET_REPO}/{TARGET_BRANCH}/{TARGET_FILE}")
        print(f"网站数量：{payload['total']}")


def main():
    print("开始从 Cloudflare D1 读取网站数据")
    print(f"JSON 目标仓库：{TARGET_REPO}")
    print(f"目标分支：{TARGET_BRANCH}")
    print(f"目标文件：{TARGET_FILE}")

    websites = get_all_websites()

    # Never replace the live JSON with an empty dataset by accident.
    if not websites:
        raise RuntimeError(
            "没有找到符合发布条件的网站；为避免覆盖线上 JSON，本次停止发布。"
        )

    payload = build_payload(websites)
    validate_payload(payload)

    # No persistent JSON is written into the crawler repository.
    # publisher.py clones the website repo, writes the JSON there, and pushes it.
    push_json_to_github(payload)
    print("Publisher 完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Publisher 失败：{exc}", file=sys.stderr)
        raise
