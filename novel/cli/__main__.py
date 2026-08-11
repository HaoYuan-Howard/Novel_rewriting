#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`python -m novel.cli …` 的入口。**`build_argv` 拼出来的那条命令跑的就是这里。**"""
from __future__ import annotations

import sys

from novel.cli.main import main

sys.exit(main())
