#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 的判据行。实现位于 novel/stages/stage0/checks.py。'''
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage0'


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(
        rule=rule, stage=STAGE, item=item, sev='hard', who_checks='HARD',
        requirement=requirement, owner=STAGE, **kw))


def _info(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(
        rule=rule, stage=STAGE, item=item, sev='info', who_checks='INFO',
        requirement=requirement, owner=STAGE, **kw))


_hard('s0_nonbody_shared', '剔除与残留断言使用同一套非正文判据', 'R-0.1')
_hard('s0_removal_ledger', '每段剔除都有可回到原文的完整账', 'R-0.2')
_hard('s0_approval', 'stage0 已获人工批准才可继续', 'R-0.3b')
_hard('s0_edit_archive', '人工编辑的上一轮已归档', 'R-0.5')
_hard('s0_hashes', '原始正文与清理正文的两个指纹都对得上', 'R-0.6')
_hard('s0_human_preserved', '人工编辑状态没有被机器重跑抹掉', 'R-0.8')
_hard('s0_body_ratio', '清理后正文至少保留六成', 'R-0.9')
_info('s0_warnings', '可疑剔除逐项显式报出', 'R-0.10')
_hard('s0_decode', '原始小说仍能按记录的编码无损解码', 'R-0.11')
_hard('s0_normalization', '只做 BOM 与换行归一化', 'R-0.12')
_hard('s0_dir_roundtrip', '书目录能被同一把寻址尺子解析回来', 'R-0.13')
_hard('s0_source_identity', '原始小说没有被换成另一份正文', 'R-0.14')

REGISTRY.register(Rule(
    rule='s0_correctness', stage=STAGE, item='剔除段在语义上确实不是正文',
    sev='unver', who_checks='HUMAN', requirement='R-0.7',
    note='这一段到底是不是番外没有结构不变量可查，必须由人读原文确认。'))
