# -*- coding: utf-8 -*-
"""从 Markdown 派生各级 _INDEX.md

薄封装：真正的实现在 kbkit/build_index.py。参数说明见 `--help`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbkit import build_index  # noqa: E402

if __name__ == "__main__":
    sys.exit(build_index.main())
