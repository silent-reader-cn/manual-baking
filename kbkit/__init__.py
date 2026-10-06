# -*- coding: utf-8 -*-
"""
kbkit —— 把在线文档手册烘焙成本地可检索知识库的通用工具包。

零依赖（仅 Python 3 标准库），设计为**整包 vendoring 进每个知识库仓库**，
保持各 skill 自包含（不跨仓库 import）。

典型布局（由 `kb_new.py` 生成）：

    <知识库>/
    ├── knowledge.json      ← 唯一需要按站点改的文件
    ├── kbkit/              ← 本工具包
    ├── scripts/search.py   ← CLI 薄封装
    ├── scripts/build_index.py
    ├── tools/fetch_manual.py
    ├── tools/selftest.py
    ├── tools/siteprobe.py
    ├── references/         ← 语料（Markdown 是唯一真相源）
    └── _source/            ← 抓取清单 / 路由 / sidebar 存档

所有模块共用 config 与 corpus，避免各脚本各自解析 frontmatter 而漂移。
"""

__all__ = ["config", "corpus", "html2md", "search", "build_index", "selftest", "probe", "fetch"]

__version__ = "1.0.0"
