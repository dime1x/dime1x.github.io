
import os
import re
import json
import logging
import tempfile

from pathlib import Path
from datetime import datetime, timezone

from database import _query, _rows


# ============================================================
# 1. 基础配置
# ============================================================

# 每次从 D1 读取多少条
BATCH_SIZE = int(
    os.getenv("PUBLISH_BATCH_SIZE", "500")
)

# R2 图片域名
IMAGE_BASE_URL = os.getenv(
    "IMAGE_BASE_URL",
    "https://img.deople.ai"
).rstrip("/")

# JSON 输出路径
OUTPUT_FILE = Path(
    os.getenv(
        "PUBLISH_OUTPUT",
        "data/websites.json"
    )
)

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("DeoplePublisher")


# ============================================================
# 2. 处理关键词
# ============================================================

def normalize_keywords(value):
    """
    将数据库中的 keywords 统一转换成字符串数组。
    支持：
    - JSON 数组
    - 逗号分隔字符串
    - 空值
    """

    if not value:
        return []

    if isinstance(value, list):

        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, str):

        value = value.strip()

        if not value:
            return []

        # 尝试解析 JSON 数组
        try:

            parsed = json.loads(value)

            if isinstance(parsed, list):

                return [
                    str(item).strip()
                    for item in parsed
                    if str(item).strip()
                ]

        except (json.JSONDecodeError, TypeError):
            pass

        # 兼容逗号、分号、竖线分隔
        parts = re.split(
            r"[,;|，；]",
            value
        )

        return [
            item.strip()
            for item in parts
            if item.strip()
        ]

    return []


# ============================================================
# 3. 生成手机端图片地址
# ============================================================

def get_mobile_image(domain, desktop_image):
    """
    桌面端图片：domain_240.jpg
    手机端图片：domain_120.jpg
    """

    if desktop_image:

        if desktop_image.endswith("_240.jpg"):

            return desktop_image[:-8] + "_120.jpg"

    return f"{IMAGE_BASE_URL}/{domain}_120.jpg"


# ============================================================
# 4. 清理网站数据
# ============================================================

def normalize_website(item):

    domain = str(
        item.get("domain") or ""
    ).strip().lower()

    if not domain:
        return None

    title = str(
        item.get("title") or domain
    ).strip()

    description = str(
        item.get("description") or ""
    ).strip()

    url = str(
        item.get("final_url")
        or item.get("url")
        or ""
    ).strip()

    image = str(
        item.get("image_url") or ""
    ).strip()

    if not url or not image:
        return None

    mobile_image = get_mobile_image(
        domain,
        image
    )

    keywords = normalize_keywords(
        item.get("keywords")
    )

    return {
        "domain": domain,
        "url": url,
        "title": title,
        "description": description,
        "keywords": keywords,
        "image": image,
        "image_mobile": mobile_image
    }


# ============================================================
# 5. 从 D1 获取一批合格网站
# ============================================================

def get_publish_batch(limit, offset):

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
        LIMIT ?
        OFFSET ?
    """

    result = _query(
        sql,
        [
            limit,
            offset
        ]
    )

    return _rows(result)


# ============================================================
# 6. 读取全部符合条件的网站
# ============================================================

def get_all_websites():

    websites = []

    offset = 0

    while True:

        logger.info(
            "读取 D1 数据：offset=%s，limit=%s",
            offset,
            BATCH_SIZE
        )

        batch = get_publish_batch(
            BATCH_SIZE,
            offset
        )

        if not batch:
            break

        websites.extend(batch)

        logger.info(
            "本批读取：%s 条，累计：%s 条",
            len(batch),
            len(websites)
        )

        if len(batch) < BATCH_SIZE:
            break

        offset += BATCH_SIZE

    return websites


# ============================================================
# 7. 网站去重
# ============================================================

def deduplicate_websites(items):

    unique = {}

    for item in items:

        website = normalize_website(item)

        if not website:
            continue

        domain = website["domain"]

        # 同一个域名只保留一条
        if domain not in unique:

            unique[domain] = website

    result = list(
        unique.values()
    )

    return result


# ============================================================
# 8. 生成 JSON 文件
# ============================================================

def save_json(websites):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # 使用临时文件，避免写入过程中产生不完整 JSON
    temp_file = OUTPUT_FILE.with_suffix(
        OUTPUT_FILE.suffix + ".tmp"
    )

    data = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "total": len(websites),

        "websites": websites
    }

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    # 写入成功后再替换正式文件
    os.replace(
        temp_file,
        OUTPUT_FILE
    )

    logger.info(
        "JSON 文件生成成功：%s",
        OUTPUT_FILE
    )


# ============================================================
# 9. 检查 JSON 文件
# ============================================================

def verify_json():

    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    websites = data.get(
        "websites",
        []
    )

    logger.info(
        "JSON 验证成功，网站数量：%s",
        len(websites)
    )

    if len(websites) != data.get("total"):

        raise RuntimeError(
            "JSON total 数量与 websites 数组长度不一致"
        )

    return len(websites)


# ============================================================
# 10. 主程序
# ============================================================

def main():

    logger.info("===================================")
    logger.info("Deople Publisher 启动")
    logger.info("D1 批次大小：%s", BATCH_SIZE)
    logger.info("JSON 输出：%s", OUTPUT_FILE)
    logger.info("===================================")

    # 第一步：从 D1 读取网站
    raw_websites = get_all_websites()

    logger.info(
        "D1 原始记录数量：%s",
        len(raw_websites)
    )

    # 第二步：清理和去重
    websites = deduplicate_websites(
        raw_websites
    )

    logger.info(
        "去重后网站数量：%s",
        len(websites)
    )

    # 第三步：生成 JSON
    save_json(websites)

    # 第四步：检查 JSON
    total = verify_json()

    logger.info("===================================")
    logger.info("Deople Publisher 完成")
    logger.info("发布网站数量：%s", total)
    logger.info("输出文件：%s", OUTPUT_FILE)
    logger.info("===================================")


if __name__ == "__main__":
    try:

        main()

    except Exception:

        logger.exception(
            "Publisher 执行失败"
        )

        raise