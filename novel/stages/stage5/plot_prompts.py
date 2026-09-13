"""章级写作任务渲染：唯一剧情正文与逐场许可，不把呈现任务展开成事实数据库。"""
from __future__ import annotations
import json
from typing import Any


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def goal_directive(goal: dict[str, Any]) -> str:
    return compact(goal)


def chapter_directive(entry: dict[str, Any], book) -> str:
    return '\n'.join([
        f"本章功能：{entry['semantic_contract']['chapter_function']}",
        f"场景重写总纲：{entry['chapter_rewrite_brief']}",
        '完整剧情目录（每个节点全文只在此出现一次）：',
        compact(entry['beats']),
        '必要背景剧情（只供知识传播和承接，不重新主落）：' + compact(entry.get('background_beats', [])),
        '持续状态目录（modality 是命题性质，不是读者或人物知情许可）：',
        compact(entry['states']),
        '必要执行约束：' + compact(entry['constraints']),
        '章首状态引用：' + compact(entry['semantic_contract']['entry_state_refs']),
        '章末携带状态引用：' + compact(entry['semantic_contract']['carry_out_refs']),
        '章节、开篇、结尾目标：' + compact({
            key: entry[key] for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal')}),
        '未开放主体（只有引用，没有答案）：' + compact(entry['restricted_subject_refs']),
        '所有编号只供引用任务，不写进小说正文。完整节点由它的全部呈现共同完成。',
        '同次请求可见后场材料，但前场不得提前表达；首次出现不等于整个节点已经披露。',
        '保留动机、施受关系、反应含义和结果；区分客观成立、人物相信、人物声称与未知。',
    ])


def chapter_facts(entry: dict[str, Any], slot_plan: list[int] | None = None) -> str:
    rows = []
    for index, scene in enumerate(entry['scenes']):
        task = dict(scene)
        if slot_plan is not None:
            task['paragraph_target'] = slot_plan[index]
        rows.append(task)
    return ('按 slot 顺序完成下面全部场景；delivery 只安排呈现，不增加子事实。\n'
            '每场按 presentation_instruction 使用父节点，不能把完整父节点在多场重复讲一遍。\n'
            'context_beat_refs 只用于承接已经完整呈现的背景。\n'
            'stages 的 null 表示未知，present=[] 表示有据的无人在场；不推断被提及的人在场。\n'
            + compact(rows) + '\n叙述视角：' + compact(entry['narration']))


def facts_block(entry: dict[str, Any]) -> str:
    return ('── 本章允许下发的共享主体与叫法 ──\n'
            'allowed_slots 允许陈述主体真相；state_permissions 仅允许按所指状态的信念、声称或未知表达，不能升级为真相。\n'
            'effective_from/until 是事实成立窗口（until 包含该章），不能当作披露许可；已结束的事实仍可作为历史披露。\n'
            'actor_ref 是独立表面身份；只有 identity_actors 对应的知识事件开放后，才能断言两种身份是同一人。\n'
            '普通昵称在同一 surfaces 中；每个称谓分别遵守 surface_allowed_slots，不能借同一 actor_ref 的其他称谓提前出现。role 仅在 role_allowed_slots 中使用，不从另一个身份继承。\n'
            '不同版本仍分别服从各自 allowed_slots；局部称呼的施受、改口和对白语境依完整 Beat/State。\n'
            + compact({'global_subjects': entry['global_subjects']}))


def prev_context(entry: dict[str, Any], book) -> str | None:
    previous = book.chapters.get(entry['output_ordinal'] - 1)
    if previous is None:
        return None
    end_refs = {row['beat_ref'] for row in previous['scenes'][-1]['deliveries']}
    ending = [r for r in previous['beats'] if r['beat_ref'] in end_refs]
    current = {row['beat_ref']: row for row in (
        *entry.get('beats', []), *entry.get('background_beats', []))}
    # 上章末节点可能已作为本章必要背景下发；收束只补目录未收录的正文。
    # 同一身份有两份不同正文是输入冲突，不能通过择一隐藏。
    for row in ending:
        if row['beat_ref'] in current and current[row['beat_ref']] != row:
            raise ValueError('上章收束与本章目录含同 ID 不同剧情正文')
    used = {token for row in ending for token in row['participants']}
    current_actors = {row['actor_ref'] for row in entry['characters']}
    previous_actors = {row['actor_ref']: row for row in previous['characters']}
    if not used <= previous_actors.keys():
        raise ValueError('上章收束人物缺少已公开表面身份')
    last = previous['scenes'][-1]['slot']
    roster = []
    for token in sorted(used - current_actors):
        previous_actor = previous_actors[token]
        surfaces = [s for s in previous_actor['surfaces']
                    if last in previous_actor['surface_allowed_slots'][s]]
        if not surfaces:
            raise ValueError('上章收束人物缺少末场可用称谓')
        roster.append({'actor_ref': token, 'display_name': surfaces[0], 'surfaces': surfaces,
                       'role': previous_actor['role'] if last in previous_actor['role_allowed_slots'] else None})
    payload = {
        'previous_public_actors': roster,
        'completed_ending_beat_refs': [r['beat_ref'] for r in ending],
        'completed_ending_beats': [r for r in ending if r['beat_ref'] not in current],
        'carry_out_refs': previous['semantic_contract']['carry_out_refs'],
    }
    return '── 上章收束（仅作衔接，不复述）──\n' + compact(payload)


def chapter_task_blocks(entry: dict[str, Any], book, slot_plan: list[int]):
    from contracts.schema.stage4 import CtxChapter
    CtxChapter.model_validate(entry)
    # 正式 Context 已消除重复正文，不再构造第二份可编辑文字表或 visible_content。
    return (chapter_directive(entry, book), chapter_facts(entry, slot_plan), entry,
            prev_context(entry, book))
