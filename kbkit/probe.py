# -*- coding: utf-8 -*-
"""
站点侦察：判断文档站是 SPA 还是 SSG，并挖出全站清单。

**这是整条流水线的第一个岔路口** —— 判错了后面全白做：
  SSG（VuePress 2 / Plume 等预渲染）  正文就在 HTML 里，直接解析，别挖接口
  SPA（Nuxt / Next 客户端渲染）       HTML 只有标题骨架，必须找背后的 JSON 接口
  结构化导出包（厂商「导出」功能）     类名规整，写专用流式解析器，别用通用转换器

VuePress 站的两个清单都在 app.js 里（比逐页抓面包屑可靠得多）：
  1) 路由表  ["<path>.html",{loader:…,meta:{title:"…"}}]  → 全站页面 + 标题
  2) sidebar 配置对象 → 「路径前缀 → 中文层级」映射

CLI：
    python tools/siteprobe.py detect <url>              # 判站点类型
    python tools/siteprobe.py routes <url> --out _source/_toc.json
"""
import argparse
import gzip
import json
import os
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- 判型

def detect_site_type(html):
    """
    返回 (类型, 证据)。类型 ∈ vuepress-ssg / ssg-generic / spa-nuxt / spa-unknown / unknown
    """
    if "vp-doc" in html or "__VP_HASH_MAP__" in html:
        return "vuepress-ssg", "含 vp-doc 标记（VuePress 预渲染，正文在 HTML 里）"
    if "window.__NUXT__" in html:
        return "spa-nuxt", "含 window.__NUXT__（Nuxt 的 SSR 载荷，正文要另外取）"
    if "__NEXT_DATA__" in html:
        return "spa-unknown", "含 __NEXT_DATA__（Next.js）"
    # 正文规模判据：HTML 里有成规模的 <p>/<table> 就是 SSG
    if html.count("<p") > 20 or html.count("<table") > 2:
        return "ssg-generic", f"HTML 里已有成规模正文（p={html.count('<p')} table={html.count('<table')}）"
    if "id=\"app\"" in html or "id=app" in html:
        return "spa-unknown", "只有挂载点 #app，正文不在 HTML 里"
    return "unknown", "无法判断，先人工看一眼"


def probe(url):
    html = http_get(url)
    kind, why = detect_site_type(html)
    return {"url": url, "bytes": len(html), "type": kind, "evidence": why}


# ---------------------------------------------------------------- VuePress

def match_obj(text, start):
    """start 指向 '{'，返回配对 '}' 之后的下标。"""
    depth, in_str, esc, k = 0, False, False, start
    while k < len(text):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
        k += 1
    return -1


def js_object_to_dict(frag):
    """
    JS 对象字面量 → dict。给裸键名补引号、替换布尔字面量即可（不必写 JS 解析器）。
    """
    js = re.sub(r"([{,])([A-Za-z_$][A-Za-z0-9_$]*):", r'\1"\2":', frag)
    js = js.replace("!1", "false").replace("!0", "true")
    return json.loads(js)


def parse_sidebar(src):
    """抠出 VuePress 的 sidebar 配置对象。找不到返回 {}。"""
    anchor = src.find('"/start/":{items:[')
    if anchor < 0:
        # 兜底：找任意 "<key>/":{items:[ 形态
        m = re.search(r'"[^"]*/":\{items:\[', src)
        if not m:
            return {}
        anchor = m.start()
    start = src.rfind("{", 0, anchor)
    end = match_obj(src, start)
    if end < 0:
        return {}
    return js_object_to_dict(src[start:end])


def walk_sidebar(items, base, ancestors, out):
    """递归 sidebar，产出 {路径前缀: [中文层级]}。"""
    if items == "auto" or items is None or not isinstance(items, list):
        return
    for it in items:
        if isinstance(it, str):
            out[base + it + "/"] = ancestors + [it]
            continue
        text = it.get("text", "")
        prefix = it.get("prefix", "")
        if prefix.startswith("/"):
            newbase = prefix if prefix.endswith("/") else prefix + "/"
        else:
            newbase = base + prefix + "/"
        sub = it.get("items")
        if sub == "auto":
            out[newbase] = ancestors + [text]
        else:
            out.setdefault(newbase, ancestors + [text])
            walk_sidebar(sub, newbase, ancestors + [text], out)


def extract_route_titles(src):
    """从路由表提取 {path: title}。"""
    pat = re.compile(
        r'\["(/[^"]*?\.html)",\s*\{loader:\(\)=>.*?meta:\{title:"((?:[^"\\]|\\.)*)"\}\}\]')
    out = {}
    for path, title in pat.findall(src):
        out[path[:-5] if path.endswith(".html") else path] = title
    return out


def resolve_app_js(origin, landing, local_dir=None, app_js=None):
    """
    返回 app.js 的本地路径。给定路径存在就直接用；否则从首页找出引用并下载。
    文件名带内容哈希，站点改版即变，所以必须每次现找。
    """
    if app_js and os.path.exists(app_js):
        return app_js
    landing_html = http_get(landing)
    m = re.search(r'["\'](/[^"\']*?assets/app-[A-Za-z0-9_-]+\.js)["\']', landing_html)
    if not m:
        raise SystemExit("首页里没找到 app-*.js 引用，站点结构可能已变；请用 --app-js 手动指定")
    url = origin.rstrip("/") + m.group(1)
    out = os.path.join(local_dir or ".", os.path.basename(m.group(1)))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(http_get(url))
    print(f"  已下载 {url} -> {out}")
    return out


def build_toc(app_js_path, origin, blocks):
    """
    产出路由清单。
    blocks: [{"dir","name","alias","prefix"}]，用于把路由归到块。
    """
    src = open(app_js_path, encoding="utf-8").read()
    sidebar = parse_sidebar(src)
    prefix_map = {}
    for key, node in sidebar.items():
        base = node.get("prefix", key)
        base = base if base.endswith("/") else base + "/"
        walk_sidebar(node.get("items"), base, [], prefix_map)
    titles = extract_route_titles(src)

    prefixes_sorted = sorted(prefix_map, key=len, reverse=True)
    pages = []
    for path in sorted(titles):
        blk = next((b for b in blocks if path.startswith(b["prefix"].rstrip("/"))), None)
        if blk is None:
            continue
        cat = None
        probe_p = path.rstrip("/") + "/"
        for pfx in prefixes_sorted:
            if probe_p.startswith(pfx):
                cat = prefix_map[pfx]
                break
        pages.append({
            "path": path,
            "url": origin.rstrip("/") + path + ".html",
            "title": titles[path],
            "block": blk["dir"],
            "blockCn": blk["name"],
            "category": cat or [blk["name"]],
            "categoryCn": " / ".join(cat or [blk["name"]]),
        })
    return {
        "source": origin,
        "appJs": os.path.basename(app_js_path),
        "blocks": blocks,
        "totalPages": len(pages),
        "prefixCn": dict(sorted(prefix_map.items())),
        "pages": pages,
    }


# ---------------------------------------------------------------- CLI

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="siteprobe.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="判断站点类型")
    d.add_argument("url")

    r = sub.add_parser("routes", help="从 VuePress 站提取路由清单（写 _toc.json）")
    r.add_argument("url", help="站点首页，如 https://developer.fxiaoke.com/openapi_v2/")
    r.add_argument("--app-js", default=None, help="本地 app-*.js；不给则自动下载")
    r.add_argument("--origin", default=None, help="站点 origin，默认从 url 推断")
    r.add_argument("--out", default="_source/_toc.json")
    r.add_argument("--js-dir", default="js")
    r.add_argument("--blocks", default="",
                   help='块定义，格式「前缀:目录[:中文名[:别名]]」，逗号分隔。'
                        '如 "/start/:01_start:快速开始:start,/api/:02_api:接口手册:api"')

    args = ap.parse_args(argv)

    if args.cmd == "detect":
        info = probe(args.url)
        print(f"URL   : {info['url']}")
        print(f"大小  : {info['bytes']} 字节")
        print(f"类型  : {info['type']}")
        print(f"依据  : {info['evidence']}")
        print()
        if info["type"] in ("vuepress-ssg", "ssg-generic"):
            print("→ 正文在 HTML 里，直接解析。VuePress 站点可用 `routes` 子命令一次拿全站清单。")
        else:
            print("→ 正文不在 HTML 里，需要找背后的 JSON 接口（见 SKILL.md §1.2–1.4）。")
        return 0

    # routes
    from urllib.parse import urlparse
    origin = args.origin or f"{urlparse(args.url).scheme}://{urlparse(args.url).netloc}"
    blocks = []
    for spec in filter(None, (s.strip() for s in args.blocks.split(","))):
        parts = spec.split(":")
        if len(parts) < 2:
            raise SystemExit(f"--blocks 每项至少「前缀:目录」，收到 {spec!r}")
        prefix, d = parts[0], parts[1]
        name = parts[2] if len(parts) > 2 and parts[2] else d
        alias = parts[3] if len(parts) > 3 and parts[3] else d
        blocks.append({"prefix": prefix, "dir": d, "name": name, "alias": alias})
    if not blocks:
        blocks = [{"prefix": "/", "dir": "01_docs", "alias": "docs", "name": "文档"}]

    app_js = resolve_app_js(origin, args.url, args.js_dir, args.app_js)
    toc = build_toc(app_js, origin, blocks)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(toc, f, ensure_ascii=False, indent=1)
    print(f"已写出 {args.out}：{toc['totalPages']} 个页面")
    for b in blocks:
        n = sum(1 for p in toc["pages"] if p["block"] == b["dir"])
        print(f"  {b['dir']:12s} {n} 页")
    return 0


if __name__ == "__main__":
    sys.exit(main())
