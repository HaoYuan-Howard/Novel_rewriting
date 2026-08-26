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
    'stage2.global_grounding': 'stage2/global_grounding.json',
    'stage2.global_review': 'stage2/global_review.json',
    'stage2.global_review_decisions': 'stage2/global_review_decisions.json',
    'stage2.global_edit': 'stage2/global_edit.json',
    'stage2.chapter_blueprints': 'stage2/chapter_blueprints.json',
    'stage3.style_grounding': 'stage3/style_grounding.json',
    'stage4.blind_candidate': 'stage4/blind_candidate.json',
    'stage4.evidence_binding': 'stage4/evidence_binding.json',
    'stage4.plan_chapters': 'stage4/plan_chapters.json',
    'stage4.plan_edit': 'stage4/plan_edit.json',
    'stage4.writer_context': 'stage4/writer_context.json',
    'stage5.chapter': 'stage5/ch{nn}.json',
    'stage5.rejected_revision': 'stage5/ch{nn}.rejected.json',
    'stage5.review': 'stage5/review_{n}.json',
    'stage5.trim_audit': 'stage5/trim_audit_ch{nn}.json',
    'stage5.trim_resolution': 'stage5/trim_resolution_ch{nn}.json',
    'stage5.wontfix': 'stage5/wontfix.json',
    'stage5.book_draft': 'stage5/draft.md',
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
STAGE2_GLOBAL_GROUNDING = artifact_rel('stage2.global_grounding')
STAGE2_GLOBAL_REVIEW = artifact_rel('stage2.global_review')
STAGE2_GLOBAL_REVIEW_DECISIONS = artifact_rel('stage2.global_review_decisions')
STAGE2_GLOBAL_EDIT = artifact_rel('stage2.global_edit')
STAGE2_CHAPTER_BLUEPRINTS = artifact_rel('stage2.chapter_blueprints')
STAGE3_STYLE_GROUNDING = artifact_rel('stage3.style_grounding')
STAGE4_BLIND_CANDIDATE = artifact_rel('stage4.blind_candidate')
STAGE4_EVIDENCE_BINDING = artifact_rel('stage4.evidence_binding')
STAGE4_PLAN_CHAPTERS = artifact_rel('stage4.plan_chapters')
STAGE4_PLAN_EDIT = artifact_rel('stage4.plan_edit')
STAGE4_WRITER_CONTEXT = artifact_rel('stage4.writer_context')
STAGE5_CHAPTER = artifact_rel('stage5.chapter')
STAGE5_REJECTED_REVISION = artifact_rel('stage5.rejected_revision')
STAGE5_REVIEW = artifact_rel('stage5.review')
STAGE5_TRIM_AUDIT = artifact_rel('stage5.trim_audit')
STAGE5_TRIM_RESOLUTION = artifact_rel('stage5.trim_resolution')
STAGE5_WONTFIX = artifact_rel('stage5.wontfix')
STAGE5_BOOK_DRAFT = artifact_rel('stage5.book_draft')
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
