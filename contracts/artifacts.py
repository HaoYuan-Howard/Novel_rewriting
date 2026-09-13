#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物相对路径目录。

这里是路径字面的唯一权威表；schema 注册表、节点 ``produces``、消费投影和运行时读写
只引用符号名。模块只依赖标准库，因而 ``contracts.invariants`` 与 ``contracts.schema``
都能安全导入，不会形成层间环。
"""
from __future__ import annotations

from typing import Final


ARTIFACT_PATHS: Final[dict[str, str]] = {
    # ⚠ 2026-08-22 阶段 C：`revisions.json` 是本层**唯一可变的那一格**，
    #   下面两条退成它的物化投影（路径与字节形态一个字没动，见 `contracts/schema/stage0.py`）。
    #   同批删除的三份：`machine_snapshot.json` / `merge_candidate.json` /
    #   `_audit/edit_{n}.json` —— 它们守的事全部由 revision 链本身承担。
    'stage0.revisions': 'stage0/revisions.json',
    'stage0.clean_text': 'stage0/clean_text.txt',
    'stage1.main_text': 'stage1/main_text.json',
    'stage1.hook_copy': 'stage1/hook_copy.json',
    'stage2.analyze_receipt': 'stage2/analyze_receipt.json',
    'stage2.analyze_recovery': 'stage2/analyze_recovery.json',
    'stage2.analyze_owner_draft_edit': 'stage2/analyze_owner_draft_edit.json',
    'stage2.analyze_characters_draft_repair': 'stage2/analyze_characters_draft_repair.json',
    'stage2.analyze_global_draft_repair': 'stage2/analyze_global_draft_repair.json',
    'stage2.characters': 'stage2/characters.json',
    'stage2.characters_edit': 'stage2/characters_edit.json',
    'stage2.global_grounding': 'stage2/global_grounding.json',
    'stage2.global_review': 'stage2/global_review.json',
    'stage2.global_review_decisions': 'stage2/global_review_decisions.json',
    'stage2.global_edit': 'stage2/global_edit.json',
    # reviewed edit 已经落为 canonical input、但 formal Global/review 尚未提交时的
    # 瞬态恢复事实。它不是节点完成产物，也不是被 HARD 拦下的候选；因此不进入
    # ``NodeSpec.produces``，只供 state/reviewer 关闭式恢复同一个 typed 动作。
    'stage2.global_edit_failure': 'stage2/global_edit_failure.json',
    # canonical GlobalEdit + pending/superseded failure 的 write-ahead journal；
    # 覆盖两个 live 文件之间无法靠 Python rollback 修复的进程硬杀窗口。
    'stage2.global_edit_input_commit': 'stage2/global_edit_input_commit.json',
    # Stage2 Global 正式三件套的 book-local write-ahead journal。reviewed 时它与
    # failure sidecar 分责；generic INVALID 提升即使没有 action-owned GlobalEdit
    # identity / formal baseline 也由同一记录按 baseline/target 两态补尾。
    'stage2.global_edit_commit': 'stage2/global_edit_commit.json',
    'stage2.blueprint_character_projection': 'stage2/blueprint_character_projection.json',
    'stage2.chapter_blueprints': 'stage2/chapter_blueprints.json',
    'stage2.knowledge_links': 'stage2/knowledge_links.json',
    'stage2.blueprint_edit': 'stage2/blueprint_edit.json',
    'stage4.blind_candidate': 'stage4/blind_candidate.json',
    'stage4.blind_chapter_edit': 'stage4/blind_chapter_edit.json',
    'stage4.evidence_binding': 'stage4/evidence_binding.json',
    'stage4.plan_chapters': 'stage4/plan_chapters.json',
    'stage4.plan_edit': 'stage4/plan_edit.json',
    'stage4.writer_context': 'stage4/writer_context.json',
    'stage5.chapter': 'stage5/ch{nn}.json',
    'stage5.rejected_revision': 'stage5/ch{nn}.rejected.json',
    # Commander 先以 free reviewer node 落一份单章、hash-bound 的正式改写请求；
    # paid writer 只消费这一条 canonical 路径，不能把自由文本指令塞进 argv。
    'stage5.reviewer_rewrite_request': 'stage5/reviewer_rewrite_request.json',
    'stage5.review': 'stage5/review_{n}.json',
    'stage5.trim_audit': 'stage5/trim_audit_ch{nn}.json',
    'stage5.trim_resolution': 'stage5/trim_resolution_ch{nn}.json',
    'stage5.wontfix': 'stage5/wontfix.json',
    'stage6.prepare': 'stage6/prepare.json',
    'stage6.prepare_edit': 'stage6/prepare_edit.json',
    'stage6.prepare_review': 'stage6/prepare_review.json',
    'stage6.prepare_review_decisions': 'stage6/prepare_review_decisions.json',
    'stage6.prepare_commit': 'stage6/prepare_commit.json',
    'stage6.hook': 'stage6/hook.json',
    'stage6.hook_edit': 'stage6/hook_edit.json',
    'stage6.semantic': 'stage6/semantic_{n}.json',
    'stage6.wontfix': 'stage6/wontfix.json',
    'stage7.name_map': 'stage7/name_map.json',
    'stage7.name_map_edit': 'stage7/name_map_edit.json',
    # ⚠ **交付物按书名命名**，所以它是本表里唯一的「书级参数化」路径（见 `BOOK_SCOPED_PATHS`）。
    #   占位符由「这本书是谁」决定 ⇒ 每本书恰好解析出一份具名产物，落点唯一在
    #   `novel/paths.py::book_txt_rel`。
    'stage7.book': 'stage7/{title}_rewriting.txt',
}

# 只供识别并拒绝退役产物；不进入当前 artifact registry，不提供解码或迁移。
RETIRED_ARTIFACT_PATHS: Final[dict[str, str]] = {
    'stage2.blueprint_relation_edit': 'stage2/blueprint_relation_edit.json',
    'stage3.style_grounding': 'stage3/style_grounding.json',
}


#: 带占位符的路径分三类，**分类是声明的，不是从字面猜的**：
#:
#:   | 类 | 例 | 占位符由谁决定 | 完成度落点 |
#:   |---|---|---|---|
#:   | 轮次编号 | `stage5/review_{n}.json` | 盘上有几份 | `rounds_done[节点]` |
#:   | 逐章 | `stage5/ch{nn}.json` | 计划里有哪些章 | `chapters_done` |
#:   | **书级** | `stage7/{title}_rewriting.txt` | **这本书是谁** | `artifacts[]`（解析后是具名的） |
#:
#: ⚠⚠ **这张表存在的理由**：前两类问「盘上有几份」，第三类问「它叫什么名字」——
#: 解析出来仍是**一份**具名产物。而 `state.py::_is_pattern` 只看 `'{' in rel`、
#: `paths.is_numbered_pattern` 只看「tail 里恰好一个占位符」，两者都会把书级那条
#: **误判**成轮次编号类：前者把它整个丢出 `artifacts[]`（成书从完成度、freshness 徽章、
#: inbox 里同时消失），后者拿非数字去解析编号得到空集。
#: ⇒ 书级路径必须在那两道分类之前先被解析掉，而「谁是书级」只能从这里查，不能靠字面猜。
BOOK_SCOPED_PATHS: Final[frozenset[str]] = frozenset({'stage7.book'})


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
STAGE2_ANALYZE_RECEIPT = artifact_rel('stage2.analyze_receipt')
STAGE2_ANALYZE_RECOVERY = artifact_rel('stage2.analyze_recovery')
STAGE2_ANALYZE_OWNER_DRAFT_EDIT = artifact_rel('stage2.analyze_owner_draft_edit')
STAGE2_ANALYZE_CHARACTERS_DRAFT_REPAIR = artifact_rel('stage2.analyze_characters_draft_repair')
STAGE2_ANALYZE_GLOBAL_DRAFT_REPAIR = artifact_rel('stage2.analyze_global_draft_repair')
STAGE2_CHARACTERS = artifact_rel('stage2.characters')
STAGE2_CHARACTERS_EDIT = artifact_rel('stage2.characters_edit')
STAGE2_GLOBAL_GROUNDING = artifact_rel('stage2.global_grounding')
STAGE2_GLOBAL_REVIEW = artifact_rel('stage2.global_review')
STAGE2_GLOBAL_REVIEW_DECISIONS = artifact_rel('stage2.global_review_decisions')
STAGE2_GLOBAL_EDIT = artifact_rel('stage2.global_edit')
STAGE2_GLOBAL_EDIT_FAILURE = artifact_rel('stage2.global_edit_failure')
STAGE2_GLOBAL_EDIT_INPUT_COMMIT = artifact_rel(
    'stage2.global_edit_input_commit')
STAGE2_GLOBAL_EDIT_COMMIT = artifact_rel('stage2.global_edit_commit')
STAGE2_BLUEPRINT_CHARACTER_PROJECTION = artifact_rel(
    'stage2.blueprint_character_projection')
STAGE2_CHAPTER_BLUEPRINTS = artifact_rel('stage2.chapter_blueprints')
STAGE2_KNOWLEDGE_LINKS = artifact_rel('stage2.knowledge_links')
STAGE2_BLUEPRINT_EDIT = artifact_rel('stage2.blueprint_edit')
# 仅供旧路径的明确拒绝与历史识别，不在现役 schema 注册表中。
STAGE2_BLUEPRINT_RELATION_EDIT = RETIRED_ARTIFACT_PATHS['stage2.blueprint_relation_edit']
RETIRED_STAGE3_STYLE_GROUNDING = RETIRED_ARTIFACT_PATHS['stage3.style_grounding']
STAGE4_BLIND_CANDIDATE = artifact_rel('stage4.blind_candidate')
STAGE4_BLIND_CHAPTER_EDIT = artifact_rel('stage4.blind_chapter_edit')
STAGE4_EVIDENCE_BINDING = artifact_rel('stage4.evidence_binding')
STAGE4_PLAN_CHAPTERS = artifact_rel('stage4.plan_chapters')
STAGE4_PLAN_EDIT = artifact_rel('stage4.plan_edit')
STAGE4_WRITER_CONTEXT = artifact_rel('stage4.writer_context')
STAGE5_CHAPTER = artifact_rel('stage5.chapter')
STAGE5_REJECTED_REVISION = artifact_rel('stage5.rejected_revision')
STAGE5_REVIEWER_REWRITE_REQUEST = artifact_rel(
    'stage5.reviewer_rewrite_request')
STAGE5_REVIEW = artifact_rel('stage5.review')
STAGE5_TRIM_AUDIT = artifact_rel('stage5.trim_audit')
STAGE5_TRIM_RESOLUTION = artifact_rel('stage5.trim_resolution')
STAGE5_WONTFIX = artifact_rel('stage5.wontfix')
STAGE6_PREPARE = artifact_rel('stage6.prepare')
STAGE6_PREPARE_EDIT = artifact_rel('stage6.prepare_edit')
STAGE6_PREPARE_REVIEW = artifact_rel('stage6.prepare_review')
STAGE6_PREPARE_REVIEW_DECISIONS = artifact_rel('stage6.prepare_review_decisions')
STAGE6_PREPARE_COMMIT = artifact_rel('stage6.prepare_commit')
STAGE6_HOOK = artifact_rel('stage6.hook')
STAGE6_HOOK_EDIT = artifact_rel('stage6.hook_edit')
STAGE6_SEMANTIC = artifact_rel('stage6.semantic')
STAGE6_WONTFIX = artifact_rel('stage6.wontfix')
STAGE7_NAME_MAP = artifact_rel('stage7.name_map')
STAGE7_NAME_MAP_EDIT = artifact_rel('stage7.name_map_edit')
#: ⚠ **它是模板，不是可以直接落盘的路径**（同 `STAGE5_CHAPTER`）：里面还有 `{title}`。
#: 要具体路径请走 `novel/paths.py::book_txt_rel(novel_dir)`；拿这个常量去
#: `store.path()` / 相等比较，得到的是一个字面叫 `{title}_rewriting.txt` 的东西。
STAGE7_BOOK = artifact_rel('stage7.book')
