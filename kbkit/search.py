# -*- coding: utf-8 -*-
"""
通用全文检索。

直接读 Markdown（不依赖预生成索引，因此不存在「改了文档忘了重建索引就搜不到」的漂移），
所有站点差异由 knowledge.json 驱动。

排序是分层加权的，核心两点（都是实测踩出来的）：
  1. **path 必须参与检索与打分** —— 对象页标题是中文，而使用者最自然的查法是敲
     标识符（apiName / 表名）。漏掉 path 会导致按标识符查**静默漏召回**。
  2. 精确名（标题完全相等）必须压过「正文里被大量引用」的长文。

截断**围绕命中位置**，绝不从头截断；只要截断了就一定打印提示，不静默丢内容。

CLI：
    python scripts/search.py <关键词...> [选项]
"""
import argparse
import os
import re
import sys

from . import config as config_mod
from . import corpus as corpus_mod


# ---------------------------------------------------------------- 打分

def score(rec, kws, require_all, w):
    """
    返回 (分数, 命中词数, 各词命中次数)；不命中返回 None。
    """
    title = rec.title or ""
    cat = rec.category or ""
    path = rec.path or ""
    segs = set(path.strip("/").split("/"))

    counts = []
    for kw in kws:
        c = rec.body.count(kw) + title.count(kw) * w["titleHitWeight"]
        if kw in path:
            c += 1
        counts.append(c)
    if require_all and any(c == 0 for c in counts):
        return None
    if not any(counts):
        return None

    s = 0
    for kw in kws:
        if title == kw:
            s += w["titleExact"]
        elif title.startswith(kw):
            s += w["titlePrefix"]
        elif kw in title:
            s += w["titleContains"]
        if kw in cat:
            s += w["categoryContains"]
        if kw in segs:
            s += w["pathSegmentExact"]
        elif kw in path:
            s += w["pathContains"]
    s += sum(counts) * w["bodyPerHit"]
    return s, sum(1 for c in counts if c), counts


def make_snippet(body, kws, width, lead_ratio=0.33):
    """围绕首次命中位置取窗口；截断了就显式提示。"""
    pos = -1
    for kw in kws:
        p = body.find(kw)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return body[:width] + ("\n…(后略)" if len(body) > width else "")
    start = max(0, pos - int(width * lead_ratio))
    end = min(len(body), start + width)
    start = max(0, end - width)
    prefix = "…(前略) " if start > 0 else ""
    suffix = "\n…(后略，完整正文请加 --full)" if end < len(body) else ""
    return prefix + body[start:end] + suffix


# ---------------------------------------------------------------- 检索

def run(cfg, keywords, scopes=None, category="", require_all=False,
        limit=None, exact_name=False):
    """返回按分数排序的 [(score, rec)]。"""
    w = cfg.ranking
    kws = [k for k in keywords if k]
    out = []
    for rec in corpus_mod.iter_records(cfg, scopes=scopes, category=category):
        if not kws:
            out.append((0, rec))
            continue
        if exact_name and rec.title.strip().lower() != kws[0].strip().lower():
            continue
        head = rec.searchable_head(cfg)
        hay = rec.body + "\n" + head
        if not any(k in hay for k in kws):     # 文件级预筛
            continue
        sc = score(rec, kws, require_all, w)
        if sc is None:
            continue
        out.append((sc[0], rec))
    if kws:
        out.sort(key=lambda x: (-x[0], x[1].title))
    else:
        out.sort(key=lambda x: (x[1].category, x[1].title))
    return out


def print_results(cfg, results, kws, brief=False, full=False, list_only=False,
                  limit=None, max_chars=None):
    limit = limit if limit is not None else cfg.search["defaultLimit"]
    max_chars = max_chars if max_chars is not None else cfg.search["defaultMax"]
    shown = results[:limit]
    print(f"命中 {len(results)} 条，显示前 {len(shown)} 条"
          f"{'（--list）' if list_only else ''}\n")
    for i, (_s, rec) in enumerate(shown, 1):
        if brief or list_only:
            print(f"{i:>3}. [{rec.alias}] {rec.title}   | {rec.category} | "
                  f"{rec.path or rec.rel}")
            continue
        print("=" * 78)
        print(f"{i}. 【{rec.title}】  ({rec.alias} / {rec.category})")
        if rec.path:
            print(f"   path: {rec.path}")
        if rec.meta.get("url"):
            print(f"   原文: {rec.meta['url']}")
        print(f"   文件: {rec.rel}")
        print()
        if full:
            print(rec.body.strip())
        else:
            print(make_snippet(rec.body, kws, max_chars, cfg.search["snippetLeadRatio"]))
        print()
    if len(results) > len(shown):
        print(f"…还有 {len(results) - len(shown)} 条未显示，用 --limit 调整")


# ---------------------------------------------------------------- CLI

def build_parser(cfg=None):
    ap = argparse.ArgumentParser(prog="search.py", add_help=True)
    ap.add_argument("keywords", nargs="*")
    alias = ", ".join(sorted(cfg.alias_to_dir)) if cfg else "all"
    ap.add_argument("--scope", default="all", help=f"检索范围：{alias}（逗号可组合），默认 all")
    ap.add_argument("--category", default="", help="按分类子串过滤")
    ap.add_argument("--brief", action="store_true", help="每条只输出一行")
    ap.add_argument("--full", "-d", action="store_true", help="输出完整正文（不截断）")
    ap.add_argument("--all", action="store_true", help="多个关键词必须全部命中")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max", type=int, default=None, help="非 --full 时每条正文上限")
    ap.add_argument("--list", action="store_true", help="只列条目")
    ap.add_argument("--path", default="", help="按 path 或标题精确取一篇")
    ap.add_argument("--exact", action="store_true",
                    help="关键词按标题精确匹配（适合按表名/接口名取定义）")
    ap.add_argument("--root", default=None, help="知识库根目录（默认自动向上查找）")
    ap.add_argument("--config", default=None)
    return ap


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    argv = list(sys.argv[1:] if argv is None else argv)

    # --help 不该依赖 knowledge.json（在没有配置的目录里也要能看帮助）
    if "-h" in argv or "--help" in argv:
        build_parser(None).print_help()
        return 0

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--root", default=None)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args(argv)
    cfg = config_mod.load(known.root, known.config)

    ap = build_parser(cfg)
    args = ap.parse_args(argv)

    try:
        scopes = cfg.resolve_scopes(args.scope)
    except ValueError as e:
        print(e)
        return 2

    # 精确取一篇
    if args.path:
        want = args.path.strip()
        for rec in corpus_mod.iter_records(cfg, scopes, args.category):
            if rec.path == want or rec.title == want or rec.rel == want:
                print(f"# {rec.title}")
                if rec.path:
                    print(f"path: {rec.path}")
                print(f"分类: {rec.category}   块: {cfg.block_name(rec.block)}")
                if rec.meta.get("url"):
                    print(f"原文: {rec.meta['url']}")
                print(f"文件: {rec.rel}\n")
                print(rec.body.strip())
                return 0
        print(f"未找到：{want}")
        return 1

    kws = [k for k in args.keywords if k]
    if not kws and not args.list:
        ap.print_help()
        return 2

    results = run(cfg, kws, scopes, args.category, args.all,
                  limit=args.limit, exact_name=args.exact)
    print_results(cfg, results, kws, brief=args.brief, full=args.full,
                  list_only=args.list, limit=args.limit, max_chars=args.max)
    return 0


if __name__ == "__main__":
    sys.exit(main())
