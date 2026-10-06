# -*- coding: utf-8 -*-
"""站点侦察：判 SPA/SSG、提取路由清单

薄封装：真正的实现在 kbkit/probe.py。参数说明见 `--help`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbkit import probe  # noqa: E402

if __name__ == "__main__":
    sys.exit(probe.main())
