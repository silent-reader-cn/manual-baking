# -*- coding: utf-8 -*-
"""
完整性自检：回答两个问题 —— 抓取有没有丢内容、检索会不会静默截断。

检查项（缺少前置数据的项会自动 SKIP，不会误报失败）：
  A1  路由清单 / 抓取清单 / 磁盘记录 三方条数一致
  A2  文本保真（抽样联网）：源页正文的每段可见文本都能在 Markdown 里找到
  A3  结构守恒（抽样联网）：table / pre 数量一致
  A4  Markdown 里不残留未转换的 HTML 块级标签（代码块内的除外）
  A5  每条记录都有 frontmatter 且字段齐全
  B1  --path 输出完整正文
  B2  非 --full 截断时必须打印明确提示
  B3  --limit 生效，且「总命中数」口径正确
  B4a 索引内链接全部可达 / B4b 叶子目录索引条数与实际一致
  B5  正文深处关键词可召回（用**全库唯一**的探针，否则测的是排序不是召回）

⚠️ 自检报错时**先怀疑检查器**：实测五轮报错里四轮是检查器自身的 bug
（归一化不对称、按子串计数、正则截断带括号的文件名、探针抽到跨页样板句）。

CLI：
    python tools/selftest.py                 # 离线检查
    python tools/selftest.py --online 30     # 再抽样 30 页联网核对
"""
import argparse
import gzip
import html as _html
import json
import os
import random
import re
import subprocess
import sys
import urllib.request

from . import config as config_mod
from . import corpus as corpus_mod
from . import html2md

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def skip(name, why):
    print(f"[SKIP] {name}  ({why})")


def norm(s):
    """两侧统一的归一化：只保留字母数字与汉字。**两侧必须用同一套**。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s or "")


def fetch(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": "kbkit-selftest/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- A 组

def check_a(cfg, records, online):
    print("=" * 70)
    print("A 组：抓取完整性")
    print("=" * 70)

    toc = json.load(open(cfg.toc_file, encoding="utf-8")) if os.path.exists(cfg.toc_file) else None
    manifest = (json.load(open(cfg.files_file, encoding="utf-8"))
                if os.path.exists(cfg.files_file) else None)

    if toc and manifest:
        n_toc = toc.get("totalPages") or len(toc.get("pages") or [])
        n_man = len(manifest)
        n_disk = len({r.rel for r in records})
        check("A1 三方条数一致", n_toc == n_man == n_disk,
              f"toc={n_toc} 清单={n_man} 磁盘文件={n_disk}")
    else:
        skip("A1 三方条数一致", "缺 _toc.json 或 _files.json")

    # A5：查「每条记录都能拿到必需字段」，用 Record 的解析结果而非 raw frontmatter
    #     （title/category 有文件名、目录等兜底，不该因为没写 frontmatter 就判失败）
    need = list(cfg.frontmatter)

    def effective(rec):
        d = dict(rec.meta)
        d.setdefault("title", rec.title)
        d.setdefault("category", rec.category)
        d.setdefault("path", rec.path)
        return d

    bad = [r.rel for r in records
           if any(not effective(r).get(f) for f in need)]
    check("A5 必需字段齐全（含兜底）", not bad,
          f"缺字段 {len(bad)} 条" + (f"，如 {bad[0]}" if bad else ""))

    block_tag = re.compile(
        r"</?(div|span|table|thead|tbody|tr|td|th|ul|ol|li|pre|code|h[1-6]|p|a|img|strong|em)\b[^>]*>",
        re.I)
    residual = [(r.rel, block_tag.findall(corpus_mod.strip_fences(r.body))[:3])
                for r in records if block_tag.search(corpus_mod.strip_fences(r.body))]
    check("A4 无残留 HTML 块级标签（不含代码块）", not residual,
          f"{len(residual)} 条有残留" + (f"，如 {residual[0]}" if residual else ""))

    if online <= 0:
        skip("A2/A3 抽样联网", "加 --online N 开启")
        return

    # 只抽样有 url 的记录，且按文件去重（heading 粒度会重复）
    pool, seen = [], set()
    for r in records:
        if r.meta.get("url") and r.rel not in seen:
            seen.add(r.rel)
            pool.append(r)
    sample = random.Random(20261006).sample(pool, min(online, len(pool)))
    print(f"      抽样 {len(sample)} 页联网核对 …")

    text_miss, struct_bad, fetch_fail = [], [], 0
    for rec in sample:
        try:
            page = html2md.extract_page(fetch(rec.meta["url"]), cfg)
        except Exception as e:  # noqa: BLE001
            fetch_fail += 1
            text_miss.append((rec.rel, f"抓取失败 {type(e).__name__}"))
            continue
        src = page["contentHtml"]
        nmd = norm(rec.body)
        for c in re.findall(r">([^<>]+)<", src):
            nc = norm(_html.unescape(c))   # 源侧是原始 HTML，必须解码；md 侧已解码，不能再解
            if len(nc) >= 3 and nc not in nmd:
                text_miss.append((rec.rel, c.strip()[:60]))
                break
        st = (src.count("<table"), src.count("<pre"))
        md = (len(re.findall(r"^\s*\|\s*-{3,}", rec.body, re.M)),
              len(re.findall(r"^\s*```", rec.body, re.M)) // 2)
        if st[0] != md[0] or st[1] != md[1]:
            struct_bad.append((rec.rel, f"table {st[0]}/{md[0]} pre {st[1]}/{md[1]}"))
    check("A2 文本保真（抽样）", not text_miss,
          f"{len(sample) - len(text_miss)}/{len(sample)} 通过"
          + (f"；缺失如 {text_miss[0]}" if text_miss else ""))
    check("A3 结构守恒（抽样）", not struct_bad,
          f"{len(struct_bad)} 条不一致" + (f"，如 {struct_bad[0]}" if struct_bad else ""))


# ---------------------------------------------------------------- B 组

def _run_search(cfg, args_):
    cmd = [sys.executable, "-m", "kbkit.search", "--root", cfg.root] + args_
    env = dict(os.environ)
    env["PYTHONPATH"] = cfg.root + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.stdout or ""


def check_b(cfg, records):
    from . import search as search_mod
    print()
    print("=" * 70)
    print("B 组：检索行为")
    print("=" * 70)

    # B1
    #   用「唯一标识」去取，别用文件路径 —— heading 粒度下一个文件有多条记录，
    #   --path <文件rel> 只会返回该文件的第一条，和抽样目标对不上（实测踩过）。
    from collections import Counter
    key_of = lambda r: r.path or r.title          # noqa: E731
    key_freq = Counter(key_of(r) for r in records)
    uniq = [r for r in records if key_of(r) and key_freq[key_of(r)] == 1]
    target = max(uniq or records, key=lambda r: len(r.body))
    out = _run_search(cfg, ["--path", key_of(target)])
    body_lines = [ln for ln in target.body.split("\n") if ln.strip()]
    missing = [ln for ln in body_lines if ln.strip() not in out]
    check("B1 --path 输出完整正文", not missing,
          f"{len(body_lines) - len(missing)}/{len(body_lines)} 行命中"
          + (f"，缺 {missing[0][:50]}" if missing else ""))

    # B2/B3
    kw = "的"
    trunc = _run_search(cfg, [kw, "--limit", "1", "--max", "200"])
    has_notice = "后略" in trunc or "前略" in trunc
    check("B2 截断时有明确提示", has_notice or "命中 0 条" in trunc,
          "提示已打印" if has_notice else "该条未截断（正文短于上限）")

    a = _run_search(cfg, [kw, "--limit", "5", "--brief"])
    b = _run_search(cfg, [kw, "--limit", "1", "--brief"])
    ma = re.search(r"命中 (\d+) 条，显示前 (\d+) 条", a)
    mb = re.search(r"命中 (\d+) 条，显示前 (\d+) 条", b)
    ok = bool(ma and mb) and ma.group(1) == mb.group(1) and mb.group(2) == "1"
    check("B3 --limit 生效且总命中数口径一致", ok,
          f"{ma.groups() if ma else None} vs {mb.groups() if mb else None}")

    # B4
    #   B4a 对所有 _INDEX.md 都查（链接可达是通用要求）；
    #   B4b 只查**本工具生成**的索引 —— 有些语料的 _INDEX.md 是内容清单
    #   （kingdee 的模块索引列 228 张表跨 72 个文件），按文件数比会误报。
    broken, leaf_mism = [], []
    checked_b4b = 0
    for dirpath, _d, files in os.walk(cfg.references_dir):
        if cfg.index_file_name not in files:
            continue
        idx = os.path.join(dirpath, cfg.index_file_name)
        text = open(idx, encoding="utf-8").read()
        # 非贪婪 + [^#]：文件名可能带括号，写成 [^)#]+ 会在第一个 ')' 处截断
        for link in re.findall(r"\]\(\./([^#]+?\.md)\)", text):
            if not os.path.exists(os.path.join(dirpath, link)):
                broken.append((os.path.relpath(idx, cfg.root), link))
        if cfg.index_banner not in text:
            continue          # 不是本工具生成的索引，不比对条数
        actual = [f for f in files if f.endswith(".md") and f != cfg.index_file_name]
        if not actual:
            continue          # 非叶子目录，条目指向子索引
        listed = [l for l in re.findall(r"\]\(\./([^#]+?\.md)\)", text)
                  if not l.endswith(cfg.index_file_name)]
        checked_b4b += 1
        if len(listed) != len(actual):
            leaf_mism.append((os.path.relpath(idx, cfg.root), len(listed), len(actual)))
    check("B4a 索引内链接全部可达", not broken,
          f"{len(broken)} 条死链" + (f"，如 {broken[0]}" if broken else ""))
    check("B4b 叶子目录索引条数一致", not leaf_mism,
          f"检查了 {checked_b4b} 个本工具生成的索引，"
          f"{len(leaf_mism)} 处不一致" + (f"，如 {leaf_mism[0]}" if leaf_mism else ""))

    # B5 用全库唯一的行做探针
    #   ⚠️ 探针必须**按记录**归集，不能按文件 —— heading 粒度下一个文件有多条记录，
    #      按文件归集会互相覆盖，导致探针取自别的记录（实测踩过）
    freq = Counter()
    per_rec = {}
    for i, r in enumerate(records):
        ls = [ln for ln in corpus_mod.lines_outside_fence(r.body)
              if len(ln) >= 12 and not ln.startswith(("#", "|", ">", "*", "!"))]
        per_rec[i] = ls
        for ln in set(ls):
            freq[ln] += 1

    ok_n = try_n = 0
    fails = []
    picks = random.Random(20261006).sample(range(len(records)), min(15, len(records)))
    for i in picks:
        rec = records[i]
        cands = [ln for ln in per_rec.get(i, []) if freq[ln] == 1]
        if not cands:
            continue
        try_n += 1
        probe = cands[-1][:16]
        out = _run_search(cfg, [probe, "--limit", "400", "--brief"])
        if rec.title and rec.title in out:
            ok_n += 1
        else:
            fails.append((rec.rel, probe))
    check("B5 正文深处关键词可召回（全库唯一探针）",
          try_n > 0 and ok_n >= try_n * 0.8,
          f"{ok_n}/{try_n} 命中" + (f"，如 {fails[0]}" if fails else ""))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--online", type=int, default=0)
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.root, args.config)
    print(f"自检对象：{cfg.title}  ({cfg.root})\n")
    records = list(corpus_mod.iter_records(cfg))
    if not records:
        print("语料为空，先跑抓取。")
        return 1

    check_a(cfg, records, args.online)
    check_b(cfg, records)

    print()
    fails = [n for n, ok, _d in RESULTS if not ok]
    print("=" * 70)
    if fails:
        print(f"未通过 {len(fails)}/{len(RESULTS)} 项：")
        for n in fails:
            print("   -", n)
        return 1
    print(f"全部 {len(RESULTS)} 项通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
