# -*- coding: utf-8 -*-
"""
语料层：遍历 references/ 下的 Markdown，解析 frontmatter，按配置切分「记录」。

**所有模块都从这里取数据** —— search / build_index / selftest 共用一套解析，
避免三个脚本各自实现 frontmatter 解析然后慢慢漂移（这是历史上真实踩过的坑）。

两种记录粒度：
  page    一条记录 = 一篇文章（文档型内容，默认）
  heading 一条记录 = 一个标题块（表格型内容，如「一张表 = 一个 ## 块」）
"""
import os
import re

FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.S)
KV_RE = re.compile(r'^([A-Za-z_][\w-]*):\s*"(.*)"\s*$')


def parse_frontmatter(text):
    """拆出 (frontmatter dict, 正文)。没有 frontmatter 时 meta 为空、正文原样返回。"""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).split("\n"):
        kv = KV_RE.match(line.rstrip("\r"))
        if kv:
            meta[kv.group(1)] = kv.group(2)
    return meta, text[m.end():]


def render_frontmatter(meta, order=None):
    """按给定顺序生成 frontmatter 文本（含首尾 ---）。"""
    keys = [k for k in (order or []) if k in meta]
    keys += [k for k in meta if k not in keys]
    lines = ["---"]
    for k in keys:
        v = str(meta[k]).replace('"', '\\"')
        lines.append(f'{k}: "{v}"')
    lines.append("---")
    return "\n".join(lines)


class Record:
    """一条可检索记录。"""

    __slots__ = ("meta", "body", "file", "rel", "rel_refs", "block", "alias", "heading")

    def __init__(self, meta, body, file, rel, block, alias, heading=None, rel_refs=None):
        self.meta = meta
        self.body = body
        self.file = file
        self.rel = rel              # 相对仓库根，正斜杠
        self.rel_refs = rel_refs    # 相对 references/，正斜杠
        self.block = block          # 块目录名
        self.alias = alias          # 块别名
        self.heading = heading      # heading 粒度时的标题文本

    @property
    def category_path(self):
        """
        分类目录（相对 references/，去掉块目录那一级）。

        注意要先取 dirname —— 直接切 rel_refs 会把**文件名**也算进分类，
        导致 makedirs 去创建一个以 .md 结尾的目录（实测 FileExistsError）。
        """
        d = os.path.dirname(self.rel_refs or "").replace("\\", "/")
        return d.split("/", 1)[1] if "/" in d else ""

    @property
    def title(self):
        return self.heading or self.meta.get("title", "")

    @property
    def category(self):
        return self.meta.get("category", "")

    @property
    def path(self):
        return self.meta.get("path", "")

    def searchable_head(self, cfg):
        """标题侧文本（用于预筛与加权）。"""
        parts = [self.title, self.category]
        for f in cfg.frontmatter:
            if f not in ("title", "category"):
                parts.append(self.meta.get(f, ""))
        return "\n".join(p for p in parts if p)


def split_by_heading(body, level):
    """
    按 N 级标题把正文切成块，返回 [(标题文本, 块正文)]。
    标题前的引言会作为「(概述)」块保留，不丢内容。
    """
    pat = re.compile(r"^(#{%d})\s+(.*)$" % level, re.M)
    marks = list(pat.finditer(body))
    if not marks:
        return [(None, body)]
    out = []
    if marks[0].start() > 0 and body[: marks[0].start()].strip():
        out.append((None, body[: marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out.append((m.group(2).strip(), body[m.start():end]))
    return out


def iter_records(cfg, scopes=None, category=None, include_index_files=False):
    """
    遍历语料，产出 Record。

    scopes   块目录名集合（None = 全部）
    category 分类子串过滤（对 meta.category）
    """
    refs = cfg.references_dir
    if not os.path.isdir(refs):
        raise SystemExit(f"语料目录不存在：{refs}\n先跑抓取，或检查 knowledge.json 的 referencesDir")
    for dirpath, dirs, files in os.walk(refs):
        dirs.sort()
        rel_dir = os.path.relpath(dirpath, refs).replace("\\", "/")
        block = cfg.block_dir_of(rel_dir)
        if block is None or (scopes and block not in scopes):
            continue
        alias = cfg.alias_of(block)
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            if fn == cfg.index_file_name and not include_index_files:
                continue
            full = os.path.join(dirpath, fn)
            try:
                text = open(full, encoding="utf-8").read()
            except Exception:  # noqa: BLE001
                continue
            meta, body = parse_frontmatter(text)
            if category and category not in meta.get("category", ""):
                continue
            rel = os.path.relpath(full, cfg.root).replace("\\", "/")
            rel_refs = os.path.relpath(full, refs).replace("\\", "/")
            if cfg.record_granularity == "heading":
                for heading, chunk in split_by_heading(body, cfg.heading_record_level):
                    if not chunk.strip():
                        continue
                    m = dict(meta)
                    if heading:
                        m["title"] = heading
                    yield Record(m, chunk, full, rel, block, alias, heading, rel_refs)
            else:
                yield Record(meta, body, full, rel, block, alias, None, rel_refs)


def corpus_stats(cfg):
    """语料概览：各块记录数 + 总记录数。"""
    from collections import Counter
    per_block = Counter()
    for r in iter_records(cfg):
        per_block[r.block] += 1
    return per_block, sum(per_block.values())


def strip_fences(text):
    """去掉围栏代码块（连同围栏行）。"""
    out, fence = [], None
    for ln in text.split("\n"):
        if fence:
            if ln.strip().startswith(fence):
                fence = None
            continue
        m = re.match(r"^\s*(`{3,}|~{3,})", ln)
        if m:
            fence = m.group(1)
            continue
        out.append(ln)
    return "\n".join(out)


def lines_outside_fence(text):
    """围栏之外的有实质内容的行（去首尾空白）。"""
    out, fence = [], None
    for ln in text.split("\n"):
        if fence:
            if ln.strip().startswith(fence):
                fence = None
            continue
        m = re.match(r"^\s*(`{3,}|~{3,})", ln)
        if m:
            fence = m.group(1)
            continue
        if ln.strip():
            out.append(ln.strip())
    return out
