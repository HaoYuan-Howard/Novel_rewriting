#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原子事实场景编译器的判据注册表。"""
from __future__ import annotations

from contracts.rules import PromptSurface, REGISTRY, Rule

STAGE = 'stage4'
PLAN_PROMPT_SURFACE = REGISTRY.register_prompt_surface(PromptSurface(
    surface='stage4.plan',
    provides_id_spaces=(
        'chapter_boundaries', 'chapter_ordinals', 'characters', 'fact_atoms',
        'materials', 'relations', 'secrets', 'settings', 'source_stages', 'world_rules',
    ),
))
_HUMAN_FIX = ('人工可改 `.INVALID` 后用 `--revalidate --actor <id> --reason <why>` '
              '0 LLM 重判；writer context 用 `stage4.context` 0 成本重建。')


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=STAGE, **kw))


def _info(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=STAGE, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


for rule, item, req in (
    ('one_to_one', '一章对一章且同号', 'R-5.2'),
    ('ordinal_seq', 'output_ordinal 1..N 连续', 'R-5.1'),
    ('empty_chapter', '每章至少有一个新场景', 'R-5.7'),
    ('fact_ref', '场景事实/舞台引用只来自同章关闭式目录', 'R-5.6'),
    ('fact_coverage', '每个 land 事实恰好落一个场景', 'R-5.12'),
    ('causal_closure', 'Stage2 因果边两端事实都落入实际场景（land 或 context）', 'R-5.6'),
    ('state_closure', '章首/章尾状态闭合；章内只判可判的故事时态边界', 'R-5.6'),
    ('material_coverage', '每条保真材料恰好落一个场景', 'R-5.6'),
    ('scene_transition', '新场景 slot 与 transition 可执行', 'R-5.17'),
    ('goal_landing', '章首/章尾目标落在新首尾场景', 'R-5.6'),
    ('budget_int', '每场预算为正整数', 'R-5.7'),
    ('budget_band', '逐章场景预算落在目标区间', 'R-5.3b'),
    ('plan_book_band', '全书场景预算总量守恒', 'R-5.3c'),
    ('style_words', 'brief/purpose 只说写什么，不用纯写法词代替设计', 'R-5.4'),
    ('phase_artifact_link', 'compiled 计划完整绑定盘上当前 A/B 两份版本化字节', 'R-5.10d'),
    ('context_link', 'writer_context 照的是盘上这份计划', 'R-5.10b'),
    ('ctx_facts_exact', '写手场景事实由 typed refs 精确解析且没有旧骨架键', 'R-6.1'),
    ('ctx_name_attested', '下发人名在本章为止有据', 'R-6.2'),
    ('ctx_stage_explicit', '每个新场景带可执行舞台且不取并集', 'R-6.4'),
    ('ctx_hinge_delivered', 'Stage2 保真材料条条进入写手场景', 'R-3.3b'),
):
    _hard(rule, item, req, note=_HUMAN_FIX)

_info('causal_disclosure', '按首次可见场算的披露逆因果读数', 'R-5.16',
      note='逆序可能是合法倒叙，不设无真值阈值。')
_info('verbatim_starved', '场景预算没有被逐字材料挤满', 'R-5.13')

for rule, item, req in (
    ('scene_budget_distribution', '场景预算相对源舞台的分布', 'R-5.9b'),
    ('verbatim_share', '逐字材料占场景预算的分布', 'R-5.14'),
    ('partition_change', '事实到场景分区相对源舞台分区的变化', 'R-5.15'),
    ('disclosure_order', '严格可比较事实对的披露顺序变化', 'R-5.15'),
    ('noncontiguous_regroup', '跨源区间的真正非连续组场', 'R-5.15'),
    ('anchor_migration', '开场与结尾锚点及首尾场 payload 迁移', 'R-5.18'),
    ('structure_eligibility', '逐章可测量与保守可重排机会分母', 'R-5.18'),
    ('structure_alignment', 'retain/reconstruct 声明与代码复算变化对齐', 'R-5.19'),
    ('semantic_closure_reading', '章级事实/状态/因果闭合读数', 'R-5.19'),
    ('ctx_source', 'writer_context 的来源账', 'R-5.10c'),
):
    _metric(rule, item, req)
