#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage1 的判据行。实现位于 novel/stages/stage1/checks.py。'''
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage1'


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(
        rule=rule, stage=STAGE, item=item, sev='hard', who_checks='HARD',
        requirement=requirement, owner=STAGE, **kw))


def _info(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(
        rule=rule, stage=STAGE, item=item, sev='info', who_checks='INFO',
        requirement=requirement, owner=STAGE, **kw))


_hard('s1_input_hash', '分章输入与 stage0 批准的 clean_text 完全相同', 'R-1.3')
_hard('s1_chapter_labels', '修正序连续，原始章号的缺号重号可追溯', 'R-1.4')
_hard('s1_chapter_count', '独立章节计数与章表、区间表一致', 'R-1.5')
_hard('s1_nonbody_residual', '批准文本里没有可识别的非正文残留', 'R-1.7')
_info('s1_warnings', '可疑分章逐项显式报出', 'R-1.9')
_hard('s1_hook', '第一个正文章之前有非空开篇文案', 'R-1.12')
_hard('s1_min_chapters', '正文章数不少于两章', 'R-1.13')
_hard('s1_tail', '正文切点之后为空或只有一条完结标记', 'R-1.14')
