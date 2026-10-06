# -*- coding: utf-8 -*-
"""完整性自检

薄封装：真正的实现在 kbkit/selftest.py。参数说明见 `--help`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbkit import selftest  # noqa: E402

if __name__ == "__main__":
    sys.exit(selftest.main())
