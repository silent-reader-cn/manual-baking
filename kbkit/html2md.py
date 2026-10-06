# -*- coding: utf-8 -*-
"""
通用 HTML → Markdown 转换器（零依赖，只用 html.parser）。

从三个真实知识库的实现里收敛而来，把站点相关的东西全部参数化：
  - 丢弃哪些 class 的节点（DROP_CLASS_TOKENS 必须**按 token 精确比对**，
    用子串匹配会误伤正文容器 —— 实测把整篇正文丢光）
  - 代码块容器的识别正则（shiki 的 `language-` 后面**可以为空**，正则必须用 `*`）
  - 页面元数据的抽取模式（标题 / 面包屑 / 更新时间）

用法：
    from kbkit import html2md
    meta = html2md.extract_page(html, cfg)
    md   = html2md.html_to_markdown(meta["contentHtml"], cfg)
"""
import html as _html
import re
from html.parser import HTMLParser

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "footer", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead",
    "tbody", "tfoot", "tr", "td", "th", "pre", "blockquote", "hr", "figure",
    "figcaption", "dl", "dt", "dd", "details", "summary", "form",
}

INLINE_TAGS = {"span", "a", "strong", "b", "em", "i", "code", "img", "br",
               "del", "s", "small", "sub", "sup", "u", "mark", "time", "abbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs=None, text=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = text

    def cls(self):
        return self.attrs.get("class", "")

    def get(self, key, default=""):
        return self.attrs.get(key, default)


class _DomBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def _cur(self):
        return self.stack[-1]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, parent=self._cur())
        self._cur().children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, parent=self._cur())
        self._cur().children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self._cur().children.append(Node("#text", text=data, parent=self._cur()))

    def handle_comment(self, data):
        pass  # Vue 的 <!--[--> 之类一律忽略


def build_dom(fragment):
    b = _DomBuilder()
    b.feed(fragment)
    b.close()
    return b.root


def _norm_ws(s):
    return re.sub(r"[ \t\r\n\u00a0]+", " ", s)


def _text_of(node):
    parts = []
    for ch in node.children:
        parts.append(ch.text if ch.tag == "#text" else _text_of(ch))
    return "".join(parts)


def _iter(node):
    for ch in node.children:
        yield ch
        yield from _iter(ch)


class _Ctx:
    """渲染上下文：把配置传进递归。"""

    def __init__(self, cfg=None):
        self.drop_tokens = set(getattr(cfg, "drop_class_tokens", ()) or ())
        self.code_pat = re.compile(
            getattr(cfg, "html", {}).get("codeWrapperPattern", r"\blanguage-([A-Za-z0-9_+#-]*)")
            if cfg else r"\blanguage-([A-Za-z0-9_+#-]*)")
        self.origin = (cfg.source if cfg else "") or ""
        self.link_base = self.origin


CTX = _Ctx()


def _drop(node, ctx):
    cls = node.cls()
    if not cls:
        return False
    return bool(ctx.drop_tokens.intersection(cls.split()))


def _abs_url(url, ctx):
    if not url:
        return url
    if url.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return url
    if url.startswith("/") and ctx.origin:
        return ctx.origin.rstrip("/") + url
    return url


# ---------------------------------------------------------------- 代码块

def _code_lang(node, ctx):
    """
    判断是否代码块容器，返回语言名（可为空串）。
    语言名为空的写法（`class="language- line-numbers-mode"`）必须支持，
    否则围栏会整块丢失 —— 这是实测踩过的坑。
    """
    if node.tag not in ("div", "pre"):
        return None
    cls = node.cls()
    if cls:
        m = ctx.code_pat.search(cls)
        if m:
            return m.group(1)
    if node.get("data-highlighter") == "shiki":
        return ""
    return None


def _render_code(node, lang):
    code_el = next((el for el in _iter(node) if el.tag == "code"), None) or node
    lines = [c for c in code_el.children
             if c.tag == "span" and "line" in c.cls().split()]
    text = "\n".join(_text_of(ln) for ln in lines) if lines else _text_of(code_el)
    text = text.replace("\u00a0", " ").rstrip("\n")
    fence = "```"
    m = re.findall(r"`{3,}", text)
    if m:
        fence = "`" * (max(len(x) for x in m) + 1)
    return f"{fence}{lang}\n{text}\n{fence}\n"


# ---------------------------------------------------------------- 行内

def render_inline(node, ctx):
    if node.tag == "#text":
        return _norm_ws(node.text)
    out = []
    for ch in node.children:
        if ch.tag == "#text":
            out.append(_norm_ws(ch.text))
        elif ch.tag in ("strong", "b"):
            out.append("**" + render_inline(ch, ctx).strip() + "**")
        elif ch.tag in ("em", "i"):
            out.append("*" + render_inline(ch, ctx).strip() + "*")
        elif ch.tag in ("del", "s", "strike"):
            out.append("~~" + render_inline(ch, ctx).strip() + "~~")
        elif ch.tag == "code":
            txt = _text_of(ch)
            out.append("`" + txt + "`" if txt else "")
        elif ch.tag == "br":
            out.append("<br>")
        elif ch.tag == "a":
            if "header-anchor" in ch.cls().split():
                out.append(render_inline(ch, ctx))   # 标题锚点：留文字去链接
                continue
            if _drop(ch, ctx):
                continue
            txt = render_inline(ch, ctx).strip()
            href = _abs_url(ch.get("href"), ctx)
            if txt:
                out.append(f"[{txt}]({href})" if href else txt)
        elif ch.tag == "img":
            alt = ch.get("alt") or ch.get("title") or "image"
            src = _abs_url(ch.get("src") or ch.get("data-src"), ctx)
            out.append(f"![{alt}]({src})" if src else "")
        else:
            out.append(render_inline(ch, ctx))
    return re.sub(r"[ \t]{2,}", " ", "".join(out))


# ---------------------------------------------------------------- 列表 / 表格

def render_list(node, ctx, indent=0, ordered=False):
    lines = []
    idx = 0
    for li in node.children:
        if li.tag != "li":
            continue
        idx += 1
        inline_parts, sub_blocks = [], []
        for ch in li.children:
            if ch.tag in ("ul", "ol"):
                sub_blocks.append(render_list(ch, ctx, indent + 1, ch.tag == "ol").strip())
            elif ch.tag == "#text":
                t = _norm_ws(ch.text)
                if t.strip():
                    inline_parts.append(t.strip())
            elif ch.tag in INLINE_TAGS:
                t = render_inline(ch, ctx)
                if t.strip():
                    inline_parts.append(t.strip())
            else:
                # p / div / blockquote / pre / 代码块容器 → 作为子块缩进挂上
                b = render_block(ch, ctx, indent + 1).strip()
                if b:
                    sub_blocks.append(b)
        head = " ".join(p for p in inline_parts if p).strip()
        if not head and not sub_blocks:
            idx -= 1
            continue
        pad = "  " * indent
        lines.append(f"{pad}{'-' if not ordered else str(idx) + '.'} {head}".rstrip())
        for sb in sub_blocks:
            for ln in sb.split("\n"):
                lines.append(ln if ln.startswith("  " * (indent + 1))
                             else f"{'  ' * (indent + 1)}{ln}")
    return "\n".join(lines) + "\n"


def render_table(node, ctx):
    rows = []
    for tr in _iter(node):
        if tr.tag != "tr":
            continue
        cells = [c for c in tr.children if c.tag in ("td", "th")]
        if not cells:
            continue
        is_head = any(c.tag == "th" for c in cells)
        vals = [render_inline(c, ctx).strip().replace("\n", "<br>").replace("|", "\\|")
                for c in cells]
        rows.append((is_head, vals))
    if not rows:
        return ""
    ncol = max(len(v) for _, v in rows)
    for _, v in rows:
        v.extend([""] * (ncol - len(v)))
    head_idx = next((i for i, (h, _) in enumerate(rows) if h), None)
    if head_idx is None:
        header, body = [""] * ncol, [v for _, v in rows]
    else:
        header = rows[head_idx][1]
        body = [v for i, (_, v) in enumerate(rows) if i != head_idx]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(["---"] * ncol) + " |"]
    out += ["| " + " | ".join(v) + " |" for v in body]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 块

def render_block(node, ctx, indent=0):
    if node.tag == "#text":
        return _norm_ws(node.text)
    if node.tag == "#root":
        return render_children(node, ctx)
    if _drop(node, ctx):
        return ""

    lang = _code_lang(node, ctx)
    if lang is not None:
        return _render_code(node, lang)

    tag = node.tag
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        txt = render_inline(node, ctx).strip()
        return f"\n{'#' * int(tag[1])} {txt}\n\n" if txt else ""
    if tag == "p":
        txt = render_inline(node, ctx).strip()
        return f"{txt}\n\n" if txt else ""
    if tag == "hr":
        return "\n---\n\n"
    if tag == "br":
        return "\n"
    if tag == "blockquote":
        inner = render_children(node, ctx).strip()
        return "\n".join("> " + ln if ln else ">" for ln in inner.split("\n")) + "\n\n"
    if tag == "ul":
        return "\n" + render_list(node, ctx, indent, False) + "\n"
    if tag == "ol":
        return "\n" + render_list(node, ctx, indent, True) + "\n"
    if tag == "table":
        return "\n" + render_table(node, ctx) + "\n"
    if tag == "pre":
        return _render_code(node, "")
    if tag == "img":
        alt = node.get("alt") or "image"
        src = _abs_url(node.get("src") or node.get("data-src"), ctx)
        return f"![{alt}]({src})\n\n" if src else ""
    if tag in BLOCK_TAGS:
        return render_children(node, ctx)
    return render_inline(node, ctx)


def render_children(node, ctx):
    out = []
    for ch in node.children:
        if ch.tag == "#text":
            t = _norm_ws(ch.text)
            if t.strip():
                out.append(t)
            continue
        out.append(render_block(ch, ctx, 0))
    return "".join(out)


def normalize_heading_levels(md, target_top=2):
    """把正文最高一级标题提到 target_top（页面标题已占 h1，不调整会出现空档）。"""
    lines = md.split("\n")
    levels = []
    fence = None
    for ln in lines:
        if fence:
            if ln.strip().startswith(fence):
                fence = None
            continue
        m = re.match(r"^\s*(#{1,6}) ", ln)
        if m:
            levels.append(len(m.group(1)))
        else:
            fm = re.match(r"^\s*(`{3,}|~{3,})", ln)
            if fm:
                fence = fm.group(1)
    if not levels:
        return md
    shift = target_top - min(levels)
    if shift == 0:
        return md
    out, fence = [], None
    for ln in lines:
        if fence:
            out.append(ln)
            if ln.strip().startswith(fence):
                fence = None
            continue
        m = re.match(r"^(\s*)(#{1,6}) (.*)$", ln)
        if m:
            lv = max(1, min(6, len(m.group(2)) + shift))
            out.append(f"{m.group(1)}{'#' * lv} {m.group(3)}")
            continue
        fm = re.match(r"^\s*(`{3,}|~{3,})", ln)
        if fm:
            fence = fm.group(1)
        out.append(ln)
    return "\n".join(out)


def html_to_markdown(fragment, cfg=None):
    ctx = _Ctx(cfg) if cfg is not None else CTX
    md = render_children(build_dom(fragment), ctx)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    if cfg is None or getattr(cfg, "html", {}).get("normalizeHeadings", True):
        md = normalize_heading_levels(md)
    return md


# ---------------------------------------------------------------- 页面元数据

def _match_div(text, start):
    """start 指向 '<div'，返回配对 '</div>' 之后的下标。"""
    depth = 0
    k = start
    while k < len(text):
        nxt_open = text.find("<div", k)
        nxt_close = text.find("</div>", k)
        if nxt_close < 0:
            return -1
        if 0 <= nxt_open < nxt_close:
            depth += 1
            k = nxt_open + 4
        else:
            depth -= 1
            k = nxt_close + 6
            if depth == 0:
                return k
    return -1


def extract_page(html, cfg=None):
    """
    从整页 HTML 抽出元数据与正文区 HTML。
    选择器来自配置的 html.* 项，默认对应 VuePress 2 + Plume。
    """
    h = (getattr(cfg, "html", None) or DEFAULTS_HTML)
    meta = {}

    bcm = re.search(h["breadcrumbBlockPattern"], html, re.S)
    crumbs = re.findall(h["breadcrumbNamePattern"], bcm.group(0)) if bcm else []
    root = h.get("stripBreadcrumbRoot", "")
    meta["breadcrumb"] = [c for c in crumbs if c and c != root]

    title = ""
    m = re.search(h["titlePattern"], html, re.S)
    if m:
        title = _html.unescape(re.sub(r"<!--.*?-->", "", m.group(1))).strip()
    meta["title"] = title

    m = re.search(h["datePattern"], html, re.S)
    if m:
        d = re.search(h["dateValuePattern"], m.group(1))
        meta["updatedAt"] = d.group(1) if d else ""
    else:
        meta["updatedAt"] = ""

    marker = h["contentMarker"]
    i = html.find(marker)
    content = ""
    if i > 0:
        start = html.rfind("<div", 0, i)
        end = _match_div(html, start)
        content = html[start:end] if end > 0 else html[start:]
    meta["contentHtml"] = content
    return meta


DEFAULTS_HTML = {
    "contentMarker": "vp-doc plume-content",
    "breadcrumbBlockPattern": r'typeof="BreadcrumbList".*?</ol>',
    "breadcrumbNamePattern": r'<meta property="name" content="([^"]*)"',
    "titlePattern": r'<h1 class="vp-doc-title page-title"[^>]*>(.*?)</h1>',
    "datePattern": r'class="create-time"[^>]*>(.*?)</p>',
    "dateValuePattern": r">\s*(\d{4}-\d{2}-\d{2})\s*<",
    "stripBreadcrumbRoot": "首页",
    "normalizeHeadings": True,
}


def convert_html(html, cfg=None):
    """一步到位：整页 HTML → (meta, markdown)。"""
    meta = extract_page(html, cfg)
    meta["markdown"] = html_to_markdown(meta.pop("contentHtml"), cfg)
    return meta
