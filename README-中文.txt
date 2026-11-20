Deople GitHub Actions 分页生成工具

压缩包内文件：
1. generate_pages.py
   读取分片 manifest 和所有 JSON 分片，按 domain 去重、随机打乱，
   每页默认 80 个网站，生成 page/1/index.html ... page/N/index.html，
   同时生成总 sitemap.xml。

2. .github/workflows/generate-pages.yml
   GitHub Actions 工作流。支持手动运行和每天定时运行。

放入仓库的方法：
把 generate_pages.py 放到仓库根目录。
把 .github/workflows/generate-pages.yml 放到仓库相同路径。
确认分片 manifest 的实际路径是 data/manifest.json；若不是，
修改 YAML 中 --input 后面的路径。
确认 manifest 的分片路径是相对于 manifest 所在目录的仓库内路径。

支持的 manifest 结构：
{
  "files": [
    "shards/websites-001.json",
    "shards/websites-002.json"
  ]
}
或：
{
  "files": [
    {"path": "shards/websites-001.json"},
    {"path": "shards/websites-002.json"}
  ]
}

每个分片可以是：
{"websites": [{ "domain": "...", "title": "...", "description": "...", "image_url": "...", "url": "..." }]}
也可以直接是网站对象数组。

字段兼容：
domain/host；url/final_url；title/name；description/summary；
image_url/image/screenshot。

生成文件：
page/N/index.html
sitemap.xml

Sitemap 包含首页 / 和所有真实生成的分页 /page/N/。
不会连接 Cloudflare D1，也不会覆盖根目录 index.html。
随机种子默认 42，因此相同输入会得到相同随机顺序。
如需每次重新随机，修改 workflow 中 --seed 的值。

注意：
本工具会生成独立的 Deople 页面模板，并非复制根目录 index.html 的 CSS/布局。
如果必须与现有首页做到像素级一致，需要把当前首页 index.html 一并提供，
再把生成器改为复用首页的真实模板和卡片渲染逻辑。
