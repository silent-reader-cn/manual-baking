# -*- coding: utf-8 -*-
"""
从 Markdown 派生各级 `_INDEX.md`（总索引 / 块索引 / 分类索引）。

只做一件事：扫 references/ 生成索引。解析复用 corpus，避免各脚本各自解析而漂移。
**索引永远是派生物**，不要手工编辑。

CLI：
    python scripts/build_index.py
"""
import argparse
import os
import sys
from collections import defaultdict

from . import config as config_mod
from . import corpus as corpus_mod

BANNER = "<!-- 由 scripts/build_index.py 从 Markdown 自动生成，请勿手工编辑 -->"


def _rel(from_dir, target_abs):
    return os.path.relpath(target_abs, from_dir).replace("\\", "/")


def collect(cfg):
    """
    返回 {块目录: {分类: [记录]}}。

    分类路径必须用**相对 references/ 的**路径算（Record.category_path），
    用相对仓库根的路径切分会多套一层目录 —— 实测会生成两倍数量的索引文件。
    """
    tree = defaultdict(lambda: defaultdict(list))
    for rec in corpus_mod.iter_records(cfg):
        tree[rec.block][rec.category_path].append(rec)
    return tree


def _dedupe_by_file(records):
    """heading 粒度时同一文件会有多条记录，索引按文件去重。"""
    seen, out = set(), []
    for r in records:
        if r.rel in seen:
            continue
        seen.add(r.rel)
        out.append(r)
    return out


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def build(cfg, quiet=False):
    tree = collect(cfg)
    total = sum(len(v) for cats in tree.values() for v in cats.values())
    n_index = 0

    # ---------- 总索引 ----------
    L = [BANNER, "", f"# {cfg.title} · 总索引", "",
         f"共 **{total}** 条记录，分 **{len(tree)}** 块。"]
    if cfg.source:
        L.append(f"内容来自 {cfg.source}，由 `tools/fetch_manual.py` 批量抓取转换而来。")
    L += ["", "| 块 | 条数 | 分类数 | 入口 |", "| :--- | ---: | ---: | :--- |"]
    for blk in sorted(tree):
        cats = tree[blk]
        n = sum(len(v) for v in cats.values())
        L.append(f"| {cfg.block_name(blk)} | {n} | {len(cats)} | "
                 f"[`{blk}/`](./{blk}/_INDEX.md) |")
    L += ["", "> 全文检索见 `scripts/search.py`。", ""]
    for blk in sorted(tree):
        L += [f"## {cfg.block_name(blk)}", ""]
        for cat in sorted(tree[blk]):
            label = cat or "（未分类）"
            if cat:
                L.append(f"- [{label}](./{blk}/{cat}/_INDEX.md) —— {len(tree[blk][cat])} 条")
            else:
                L.append(f"- {label} —— {len(tree[blk][cat])} 条")
        L.append("")
    write(os.path.join(cfg.references_dir, "_INDEX.md"), L)
    n_index += 1

    # ---------- 块索引 + 分类索引 ----------
    for blk, cats in tree.items():
        blk_dir = os.path.join(cfg.references_dir, blk)
        n = sum(len(v) for v in cats.values())
        L = [BANNER, "", f"# {cfg.block_name(blk)} · 索引", "",
             f"共 {n} 条，{len(cats)} 个分类。", "",
             "| 分类 | 条数 |", "| :--- | ---: |"]
        for cat in sorted(cats):
            label = cat or "（未分类）"
            L.append(f"| [{label}](./{cat}/_INDEX.md) | {len(cats[cat])} |" if cat
                     else f"| {label} | {len(cats[cat])} |")
        L.append("")
        for cat in sorted(cats):
            if not cat:
                continue
            recs = _dedupe_by_file(cats[cat])
            L += [f"## {cat}", "", "| 标题 | 更新时间 | 原文 |", "| :--- | :--- | :--- |"]
            for r in sorted(recs, key=lambda x: x.title):
                url = r.meta.get("url", "")
                link = f"[link]({url})" if url else ""
                L.append(f"| [{r.title}](./{_rel(blk_dir, r.file)}) "
                         f"| {r.meta.get('updatedAt', '')} | {link} |")
            L.append("")
        write(os.path.join(blk_dir, "_INDEX.md"), L)
        n_index += 1

        for cat, recs in cats.items():
            if not cat:
                continue
            cat_dir = os.path.join(cfg.references_dir, blk, cat)
            files = _dedupe_by_file(recs)
            L = [BANNER, "", f"# {cfg.block_name(blk)} / {cat}", "",
                 f"共 {len(recs)} 条记录，{len(files)} 个文件。", "",
                 "| 标题 | 路径 | 更新时间 |", "| :--- | :--- | :--- |"]
            for r in sorted(files, key=lambda x: x.title):
                L.append(f"| [{r.title}](./{_rel(cat_dir, r.file)}) "
                         f"| `{r.path or r.rel}` | {r.meta.get('updatedAt', '')} |")
            L.append("")
            write(os.path.join(cat_dir, "_INDEX.md"), L)
            n_index += 1

    if not quiet:
        print(f"已生成索引：总索引 1 个，块索引 {len(tree)} 个，"
              f"分类索引 {n_index - 1 - len(tree)} 个；共 {total} 条记录。")
    return n_index, total


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="build_index.py")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.root, args.config)
    build(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
