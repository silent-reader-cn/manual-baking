# -*- coding: utf-8 -*-
"""
配置层：读 `knowledge.json`（支持 `//` 行注释与尾注释），给所有模块提供统一的站点参数。

设计意图：把「因站点而异」的东西全部收进一个文件 —— 块划分、目录别名、
排序权重、HTML 选择器、抓取参数 —— 这样 search / build_index / selftest / fetch
就能保持通用，新知识库只需改配置，不用改代码。
"""
import json
import os
import re

DEFAULTS = {
    "name": "knowledge-base",
    "title": "文档知识库",
    "source": "",
    "siteType": "static-html",          # vuepress-ssg | static-html | spa-json | structured-export
    "referencesDir": "references",
    "sourceDir": "_source",
    "tocFile": "_source/_toc.json",
    "filesFile": "_source/_files.json",
    "indexFileName": "_INDEX.md",

    # 一条记录 = 一篇文章（page）；表格型内容可设为 heading（一条记录 = 一个标题块）
    "recordGranularity": "page",
    "headingRecordLevel": 2,            # recordGranularity=heading 时，按几级标题切块

    "blocks": [
        {"dir": "01_docs", "name": "文档", "alias": "docs", "prefix": "/"}
    ],

    "frontmatter": ["title", "path", "url", "block", "category", "updatedAt"],

    # 分层加权：标题/名称命中远重于正文命中，避免长文淹没精确匹配
    "ranking": {
        "titleExact": 20000,
        "titlePrefix": 5000,
        "titleContains": 2000,
        "categoryContains": 800,
        "pathSegmentExact": 6000,       # 关键词正好命中一个 URL 段（模块/对象/动作）
        "pathContains": 1200,
        "bodyPerHit": 10,
        "titleHitWeight": 3,            # 标题里的命中按 3 倍计入次数
    },

    "search": {
        "defaultLimit": 20,
        "defaultMax": 1500,
        "snippetLeadRatio": 0.33,       # 片段从命中点往前取的比例
    },

    "fetch": {
        "userAgent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "concurrency": 6,
        "delay": 0.1,
        "retries": 3,
        "timeout": 40,
    },

    # 站点 HTML 结构选择器。默认值对应 VuePress 2 + Plume 主题。
    "html": {
        "contentMarker": "vp-doc plume-content",
        "dropClassTokens": [
            "header-anchor", "line-numbers", "line-number", "copy-code-button",
            "vp-copy", "external-link-icon", "outline-marker", "edit-link",
            "last-updated", "prev-next",
        ],
        "codeWrapperPattern": r"\blanguage-([A-Za-z0-9_+#-]*)",
        "breadcrumbBlockPattern": r'typeof="BreadcrumbList".*?</ol>',
        "breadcrumbNamePattern": r'<meta property="name" content="([^"]*)"',
        "titlePattern": r'<h1 class="vp-doc-title page-title"[^>]*>(.*?)</h1>',
        "datePattern": r'class="create-time"[^>]*>(.*?)</p>',
        "dateValuePattern": r">\s*(\d{4}-\d{2}-\d{2})\s*<",
        "stripBreadcrumbRoot": "首页",
        "normalizeHeadings": True,      # 正文最高级标题提到 h2（页面标题已占 h1）
    },
}


def _strip_json_comments(text):
    """
    去掉 // 行注释与尾注释，但**不动字符串内部**（否则 https:// 会被腰斩）。
    也不处理 /* */ —— 保持简单，需要多行说明就写多条 //。
    """
    out = []
    in_str = False
    esc = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _deep_merge(base, override):
    """递归合并：override 覆盖 base，dict 逐层合并，list 整体替换。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data, root):
        self.data = data
        self.root = root
        self.name = data["name"]
        self.title = data["title"]
        self.source = data["source"]
        self.site_type = data["siteType"]
        self.references_dir = os.path.join(root, data["referencesDir"])
        self.source_dir = os.path.join(root, data["sourceDir"])
        self.toc_file = os.path.join(root, data["tocFile"])
        self.files_file = os.path.join(root, data["filesFile"])
        self.index_file_name = data["indexFileName"]
        self.record_granularity = data["recordGranularity"]
        self.heading_record_level = int(data["headingRecordLevel"])
        self.blocks = data["blocks"]
        self.frontmatter = data["frontmatter"]
        self.ranking = data["ranking"]
        self.search = data["search"]
        self.fetch = data["fetch"]
        self.html = data["html"]
        self.drop_class_tokens = set(data["html"]["dropClassTokens"])

    # ---------------------------------------------------------------- 块

    @property
    def dir_to_block(self):
        return {b["dir"]: b for b in self.blocks}

    @property
    def alias_to_dir(self):
        """scope 别名 → 目录名（如 object → 02_object）。"""
        return {b.get("alias", b["dir"]): b["dir"] for b in self.blocks}

    @property
    def dir_to_alias(self):
        return {b["dir"]: b.get("alias", b["dir"]) for b in self.blocks}

    def resolve_scopes(self, scope_arg):
        """
        把 --scope 参数解析成目录名集合。返回 None 表示不过滤。
        支持别名、目录名、以及 'all'。
        """
        if not scope_arg or scope_arg.strip().lower() == "all":
            return None
        alias = self.alias_to_dir
        dirs = set(self.dir_to_block)
        out = set()
        unknown = []
        for s in re.split(r"[,\s]+", scope_arg.strip()):
            if not s:
                continue
            if s in alias:
                out.add(alias[s])
            elif s in dirs:
                out.add(s)
            else:
                unknown.append(s)
        if unknown:
            raise ValueError(
                f"未知 scope：{', '.join(unknown)}；可选 "
                f"{', '.join(sorted(alias))}（或目录名 {'/'.join(sorted(dirs))}）")
        return out or None

    def block_dir_of(self, rel_path):
        """由相对 references 的路径求所属块目录。"""
        top = rel_path.replace("\\", "/").split("/")[0]
        return top if top in self.dir_to_block else None

    def block_name(self, block_dir):
        b = self.dir_to_block.get(block_dir)
        return b["name"] if b else block_dir

    def alias_of(self, block_dir):
        return self.dir_to_alias.get(block_dir, block_dir)


def find_root(start=None):
    """
    向上找含 knowledge.json 的目录。

    起点优先级：显式 start → 当前工作目录 → kbkit 所在目录的上一级。
    先看 cwd 很重要：工具包可能被 vendoring 到别处，而使用者是在自己的知识库里执行。
    """
    candidates = []
    if start:
        candidates.append(os.path.abspath(start))
    candidates.append(os.path.abspath(os.getcwd()))
    candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for c in candidates:
        cur = c
        for _ in range(8):
            if os.path.exists(os.path.join(cur, "knowledge.json")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return os.path.abspath(start or os.getcwd())


def load(root=None, config_file=None):
    """读配置。root 省略时自动向上查找 knowledge.json。"""
    if config_file:
        path = os.path.abspath(config_file)
        root = os.path.dirname(path)
    else:
        root = find_root(root)
        path = os.path.join(root, "knowledge.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"未找到配置文件 {path}\n"
            "请在知识库根目录放一个 knowledge.json（可用 `python tools/kb_new.py` 生成，"
            "或参考 manual-baking/templates/knowledge.json）。")
    raw = open(path, encoding="utf-8").read()
    user = json.loads(_strip_json_comments(raw))
    return Config(_deep_merge(DEFAULTS, user), root)
