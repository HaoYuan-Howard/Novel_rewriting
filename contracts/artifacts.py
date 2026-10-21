#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物相对路径目录。

这里是路径字面的唯一权威表；schema 注册表、节点 ``produces``、消费投影和运行时读写
只引用符号名。模块只依赖标准库，因而 ``contracts.invariants`` 与 ``contracts.schema``
都能安全导入，不会形成层间环。
"""
from __future__ import annotations

from typing import Final


from contracts.story_paths import STORY_ARTIFACTS

ARTIFACT_PATHS: Final[dict[str, str]] = {
    'stage0.revisions': 'stage0/revisions.json',
    'stage0.clean_text': 'stage0/clean_text.txt',
    'stage1.main_text': 'stage1/main_text.json',
    'stage1.hook_copy': 'stage1/hook_copy.json',
    **{rel: rel for rel in STORY_ARTIFACTS},
}
# 只供识别并拒绝退役产物；不进入当前 artifact registry，不提供解码或迁移。
# ⚠ F8a：旧 Stage2/4/5/6/7 链全部路径键移入本表（STR-06 切换，F7e 退役登记）。
#   旧书只读识别为 `legacy_incompatible`：`_refuse_retired_artifact` 在正式
#   decode/encode 口拒绝它们，独立新根重建，不手补旧产物、不继承旧批准。
RETIRED_ARTIFACT_PATHS: Final[dict[str, str]] = {
    # ── 旧 Stage2 链 ──
    'stage2.analyze_receipt': 'stage2/analyze_receipt.json',
    'stage2.analyze_recovery': 'stage2/analyze_recovery.json',
    'stage2.analyze_owner_draft_edit': 'stage2/analyze_owner_draft_edit.json',
    'stage2.analyze_characters_draft_repair': 'stage2/analyze_characters_draft_repair.json',
    'stage2.analyze_global_draft_repair': 'stage2/analyze_global_draft_repair.json',
    'stage2.characters': 'stage2/characters.json',
    'stage2.characters_edit': 'stage2/characters_edit.json',
    'stage2.global_grounding': 'stage2/global_grounding.json',
    'stage2.global_edit': 'stage2/global_edit.json',
    'stage2.global_edit_failure': 'stage2/global_edit_failure.json',
    'stage2.global_edit_input_commit': 'stage2/global_edit_input_commit.json',
    'stage2.global_edit_commit': 'stage2/global_edit_commit.json',
    'stage2.blueprint_character_projection': 'stage2/blueprint_character_projection.json',
    'stage2.chapter_blueprints': 'stage2/chapter_blueprints.json',
    'stage2.blueprint_edit': 'stage2/blueprint_edit.json',
    # ── 旧 Stage4 链 ──
    'stage4.blind_candidate': 'stage4/blind_candidate.json',
    'stage4.blind_chapter_edit': 'stage4/blind_chapter_edit.json',
    'stage4.evidence_binding': 'stage4/evidence_binding.json',
    'stage4.plan_edit': 'stage4/plan_edit.json',
    'stage4.writer_context': 'stage4/writer_context.json',
    # ── 旧 Stage5 链 ──
    'stage5.chapter': 'stage5/ch{nn}.json',
    'stage5.rejected_revision': 'stage5/ch{nn}.rejected.json',
    'stage5.reviewer_rewrite_request': 'stage5/reviewer_rewrite_request.json',
    'stage5.review': 'stage5/review_{n}.json',
    'stage5.trim_audit': 'stage5/trim_audit_ch{nn}.json',
    'stage5.trim_resolution': 'stage5/trim_resolution_ch{nn}.json',
    'stage5.wontfix': 'stage5/wontfix.json',
    # ── Stage6 整层（三候选改选与 prepare 链）──
    'stage6.prepare': 'stage6/prepare.json',
    'stage6.prepare_edit': 'stage6/prepare_edit.json',
    'stage6.prepare_review': 'stage6/prepare_review.json',
    'stage6.prepare_review_decisions': 'stage6/prepare_review_decisions.json',
    'stage6.prepare_commit': 'stage6/prepare_commit.json',
    'stage6.hook': 'stage6/hook.json',
    'stage6.hook_edit': 'stage6/hook_edit.json',
    'stage6.semantic': 'stage6/semantic_{n}.json',
    'stage6.wontfix': 'stage6/wontfix.json',
    # ── 旧 Stage7 链 ──
    'stage7.name_map': 'stage7/name_map.json',
    'stage7.name_map_edit': 'stage7/name_map_edit.json',
    # 末端命名产物只读拒识；旧根归档并在独立新根重建。
    'stage7.naming_name_map': 'stage7/naming/name_map.json',
    'stage7.book': 'stage7/{title}_rewriting.txt',
    # ── 更早的退役项（原样保留）──
    'stage2.blueprint_relation_edit': 'stage2/blueprint_relation_edit.json',
    'stage2.knowledge_links': 'stage2/knowledge_links.json',
    'stage2.global_review': 'stage2/global_review.json',
    'stage2.global_review_decisions': 'stage2/global_review_decisions.json',
    # ── B2 五节点重建退役（2026-09-27，D3/D9）：联合抽取的派生输入面──
    # book_extraction 由 compile 直接发布 Characters 双真源取代（D9）；
    # input_snapshots 随 D3「章观察互不依赖」退役（不再有逐章输入快照）。
    # 旧根上的同类文件只读保留，新协议下新根全量重购（I-13，无转换层）。
    'stage2.compiled.book_extraction': 'stage2/compiled/book_extraction.json',
    'stage2.compiled.input_snapshots': 'stage2/compiled/input_snapshots.json',
    'stage3.style_grounding': 'stage3/style_grounding.json',
    # STR-03-S4：正式计划退役；章集/预算/对账边拆入 Design + Context provenance。
    'stage4.plan_chapters': 'stage4/plan_chapters.json',
}



#: 带占位符的路径分三类，**分类是声明的，不是从字面猜的**：
#:
#:   | 类 | 例 | 占位符由谁决定 | 完成度落点 |
#:   |---|---|---|---|
#:   | 轮次编号 | `stage5/review_{n}.json` | 盘上有几份 | `rounds_done[节点]` |
#:   | 逐章 | `stage5/body/ch{nn}.txt` | 计划里有哪些章 | `chapters_done` |
#:   | **书级** | `stage7/{title}_rewriting.txt` | **这本书是谁** | `artifacts[]`（解析后是具名的） |
#:
#: ⚠⚠ **这张表存在的理由**：前两类问「盘上有几份」，第三类问「它叫什么名字」——
#: 解析出来仍是**一份**具名产物。而 `state.py::_is_pattern` 只看 `'{' in rel`、
#: `paths.is_numbered_pattern` 只看「tail 里恰好一个占位符」，两者都会把书级那条
#: **误判**成轮次编号类：前者把它整个丢出 `artifacts[]`（成书从完成度、freshness 徽章、
#: inbox 里同时消失），后者拿非数字去解析编号得到空集。
#: ⇒ 书级路径必须在那两道分类之前先被解析掉，而「谁是书级」只能从这里查，不能靠字面猜。
# ⚠ F8a：书级路径翻为新链成书（`stage7/final/{title}_rewriting.txt`）。
BOOK_SCOPED_PATHS: Final[frozenset[str]] = frozenset({'stage7/final/{title}_rewriting.txt'})


def artifact_rel(name: str, **values: object) -> str:
    """取路径模板；给参数时返回具体路径，``nn`` 统一补零两位。"""
    try:
        template = ARTIFACT_PATHS[name]
    except KeyError:
        raise KeyError(f'未知产物 id：{name!r}') from None
    if not values:
        return template
    supplied = dict(values)
    if 'nn' in supplied:
        supplied['nn'] = f'{int(supplied["nn"]):02d}'
    try:
        return template.format(**supplied)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f'{name} 的路径参数不完整或非法：{values!r}') from exc


STAGE0_REVISIONS = artifact_rel('stage0.revisions')
STAGE0_CLEAN_TEXT = artifact_rel('stage0.clean_text')
STAGE1_MAIN_TEXT = artifact_rel('stage1.main_text')
STAGE1_HOOK_COPY = artifact_rel('stage1.hook_copy')

# 当前正式故事产物版本参与请求和候选契约身份；不提供旧版本解码或迁移。
STR06_ARTIFACT_RC_VERSIONS: Final[dict[str, str]] = {
    rel: 'story-v3' for rel in STORY_ARTIFACTS
}
