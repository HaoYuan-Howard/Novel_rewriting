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
_hard('s0_approval', 'stage0 机械 admission 通过才可继续', 'R-0.3b')
_hard('s0_revision_chain', 'transform 自证，clean_text 是其物化投影', 'R-0.5')
_hard('s0_hashes', '原始正文与清理正文的两个指纹都对得上', 'R-0.6')
_hard('s0_human_preserved', 'v3 禁止人工剔除段进入正式 transform', 'R-0.8')
_hard('s0_body_ratio', '清理后正文至少保留六成', 'R-0.9')
_info('s0_warnings', '可疑剔除逐项显式报出', 'R-0.10')
_hard('s0_decode', '原始小说仍能按记录的编码无损解码', 'R-0.11')
_hard('s0_normalization', '只做 BOM 与换行归一化', 'R-0.12')
_hard('s0_dir_roundtrip', '书目录能被同一把寻址尺子解析回来', 'R-0.13')
_hard('s0_source_identity', '原始小说没有被换成另一份正文', 'R-0.14')

# R-0.7（剔除段语义正确性）原由 `s0_correctness` 承载：sev=unver 的披露判据，
# 2026-09-15 全自动裁决后人工确认入口退役，本维转为 Q-13 内容质量类披露。
# 2026-10-08 裁决删除：剔除决策已由机械判据确定性处理并由上方 hard 判据完整
# 守护（账目/指纹/占比/判据共享），一条永 unver 的规则只会让 stage0 复检恒
# AMBER，把「无事待确认」显示成未知；内容质量披露由 Stage7 交付 manifest 的
# 固定披露文案承担，不再依赖本注册表行。
