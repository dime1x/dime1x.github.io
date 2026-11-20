\
#!/usr/bin/env python3
# Generate Deople's static listing pages from a sharded JSON manifest.
# No database/API access is used. All input files must exist in the checked-out repo.

import argparse
import html
import json
import random
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def find_manifest_shards(manifest, manifest_path):
    """Accept common manifest layouts: files/shards/data arrays of strings or objects."""
    entries = None
    for key in ("files", "shards", "chunks", "parts", "data"):
        value = manifest.get(key) if isinstance(manifest, dict) else None
        if isinstance(value, list):
            entries = value
            break
    if entries is None:
        raise ValueError(
            "Manifest 中没有找到 files/shards/chunks/parts/data 数组。"
            "请检查 manifest.json 的结构。"
        )

    paths = []
    for item in entries:
        if isinstance(item, str):
            raw = item
        elif isinstance(item, dict):
            raw = None
            for key in ("path", "file", "filename", "name", "url"):
                if isinstance(item.get(key), str) and item[key].strip():
                    raw = item[key].strip()
                    break
            if raw is None:
                raise ValueError(f"无法识别 manifest 分片记录：{item}")
        else:
            raise ValueError(f"不支持的 manifest 分片记录：{item!r}")

        # GitHub Actions checks out the repository; shard paths should be local.
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https"):
            raise ValueError(
                f"manifest 使用了远程分片地址：{raw}。"
                "当前版本只读取仓库内的本地 JSON 分片。"
            )
        # Manifest entries often start with "/" even though they are
        # repository-relative paths (for example: /data/websites/001.json).
        # In GitHub Actions, do NOT interpret those as Linux filesystem paths.
        raw_path = Path(raw)
        candidates = []

        if raw_path.is_absolute():
            repo_relative = Path.cwd() / str(raw_path).lstrip("/")
            manifest_relative = manifest_path.parent / str(raw_path).lstrip("/")
            candidates.extend([repo_relative, manifest_relative])
        else:
            candidates.extend([
                manifest_path.parent / raw_path,
                Path.cwd() / raw_path,
            ])

        resolved = None
        for candidate in candidates:
            candidate = candidate.resolve()
            if candidate.is_file():
                resolved = candidate
                break

        if resolved is None:
            attempted = ", ".join(str(p.resolve()) for p in candidates)
            raise FileNotFoundError(
                f"Manifest 引用了分片 {raw!r}，但在以下位置都找不到：{attempted}。"
                "请确认分片文件已提交到 GitHub 仓库，且 manifest 路径正确。"
            )

        paths.append(resolved)

    # Preserve manifest order while avoiding duplicate paths.
    return list(dict.fromkeys(paths))

def records_from_json(data, source):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("websites", "items", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise ValueError(f"{source} 中没有找到网站数组（websites/items/results/data）。")

def read_websites(input_path):
    data = load_json(input_path)
    if isinstance(data, list):
        raw_records = data
    elif isinstance(data, dict) and any(
        isinstance(data.get(k), list) for k in ("websites", "items", "results")
    ):
        raw_records = records_from_json(data, input_path)
    elif isinstance(data, dict):
        shard_paths = find_manifest_shards(data, input_path)
        raw_records = []
        print(f"Manifest 找到 {len(shard_paths)} 个分片")
        for i, shard_path in enumerate(shard_paths, 1):
            if not shard_path.exists():
                raise FileNotFoundError(f"找不到分片文件：{shard_path}")
            shard_data = load_json(shard_path)
            rows = records_from_json(shard_data, shard_path)
            raw_records.extend(rows)
            print(f"[{i}/{len(shard_paths)}] {shard_path.name}: {len(rows)} 条")
    else:
        raise ValueError("JSON 根节点必须是数组或对象。")

    # Deduplicate by normalized domain (or URL when domain is absent).
    unique = {}
    skipped = 0
    for item in raw_records:
        if not isinstance(item, dict):
            skipped += 1
            continue
        domain = str(item.get("domain") or item.get("host") or "").strip().lower()
        url = str(item.get("url") or item.get("final_url") or "").strip()
        if not domain and url:
            domain = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
        domain = domain.removeprefix("www.")
        if not domain:
            skipped += 1
            continue
        if not url:
            url = "https://" + domain
        elif not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        item_copy = dict(item)
        item_copy["_domain"] = domain
        item_copy["_url"] = url
        unique.setdefault(domain, item_copy)

    websites = list(unique.values())
    print(f"原始记录：{len(raw_records)}；去重后：{len(websites)}；跳过：{skipped}")
    return websites

def esc(value):
    return html.escape(str(value or ""), quote=True)

def card_html(site):
    title = site.get("title") or site.get("name") or site["_domain"]
    description = site.get("description") or site.get("summary") or ""
    image = site.get("image_url") or site.get("image") or site.get("screenshot") or ""
    domain = site["_domain"]
    image_block = (
        f'<img class="thumb" src="{esc(image)}" alt="{esc(title)}" loading="lazy" '
        f'decoding="async" onerror="this.style.display=\'none\'">'
        if image else '<div class="thumb placeholder">DEOPLE</div>'
    )
    return f"""
    <article class="site-card">
      <a class="site-link" href="{esc(site['_url'])}" target="_blank" rel="noopener noreferrer">
        <div class="preview">{image_block}</div>
        <div class="card-body">
          <h2>{esc(title)}</h2>
          <p>{esc(description)}</p>
          <span class="domain">{esc(domain)}</span>
        </div>
      </a>
    </article>"""

def page_html(page, total_pages, sites, base_url, total_sites, per_page):
    canonical = f"{base_url}/page/{page}/"
    title = f"Explore Websites – Deople | Page {page}"
    description = (
        f"Discover {len(sites)} websites on Deople. Explore useful websites, "
        "tools and online resources."
    )
    cards = "\n".join(card_html(s) for s in sites)
    prev_link = (
        f'<a href="/page/{page-1}/" rel="prev">← Previous</a>' if page > 1 else ""
    )
    next_link = (
        f'<a href="/page/{page+1}/" rel="next">Next →</a>'
        if page < total_pages else ""
    )
    # Keep pagination compact on large sites.
    start = max(1, page - 2)
    end = min(total_pages, page + 2)
    numbers = []
    if start > 1:
        numbers.append('<a href="/page/1/">1</a>')
        if start > 2:
            numbers.append("<span>…</span>")
    for n in range(start, end + 1):
        numbers.append(
            f'<a class="current" aria-current="page" href="/page/{n}/">{n}</a>'
            if n == page else f'<a href="/page/{n}/">{n}</a>'
        )
    if end < total_pages:
        if end < total_pages - 1:
            numbers.append("<span>…</span>")
        numbers.append(f'<a href="/page/{total_pages}/">{total_pages}</a>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta name="robots" content="index,follow">
<link rel="icon" href="/favicon.svg">
<style>
:root{{color-scheme:dark;--bg:#090d16;--panel:#111827;--muted:#9ca3af;--line:#263244;--text:#f3f4f6;--blue:#3685ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
a{{color:inherit;text-decoration:none}}.wrap{{width:min(1180px,calc(100% - 32px));margin:auto}}
header{{border-bottom:1px solid var(--line);background:#0b1220}}.top{{height:68px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
.logo{{font-size:25px;font-weight:800;letter-spacing:-1.2px}}.logo b{{color:var(--blue)}}.top small{{color:var(--muted)}}
main{{padding:34px 0 60px}}.intro{{margin-bottom:24px}}h1{{font-size:clamp(26px,4vw,38px);line-height:1.15;margin:0 0 8px;letter-spacing:-1px}}.intro p{{color:var(--muted);margin:0}}
.tools{{display:flex;gap:12px;align-items:center;margin:22px 0}}input{{width:100%;max-width:440px;background:#111827;border:1px solid var(--line);border-radius:10px;padding:12px 14px;color:white;font:inherit;outline:none}}input:focus{{border-color:var(--blue)}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}
.site-card{{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:13px;overflow:hidden;transition:transform .16s,border-color .16s}}
.site-card:hover{{transform:translateY(-3px);border-color:#477fc9}}.preview{{height:170px;background:#0b1220;overflow:hidden}}.thumb{{width:100%;height:100%;object-fit:cover;display:block}}.placeholder{{display:grid;place-items:center;color:#64748b;font-weight:800;letter-spacing:3px}}
.card-body{{padding:13px 14px 15px}}h2{{font-size:15px;line-height:1.35;margin:0 0 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.card-body p{{font-size:13px;color:var(--muted);height:40px;overflow:hidden;margin:0 0 10px}}.domain{{font-size:12px;color:#75aaff;overflow-wrap:anywhere}}
.pagination{{display:flex;justify-content:center;align-items:center;flex-wrap:wrap;gap:8px;margin:34px 0}}.pagination a,.pagination span{{border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:#cbd5e1}}.pagination .current{{background:var(--blue);border-color:var(--blue);color:white}}
footer{{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:13px}}
@media(max-width:900px){{.grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media(max-width:650px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.preview{{height:125px}}.card-body{{padding:10px}}.card-body p{{font-size:12px}}}}
@media(max-width:380px){{.grid{{grid-template-columns:1fr}}.preview{{height:180px}}}}
</style>
</head>
<body>
<header><div class="wrap top"><a class="logo" href="/">Ð<b>eople</b></a><small>Website discovery</small></div></header>
<main class="wrap">
  <section class="intro"><h1>Explore websites</h1><p>Discover useful websites, tools and resources from around the web.</p></section>
  <div class="tools"><input id="search" type="search" placeholder="Search websites on this page…" aria-label="Search websites on this page"></div>
  <section class="grid" id="site-grid">{cards}</section>
  <nav class="pagination" aria-label="Pagination">{prev_link}{''.join(numbers)}{next_link}</nav>
</main>
<footer><div class="wrap">© {datetime.now(timezone.utc).year} Deople · Discover something useful.</div></footer>
<script>
const search=document.getElementById('search');
search.addEventListener('input',()=>{{
 const q=search.value.toLowerCase().trim();
 document.querySelectorAll('.site-card').forEach(card=>{{
  card.hidden=!card.innerText.toLowerCase().includes(q);
 }});
}});
</script>
</body></html>"""

def write_sitemap(output_dir, base_url, total_pages):
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}urlset")
    today = datetime.now(timezone.utc).date().isoformat()
    paths = ["/"] + [f"/page/{n}/" for n in range(1, total_pages + 1)]
    for path in paths:
        node = ET.SubElement(root, f"{{{ns}}}url")
        ET.SubElement(node, f"{{{ns}}}loc").text = base_url + path
        ET.SubElement(node, f"{{{ns}}}lastmod").text = today
    ET.ElementTree(root).write(
        output_dir / "sitemap.xml", encoding="utf-8", xml_declaration=True
    )

def main():
    parser = argparse.ArgumentParser(
        description="Generate Deople static listing pages from a sharded JSON manifest."
    )
    parser.add_argument("--input", default="data/manifest.json", help="manifest JSON path")
    parser.add_argument("--output", default=".", help="output directory")
    parser.add_argument("--base-url", default="https://deople.ai", help="site root URL")
    parser.add_argument("--per-page", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42, help="random shuffle seed")
    args = parser.parse_args()

    if args.per_page < 1:
        parser.error("--per-page 必须大于 0")
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"找不到 manifest：{input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url.rstrip("/")

    websites = read_websites(input_path)
    if not websites:
        raise ValueError("没有读取到有效网站记录，停止生成，避免覆盖现有页面。")

    random.Random(args.seed).shuffle(websites)
    total_sites = len(websites)
    total_pages = (total_sites + args.per_page - 1) // args.per_page

    # Remove stale generated page folders so sitemap and published pages stay aligned.
    page_root = output_dir / "page"
    if page_root.exists():
        for child in page_root.iterdir():
            if child.is_dir() and child.name.isdigit():
                import shutil
                shutil.rmtree(child)

    for page in range(1, total_pages + 1):
        start = (page - 1) * args.per_page
        batch = websites[start:start + args.per_page]
        target = page_root / str(page) / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            page_html(page, total_pages, batch, base_url, total_sites, args.per_page),
            encoding="utf-8"
        )
        print(f"生成 page/{page}/：{len(batch)} 个网站")

    write_sitemap(output_dir, base_url, total_pages)
    print(f"完成：{total_sites} 个网站，{total_pages} 个分页")
    print(f"Sitemap：{output_dir / 'sitemap.xml'}")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
