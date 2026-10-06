# -*- coding: utf-8 -*-
"""统一全文检索（读 references/ 下的 Markdown）

薄封装：真正的实现在 kbkit/search.py。参数说明见 `--help`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbkit import search  # noqa: E402

if __name__ == "__main__":
    sys.exit(search.main())
