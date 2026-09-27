#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage5 —— STR-06 新链 Stage5：Hook 成文与按章正文。

【本地块】
  · `hook_writer.py`  Hook 成文：同 Context 有限重写＋机械验（`str06.stage5.hook`）。
  · `body.py`        按章正文成文（`str06.stage5.body`）：机械检查＋有限重写＋章级回执。
  · `body_checks.py` 章级机械判定与篇幅口径（validator：只验证并报告）。

Hook 设计与唯一 graph publisher、HookContext 编译已按节点所属 Stage 归位
Stage4 包（B3 后半：`novel/stages/stage4/hook.py`／`hook_context.py`）；
本包只保留 Stage5 的语言实现面。

【包级公开面】跨 stage 只许 `from novel.stages.stage5 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
Stage7 装配经本公开面消费 `count_body_chars`／`prior_body_text`／`sha256_text`。
"""
from __future__ import annotations

from novel.stages.stage5.body import (
    AnsweredText, ChapterAttempt, ChapterOutcome, book_ir_sha256,
    prior_body_text, sha256_text, write_chapter,
)
from novel.stages.stage5.body_checks import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, MAX_BODY_REWRITES,
    chapter_surface_releases, chapter_withheld_literals, count_body_chars,
    validate_chapter_prose,
)
from novel.stages.stage5.hook_writer import (
    HOOK_VALIDATOR_VERSION, MAX_REWRITES, MECHANICAL_CHECKS,
    build_hook_user_prompt, build_rewrite_user_prompt, context_sha256,
    validate_hook_prose,
)

__all__ = [
    'AnsweredText', 'BODY_MECHANICAL_CHECKS', 'BODY_VALIDATOR_VERSION',
    'ChapterAttempt', 'ChapterOutcome', 'HOOK_VALIDATOR_VERSION', 'MAX_BODY_REWRITES',
    'MAX_REWRITES', 'MECHANICAL_CHECKS',
    'book_ir_sha256', 'build_hook_user_prompt', 'build_rewrite_user_prompt',
    'chapter_surface_releases', 'chapter_withheld_literals',
    'context_sha256', 'count_body_chars', 'prior_body_text',
    'sha256_text', 'validate_chapter_prose', 'validate_hook_prose', 'write_chapter',
]
