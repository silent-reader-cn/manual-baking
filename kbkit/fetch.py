# -*- coding: utf-8 -*-
"""
通用抓取流水线：路由清单 → 并发抓取 → 转换 → 串行定路径写盘。

设计要点（都是实测结论）：
  * **两阶段**：先并发抓取（只拿元数据与正文），再串行定路径写盘 ——
    输出路径依赖页面元数据，且需要全局撞名保护，必须串行决定。
  * **分类以配置的 prefixCn 为准**，不用页面面包屑：未登记在站点导航里的页面
    面包屑会少一级，拿它建目录会互相覆盖。
  * 增量：已记录在清单里的页面直接跳过，不联网。
  * 站点体积陷阱：VuePress 会把整个侧边栏内联进每一页（实测单页 600KB 里
    97% 是侧边栏，正文只有 18KB）。只留正文，别把整页存下来。

CLI：
    python tools/fetch_manual.py                 # 增量抓取
    python tools/fetch_manual.py --force --jobs 8
    python tools/fetch_manual.py --only /object/CommoditiesAndProducts
    python tools/fetch_manual.py --prune         # 列出已下线页面（不删）
"""
import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config as config_mod
from . import corpus as corpus_mod
from . import html2md

_lock = threading.Lock()
_stats = {"ok": 0, "fail": 0, "bytes": 0}
_failures = []


def sanitize(name, fallback="untitled"):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    return name or fallback


def fetch(url, cfg, retries=None, timeout=None):
    retries = retries or cfg.fetch["retries"]
    timeout = timeout or cfg.fetch["timeout"]
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": cfg.fetch["userAgent"],
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                enc = "utf-8"
                m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
                if m:
                    enc = m.group(1)
                return raw.decode(enc, errors="replace"), len(raw)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


def compute_path(cfg, page, meta):
    """
    决定 (输出绝对路径, 分类目录, 标题)。

    分类优先用 toc 的 prefixCn（来自站点导航，权威且完整）；
    对象页这类「恒定两级」的结构由配置的 block 的 `pathDepth` 控制。
    """
    segs = page["path"].strip("/").split("/")
    crumbs = meta.get("breadcrumb") or []
    title = (crumbs[-1] if crumbs else "") or page.get("title") or segs[-1]

    blk = cfg.dir_to_block.get(page["block"], {})
    depth = blk.get("pathDepth")           # 例：对象页固定「模块/对象」两级

    if depth:
        # 用 URL 段 + 中英对照表拼固定层级
        mod_map = blk.get("segmentNameMap", {}) or {}
        cats = []
        for i in range(1, min(depth + 1, len(segs))):
            key = "/".join(segs[1:i + 1])
            cats.append(mod_map.get(key, segs[i]))
    else:
        cats = None
        prefix_cn = cfg.data.get("prefixCn") or {}
        probe = page["path"].rstrip("/") + "/"
        for pfx in sorted(prefix_cn, key=len, reverse=True):
            if probe.startswith(pfx):
                cats = list(prefix_cn[pfx])
                break
        if cats is None:
            cats = crumbs[:-1] if crumbs else []

    cats = [sanitize(c) for c in cats if c]
    parts = [cfg.references_dir, page["block"]] + cats + [sanitize(title)]
    return os.path.normpath(os.path.join(*parts) + ".md"), cats, title


def build_frontmatter(cfg, page, meta, cats, title):
    blk = cfg.dir_to_block.get(page["block"], {})
    fm = {"title": title, "path": page["path"], "url": page["url"],
          "block": blk.get("name", page["block"])}
    crumbs = meta.get("breadcrumb") or []
    for i, key in enumerate(blk.get("breadcrumbKeys", []) or []):
        if i < len(crumbs):
            fm[key] = crumbs[i]
    fm["category"] = " / ".join(cats)
    fm["updatedAt"] = meta.get("updatedAt", "")
    fm["fetchedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    order = list(dict.fromkeys(cfg.frontmatter + ["fetchedAt"]))
    return corpus_mod.render_frontmatter(fm, order)


def assign_unique(target, used, action):
    if target not in used:
        return target
    root, ext = os.path.splitext(target)
    cand, n = f"{root}-{action}{ext}", 2
    while cand in used:
        cand, n = f"{root}-{action}-{n}{ext}", n + 1
    return cand


def run(cfg, force=False, limit=0, only="", jobs=None, dry=False, prune_only=False):
    if not os.path.exists(cfg.toc_file):
        raise SystemExit(f"缺路由清单 {cfg.toc_file}\n先跑：python tools/siteprobe.py routes <站点首页> …")
    toc = json.load(open(cfg.toc_file, encoding="utf-8"))
    cfg.data["prefixCn"] = toc.get("prefixCn") or {}
    pages = toc["pages"]
    if only:
        pages = [p for p in pages if p["path"].startswith(only)]
    if limit:
        pages = pages[:limit]

    manifest = {}
    if os.path.exists(cfg.files_file):
        try:
            for r in json.load(open(cfg.files_file, encoding="utf-8")):
                manifest[r["path"]] = r
        except Exception:  # noqa: BLE001
            pass

    if prune_only:
        keep = {os.path.normpath(os.path.join(cfg.root, r["file"])) for r in manifest.values()}
        stale = []
        for dp, _d, fs in os.walk(cfg.references_dir):
            for fn in fs:
                if fn.endswith(".md") and fn != cfg.index_file_name:
                    p = os.path.normpath(os.path.join(dp, fn))
                    if p not in keep:
                        stale.append(p)
        print(f"磁盘受管文件 {len(keep)} 个，发现 {len(stale)} 个不在清单中：")
        for p in stale[:30]:
            print("   ", os.path.relpath(p, cfg.root))
        if len(stale) > 30:
            print(f"    … 其余 {len(stale) - 30} 个")
        print("\n本次仅列出，不删除。确认后再分小批删（每次 ≤10 个，删完重新核对）。")
        return 0

    todo = [p for p in pages if force or p["path"] not in manifest]
    jobs = jobs or cfg.fetch["concurrency"]
    print(f"清单 {len(pages)} 页；已抓 {len(pages) - len(todo)}；本轮待抓 {len(todo)}（线程 {jobs}）")
    if not todo:
        print("全部已是最新。")
        return 0

    t0 = time.time()
    results = []

    def worker(p):
        time.sleep(cfg.fetch["delay"])
        try:
            html, nbytes = fetch(p["url"], cfg)
        except Exception as e:  # noqa: BLE001
            with _lock:
                _stats["fail"] += 1
                _failures.append({"path": p["path"], "error": f"{type(e).__name__}: {e}"})
            return None
        meta = html2md.extract_page(html, cfg)
        body = html2md.html_to_markdown(meta.pop("contentHtml"), cfg)
        if not body.strip():
            with _lock:
                _stats["fail"] += 1
                _failures.append({"path": p["path"], "error": "正文为空"})
            return None
        with _lock:
            _stats["bytes"] += nbytes
        return (p, meta, body)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(worker, p) for p in todo]
        for done, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            if r:
                results.append(r)
            if done % 100 == 0 or done == len(todo):
                print(f"  抓取 {done}/{len(todo)}  失败 {_stats['fail']}  "
                      f"{_stats['bytes'] / 1e6:.0f}MB  {time.time() - t0:.0f}s")

    used = {os.path.normpath(os.path.join(cfg.root, r["file"])) for r in manifest.values()}
    results.sort(key=lambda x: x[0]["path"])
    for page, meta, body in results:
        target, cats, title = compute_path(cfg, page, meta)
        target = assign_unique(target, used, page["path"].rsplit("/", 1)[-1])
        used.add(target)
        rec = {"path": page["path"], "file": os.path.relpath(target, cfg.root).replace("\\", "/"),
               "title": title, "category": "/".join(cats)}
        manifest[page["path"]] = rec
        if dry:
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fm = build_frontmatter(cfg, page, meta, cats, title)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"{fm}\n\n# {title}\n\n{body}")
        _stats["ok"] += 1

    if not dry:
        os.makedirs(os.path.dirname(cfg.files_file), exist_ok=True)
        with open(cfg.files_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(sorted(manifest.values(), key=lambda x: x["path"]),
                      f, ensure_ascii=False, indent=1)

    print(f"\n完成：写入 {_stats['ok']}，失败 {_stats['fail']}，"
          f"下载 {_stats['bytes'] / 1e6:.1f}MB，用时 {time.time() - t0:.0f}s")
    if _failures:
        print(f"失败清单（共 {len(_failures)}，前 20）：")
        for f_ in _failures[:20]:
            print("   ", f_["path"], "->", f_["error"])
        os.makedirs(cfg.source_dir, exist_ok=True)
        with open(os.path.join(cfg.source_dir, "_failures.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(_failures, f, ensure_ascii=False, indent=1)
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="fetch_manual.py")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.root, args.config)
    return run(cfg, force=args.force, limit=args.limit, only=args.only,
               jobs=args.jobs or None, dry=args.dry_run, prune_only=args.prune)


if __name__ == "__main__":
    sys.exit(main())
