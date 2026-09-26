#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2_context_probe —— 质量改进批次二 · 批 0 的 Stage2 上下文/架构探针。

【它回答什么】现行 v12 契约下，第 3 章抽取失败（QP-01：夜间行为主体误归表弟、
    错误 intimacy 关系进 ch04 请求）在多大程度上源于「章请求看不到前文」。同一逐章
    架构下比较三种上下文形态（只换第 k 章输入）：
      A  现行第 k 章请求——`chain.chapter_extraction_prompt` 逐字复用，不重抄；
      B  A＋前文原文前缀（章前文字＋第 1–2 章原文，只读，不带证据 id）；
      C  B＋正文优先规则＋快照逐条带来源（D11 的探针近似：每条状态标建立拍）。
    架构探针（`--arch`）研究的是另一个问题：单次调用产出 ch k..m 能否自然解决跨章
    指代，且不引入截断、后见倒灌与长篇扩展问题。它与 A/B/C 任务分解不同，不构成
    同架构横向比率比较（预登记裁决规则 3 另有绝对门槛）。

【闸】默认 0 成本预览：打印各臂请求与现行请求的差异、精确 token 数与计划样本根，
    一个请求都不发、一个字节都不写。只有 `--execute --yes --allow-egress` 才购买；
    付费与外发是两份独立授权（I-43），本工具不代批。设施装配复用
    `novel.stages.chain.assemble_paid_boundary`（同一 session／ledger／预算闸／
    workflow epoch），不另建付费通路；请求构造复用 `chain.str06_chat_request`。

【采样纪律】每个样本一个独立探针根 `<out-root>/<臂>_<序号>/`，不挂 `--adopt-root`：
    请求缓存按根存 `runtime/request_<identity>.json`，同根同身份只读回旧响应，重复
    执行不产生第二次购买。臂 A 样本 1 是前轮已购原响应：`--baseline-original` 只读
    复证（零购买），并顺带证明臂 A 请求与前轮已购请求逐字一致（identity 相符）。

【读数与判定】工具只做机械抽取与落证：编译是否通过、finish_reason、completion／
    reasoning token（从请求文件 usage 读；ledger 该列目前为 null）、「嫂嫂」拍与
    relation effects 等结构化摘录。语义判定按预登记裁决规则由人执行，跑后不改规则；
    原始 wire 全量落样本根供人工核读（后见措辞等）。

【探针臂只存在于本工具】不进生产 prompt、不进契约；结论登记进 REQUIREMENTS／
    DECISIONS 后，本工具随批 0 关闭，不为后续批次扩展。

用法：
  # 0 成本预览：全部臂＋架构探针的差异与 token 数（默认行为）
  python -X utf8 tools/stage2_context_probe.py

  # 单臂预览（章范围可调；批 3 复读用 --from-ch 3 --to-ch 5）
  python -X utf8 tools/stage2_context_probe.py --arm C --from-ch 3 --to-ch 3

  # 只读复证臂 A 样本 1（前轮原响应，零购买、零写入生产根）
  python -X utf8 tools/stage2_context_probe.py --arm A --sample 1 --baseline-original

  # 真跑（付费＋外发分别授权；每个样本一个独立根）
  python -X utf8 tools/stage2_context_probe.py --arm B --sample 2 --execute --yes --allow-egress
  python -X utf8 tools/stage2_context_probe.py --arch --sample 1 --execute --yes --allow-egress
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel.llm.json_text import normalize_json_text                    # noqa: E402
from novel.llm.paid_requests import digest                             # noqa: E402
from novel.stages.chain import (                                       # noqa: E402
    CHAPTER_PROMPT, SYS, assemble_paid_boundary, build_evidence,
    chapter_extraction_prompt, str06_chat_request,
)
from novel.stages.stage2 import (                                      # noqa: E402
    ChapterCompiler, assemble_blueprint, chapter_input_snapshot, compile_book,
    initial_snapshot,
)

#: 前轮质量验收根（只读）：book 已修订 wire、ch01–05 原响应与 Stage1 输入都在这里。
DEFAULT_PREFIX_ROOT = Path('outputs/str06_quality_paid_20260926')

#: B／C 臂插入的前文块标题（工具本地；生产布局归批 1C 裁决）。
PRIOR_HEADING = '【前文原文】'

#: C 臂的正文优先规则（D11 的探针近似）：快照是带来源的参考，不是正文的替代。
TEXT_FIRST_RULE = """【快照与正文的关系】上方状态快照是带来源的参考与标识登记，不是正文的
替代：本拍事实以正文原文为准——不得为迁就快照改写施事、受事或动作主体，也不得因为
快照里没有就略去正文写出的变化；快照与正文不一致时，按正文记录，快照原值仅作参考。
每条快照后面的括号标出该状态最后一次由哪一拍（或 initial_state 哪一条）建立。"""

#: 架构探针专用 prompt（批 0 冻结：规则镜像现行 CHAPTER_PROMPT 的字段要求，按
#: 「单次多章输出」改写跨章表述；不用快照输入，loop 在本次输出内沿用）。
ARCH_PROMPT = """任务：一次性读完下列小说正文，按章产出有序 beats，以及每拍的状态效应、知识揭示与因果边。

【人物表】（participants / targets / recipients 只能用这些 id）
{glossary}

【小说正文】（〔第N章〕为章界标记；开头的〔章前文字〕是本书引子，其证据段 id 以 e00_ 开头）
{text}

【证据段 id 清单】（source_refs 只能从中选）
{evidence}

【输出形状】只输出一个 JSON object：
{{"chapters":[{{"chapter":{from_ch},"beats":[...],"causal_edges":[]}}, ...]}}
第 {from_ch} 至 {to_ch} 章各一项，按章序升序；chapter 为章序整数，beats 非空。

【要求】
1. beats：按故事顺序拆拍，每拍 {{"beat_id","event","participants","source_refs","effects","reveals"}}：
   - event：写"发生了什么"，并保住足以正确演出这一事件的语义——必要前因、关键指代
     （如"他那里"须写明指什么）、已成立的站位（谁在床上/床边/门外）、公开行为与内心
     反应的差别；不要混入动机分析或世界规则。
     转述对话/问句必须保真主客体：谁向谁借、谁问谁、借出方是谁，代词消解后写明对象，
     不得翻转施事与受事；身份关系必须写明归属（谁的同窗/表亲/好友），不得悬空成"同窗"；
   - 动作的范围与去向以原文为准：走了多远、去了哪里、交给了谁，原文写到哪就记到哪，
     不外推、不补全（没写"送出住处"就不得记成送离住处）；
   - 人物（含第一人称叙述者）对自己的定性是自述：记进 event，自述与行为、内心矛盾时
     两面都保留，不得只留一面，也不得把自述直接当作已成立的内在准则；
   - participants：该拍出场的人物 id（可空数组）；
   - source_refs：支撑该拍的证据段 id，1-4 个；
   - beat_id：b{{章序:02d}}_<两位序号>（如 b03_01），本次输出内唯一。
2. effects：状态的 postcondition——本拍结束后仍成立、且后续涉及该维度时必须以它为准的事实。
   每项 {{"target","key","op","value"}}：
   - target：{{"kind":"character","char_id":...}} | {{"kind":"relation","subject":...,"object":...}} |
     {{"kind":"world"}} | {{"kind":"loop","loop_id":"loop_<slug>"}}（loop 用于开局未收束的悬念线）；
   - 不因"以后可能重要"而存：即时动作、表情、一次冷哼、一次脸红留在 event；
     持续的恐惧/戒备/意图/关系变化才是状态；
   - 同一语义变化只选一个最自然的 owner：人物自己的意图/心态归 character；双方已形成的
     关系事实归 relation；客观环境归 world；未决问题归 loop。不为"完整"把同一变化
     在 character 和 relation 各写一遍；
   - 角色位置只有 whereabouts 一个维度（藏身之处也是位置，如"陆家卧房内，藏于床底"）；
     某人现身/归来/离开时 set 他自己的 whereabouts，不另开近义键；
   - 改变哪个维度就替换哪个键；场景性临时状态（借宿、暂住、暂离）在结束或转移时必须 set
     对应维度的新值——去向未知就写"去向未交代"，不得留旧值让下游补造；不另开新键；
   - key 与状态快照同一套语义维度（如 marital_status / fertility / occupation…），新维度用英文蛇形；
     状态被取代或结束时，对原键 set 新值或 unset，不另开近义键——同一维度全书一个键；
   - op：set(赋新值)/unset(清除)/add(集合加入)/remove(集合移除)/open(悬念开启)/close(悬念收束)；
     open/close 的 target 必须是 loop，value 为悬念的问题内容（open 必填非空）；
     loop 类 effect 必须带 key（悬念线的问题维度，如 hiding_place_suspicion）；
   - 无 postcondition 的拍 effects 给空数组。
3. reveals：只收信息不对称——"谁知道/谁不知道/是怀疑还是确认"会约束后续行为、误解、
   隐瞒或揭示时才建。每项 {{"knowledge_key","statement","recipients","mode"}}：
   - knowledge_key 是含义稳定的原子命题键：只在同一人、同一件事、同一次（同一命题）时
     复用 key；不同次的藏身、不同的约定是不同的命题，各用新 key，不得并成一次确认；
     同一 key 的 statement 永不改写，认知升级（suspects→confirms）沿用同 key 同 statement，
     只换 mode 与 recipients；
   - 普通在场见闻、例行告知、event 已说清的事实不要建 reveal；
   - recipients：["reader"|"public"|<人物 id>...]，至少 1 个；人物 id 必须原样使用上方
     人物表里的 id（不得改写、加前缀或换人称）；
   - mode：learns(确凿得知)/suspects(起疑)/confirms(证实怀疑)/hears_claim(听到说法未知真假)；
   - 公开的表态、改口、说辞属于 event，不得据此推导当事人内心偏好或决定已变，
     除非文本明确写出内心变化。
4. causal_edges：只收有信息增量的因果——不显然、跨距离、跨章、setup-payoff，或缺失会
   造成错误因果理解的关系。每项 {{"from_beat","to_beat","kind"}}，
   kind ∈ causes/motivates/enables/setup_payoff。to_beat 必须是同章的 beat；
   from_beat 可引用本次输出中任意更早的 beat（含前章）。
5. 跨章一致性：同一事件只有一个 canonical beat，后章不得改写前章已建立的 beat；
   前章已 open 的 loop 在后文沿用同一 loop_id，不重复 open；没有状态快照输入，
   loop_id 在本次输出内自行保持一致。不要把财富、位置等不同维度塞入同一个 key。"""


# ───────────────────────── 前缀装配（免费） ─────────────────────────

@dataclass
class ProbePrefix:
    """固定前缀：Stage1 输入＋已修订 book wire＋ch01..k 已购 wire 的免费编译产物。"""

    root: Path
    book_dir: Path
    sid: str
    chapters: list[dict]
    hook_copy: str
    source_identity: str
    root_sha1: str
    book_wire: dict
    book_wire_sha256: str
    core: object
    world_constraints: list
    evidence: dict
    prefix_through: int
    compiled: list
    compiler: ChapterCompiler


def _walk_path(wire: dict, path: str):
    node = wire
    for part in path.split('.'):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _find_book_dir(prefix_root: Path) -> tuple[Path, str]:
    """定位前缀根下唯一书目录（以 Stage1 main_text 为证）及其不可变 sid。

    sid 取目录名最后一段 `_` 后的部分（`<书名>_<id>` 布局；id 本身不含 `_`），
    与 `paths.find_novel_dir` 的 `*_<id>` 匹配同一语义。多于一个书目录即拒绝。
    """
    candidates = sorted(p.parent.parent for p in prefix_root.glob('*/stage1/main_text.json'))
    if len(candidates) != 1:
        raise SystemExit(
            f'前缀根下应有恰好一个带 Stage1 输入的书目录，实得 {len(candidates)}：'
            f'{[c.name for c in candidates]}')
    book_dir = candidates[0]
    sid = book_dir.name.rsplit('_', 1)[-1]
    if not sid or sid == book_dir.name:
        raise SystemExit(f'书目录名 {book_dir.name!r} 取不出不可变 sid（<书名>_<id>）')
    return book_dir, sid


def _read_wire(prefix_root: Path, owner: str, *, original: bool = False) -> tuple[dict, Path]:
    """取 owner 的 wire。正式路径 `<root>/wires/<owner>_wire.json`（采用目录布局）；
    `original=True` 取前轮原响应可读副本 `<root>/acceptance/<owner>_original_wire.json`。"""
    if original:
        path = prefix_root / 'acceptance' / f'{owner}_original_wire.json'
        if not path.is_file():
            raise SystemExit(f'前缀根找不到 {owner} 的原响应副本：{path}')
        return json.loads(path.read_text(encoding='utf-8-sig')), path
    for path in (prefix_root / 'wires' / f'{owner}_wire.json',
                 prefix_root / 'acceptance' / f'{owner}_original_wire.json'):
        if path.is_file():
            return json.loads(path.read_text(encoding='utf-8-sig')), path
    raise SystemExit(f'前缀根找不到 {owner} 的 wire（wires/ 与 acceptance/ 均无）')


def load_probe_prefix(prefix_root: Path, prefix_through: int) -> ProbePrefix:
    """免费装配固定前缀：Stage1 章数组＋已修订 book wire＋ch01..k 原 wire 的编译。"""
    from novel.stages.chain import load_stage1_chapters
    book_dir, sid = _find_book_dir(prefix_root)
    stage1 = load_stage1_chapters(prefix_root, sid)
    book_wire, book_path = _read_wire(prefix_root, 'book')
    note_path = prefix_root / 'wires' / 'book_patch_note.json'
    if note_path.is_file():
        # 修订留痕核对：patch note 的每个 change 必须与「原响应 → 已采用 wire」
        # 的逐字差异一致——否则前缀吃的是来路不明的 book wire。
        note = json.loads(note_path.read_text(encoding='utf-8-sig'))
        original, _ = _read_wire(prefix_root, 'book', original=True)
        for change in note.get('changes') or []:
            if (_walk_path(original, change['path']) != change['before']
                    or _walk_path(book_wire, change['path']) != change['after']):
                raise SystemExit(f'book patch note 与盘上 wire 不一致：{change}')
    evidence = build_evidence(stage1.chapters, stage1.hook_copy)
    core, wcs, errors = compile_book(book_wire, evidence_ids=evidence)
    if errors:
        raise SystemExit(f'前缀 book wire 编译失败：{errors}')
    compiler = ChapterCompiler(core, evidence_ids=evidence)
    compiled: list = []
    for no in range(1, prefix_through + 1):
        wire, _ = _read_wire(prefix_root, f'ch{no:02d}')
        blueprint, errors = compiler.compile(wire, no)
        if errors:
            raise SystemExit(f'前缀 ch{no:02d} wire 编译失败：{errors}')
        compiled.append(blueprint)
    return ProbePrefix(
        root=prefix_root, book_dir=book_dir, sid=sid, chapters=stage1.chapters,
        hook_copy=stage1.hook_copy, source_identity=stage1.source_identity,
        root_sha1=stage1.root_sha1, book_wire=book_wire,
        book_wire_sha256=_sha256_file(book_path),
        core=core, world_constraints=wcs, evidence=evidence,
        prefix_through=prefix_through, compiled=compiled, compiler=compiler)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ───────────────────────── 臂 prompt 组装 ─────────────────────────

def prior_text_block(prefix: ProbePrefix, before_chapter: int) -> str:
    """B／C 臂的前文原文前缀：章前文字＋第 1..k-1 章原文，只读、不带证据 id。"""
    parts = [PRIOR_HEADING + '（只读参考，截至第 %d 章末；不带证据 id，不要求引用）'
             % (before_chapter - 1)]
    if prefix.hook_copy.strip():
        parts.append('〔章前文字〕\n' + prefix.hook_copy)
    for chapter in prefix.chapters:
        if chapter['chapter_ordinal'] < before_chapter:
            parts.append(f'〔第{chapter["chapter_ordinal"]}章〕\n{chapter["text"]}')
    return '\n\n'.join(parts)


def _probe_tkey(target: dict) -> tuple:
    """fold 状态键的探针侧镜像（与 `stage2/source.py::_tkey` 同构；一致性由
    `_annotated_snapshot` 对 `chapter_input_snapshot` 的对账断言兜底）。"""
    kind = target.get('kind')
    if kind == 'character':
        return ('char', target.get('char_id'))
    if kind == 'relation':
        return ('rel', target.get('subject'), target.get('object'))
    if kind == 'loop':
        return ('loop', target.get('loop_id'))
    return ('world',)


def _render_key(key: tuple) -> str:
    return f'{key[0][0]}:{",".join(map(str, key[0][1:]))}.{key[1]}'


def annotated_snapshot(prefix: ProbePrefix, compiled: list, chapter: int) -> list[str]:
    """C 臂快照：每条状态带建立来源（D11 探针近似），值与生产 fold 逐条对账。"""
    state: dict[tuple, dict] = {}
    for atom in prefix.core.initial_state:
        target = atom.target.model_dump() if hasattr(atom.target, 'model_dump') else dict(atom.target)
        state[(_probe_tkey(target), atom.key)] = {
            'value': atom.value, 'source': f'initial_state {atom.state_id}'}
    for blueprint in sorted(compiled, key=lambda c: c.chapter):
        if blueprint.chapter >= chapter:
            continue
        for beat in blueprint.beats:
            for eff in beat.effects:
                target = eff.target.model_dump() if hasattr(eff.target, 'model_dump') else dict(eff.target)
                key = (_probe_tkey(target), eff.key)
                if eff.op in ('set', 'open'):
                    state[key] = {'value': eff.value, 'source': beat.beat_id}
                elif eff.op == 'unset' or eff.op == 'close':
                    state.pop(key, None)
                elif eff.op == 'add':
                    cur = (state.get(key) or {}).get('value')
                    lst = list(cur) if isinstance(cur, list) else ([] if cur is None else [cur])
                    if eff.value not in lst:
                        lst.append(eff.value)
                    state[key] = {'value': lst, 'source': beat.beat_id}
                elif eff.op == 'remove':
                    cur = (state.get(key) or {}).get('value')
                    if isinstance(cur, list) and eff.value in cur:
                        cur.remove(eff.value)
                    state[key] = {'value': cur, 'source': beat.beat_id}
    rendered = [f'{_render_key(k)} = {v["value"]!r}（{v["source"]} 建立）'
                for k, v in sorted(state.items(), key=str)]
    # 与生产 fold 对账：左半（键＝值）必须逐条一致——探针侧镜像漂移即红。
    production = chapter_input_snapshot(prefix.core, assemble_blueprint(
        prefix.world_constraints, compiled), chapter) if chapter > 1 else initial_snapshot(prefix.core)
    if sorted(line.split(' = ')[0] for line in rendered) != sorted(
            line.split(' = ')[0] for line in production):
        raise SystemExit('C 臂快照镜像与生产 fold 的键集合不一致；探针工具需同步')
    return rendered


def arm_prompt(prefix: ProbePrefix, chapter: int, arm: str, compiled: list) -> str:
    """第 k 章的臂 prompt。A 逐字走生产组装；B 加前文块；C 再加正文优先规则与
    带来源快照。"""
    request = chapter_extraction_prompt(
        chapter=chapter, book_characters=prefix.book_wire['characters'], core=prefix.core,
        world_constraints=prefix.world_constraints, compiled=compiled,
        compiler=prefix.compiler, evidence=prefix.evidence)
    if arm == 'A':
        return request.prompt
    marker = f'【第 {chapter} 章正文】'
    if marker not in request.prompt:
        raise SystemExit(f'CHAPTER_PROMPT 里找不到章正文标记 {marker!r}；模板已变，探针需同步')
    prompt = request.prompt.replace(marker, prior_text_block(prefix, chapter) + '\n\n' + marker, 1)
    if arm == 'B':
        return prompt
    kreg_marker = '【已登记的知识命题】'
    if kreg_marker not in prompt:
        raise SystemExit('CHAPTER_PROMPT 里找不到知识命题标记；模板已变，探针需同步')
    snapshot_lines = annotated_snapshot(prefix, compiled, chapter)
    prompt = CHAPTER_PROMPT.format(snapshot='\n'.join(snapshot_lines), **request.slots)
    prompt = prompt.replace(marker, prior_text_block(prefix, chapter) + '\n\n' + marker, 1)
    return prompt.replace(kreg_marker, TEXT_FIRST_RULE + '\n\n' + kreg_marker, 1)


def arch_prompt(prefix: ProbePrefix, from_ch: int, to_ch: int) -> str:
    """架构探针 prompt：单次调用产出 ch from_ch..to_ch（探针专用，批 0 冻结）。"""
    text_parts = []
    if prefix.hook_copy.strip():
        text_parts.append('〔章前文字〕\n' + prefix.hook_copy)
    for chapter in prefix.chapters:
        if from_ch <= chapter['chapter_ordinal'] <= to_ch:
            text_parts.append(f'〔第{chapter["chapter_ordinal"]}章〕\n{chapter["text"]}')
    evidence = '\n'.join(
        f'[{k}] {v["text"]}' for k, v in prefix.evidence.items()
        if from_ch <= v['chapter'] <= to_ch or (from_ch == 1 and v.get('pre_chapter')))
    return ARCH_PROMPT.format(
        glossary=json.dumps({'人物闭集': prefix.book_wire['characters']},
                            ensure_ascii=False, indent=1, sort_keys=True),
        text='\n\n'.join(text_parts), evidence=evidence,
        from_ch=from_ch, to_ch=to_ch)


# ───────────────────────── 读数（机械抽取；语义判定归预登记规则） ─────────────────────────

def _beats(wire) -> list[dict]:
    return list((wire or {}).get('beats') or [])


def context_readings(wire, compile_errors: list[str]) -> dict:
    """上下文臂读数：QP-01 相关摘录（「嫂嫂」拍、relation effects、位置键）＋编译事实。"""
    beats = _beats(wire)
    relations, positions, loops = [], [], []
    for beat in beats:
        for eff in beat.get('effects') or []:
            target = eff.get('target') or {}
            if target.get('kind') == 'relation':
                relations.append({'beat': beat.get('beat_id'),
                                  'subject': target.get('subject'),
                                  'object': target.get('object'), 'key': eff.get('key'),
                                  'op': eff.get('op'), 'value': eff.get('value')})
            if (target.get('kind') == 'loop'
                    or eff.get('op') in ('open', 'close')):
                loops.append({'beat': beat.get('beat_id'), 'op': eff.get('op'),
                              'loop_id': target.get('loop_id'), 'key': eff.get('key'),
                              'value': eff.get('value')})
            if 'whereabouts' in (eff.get('key') or '') or 'location' in (eff.get('key') or ''):
                positions.append({'beat': beat.get('beat_id'), 'key': eff.get('key'),
                                  'op': eff.get('op'), 'value': eff.get('value')})
    return {
        'compiled': not compile_errors,
        'compile_errors': compile_errors,
        'sao_sao_beats': [{'beat': b.get('beat_id'), 'participants': b.get('participants'),
                           'event': b.get('event')}
                          for b in beats if '嫂嫂' in (b.get('event') or '')],
        'relation_effects': relations,
        'position_effects': positions,
        'loop_effects': loops,
        'beat_count': len(beats),
    }


def arch_readings(chapters: list[dict]) -> dict:
    """架构探针读数：截断、逐章编译、ch04 床底揭示与 ch05 衣柜记法的机械摘录。

    `chapters` 每项带 wire 层的 beats（编译失败也照常摘录——失败本身就是读数）。
    """
    per_chapter = []
    for entry in chapters:
        beats = _beats(entry)
        bed = [{'beat': b.get('beat_id'), 'participants': b.get('participants'),
                'event': b.get('event'), 'reveals': b.get('reveals')}
               for b in beats if '床' in (b.get('event') or '')]
        wardrobe = [{'beat': b.get('beat_id'), 'participants': b.get('participants'),
                     'event': b.get('event'),
                     'effects': [e for e in (b.get('effects') or [])
                                 if 'whereabouts' in (e.get('key') or '')
                                 or 'location' in (e.get('key') or '')]}
                    for b in beats if '衣柜' in (b.get('event') or '')]
        per_chapter.append({
            'chapter': entry.get('chapter'), 'compiled': entry.get('compiled'),
            'compile_errors': entry.get('compile_errors'), 'beat_count': len(beats),
            'bed_beats': bed, 'wardrobe_beats': wardrobe,
        })
    return {'chapters': per_chapter}


# ───────────────────────── 执行 ─────────────────────────

def _request_record(repository, identity: str) -> dict:
    return repository.read(f'runtime/request_{identity}.json')


def _usage_of(record: dict) -> dict:
    usage = record.get('usage') or {}
    details = usage.get('completion_tokens_details') or {}
    return {'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'reasoning_tokens': details.get('reasoning_tokens'),
            'finish_reason': record.get('finish_reason'),
            'model': record.get('model'), 'latency_ms': record.get('latency_ms'),
            'status': record.get('status')}


def _identity_of(boundary, owner: str, request) -> str:
    return digest({'owner': owner, 'provider': boundary.session.provider,
                   'request': request.cache_payload()})


def _decode_wire(content: str):
    return json.JSONDecoder().decode(normalize_json_text(content))


def _recover_wire(repository, identity: str) -> tuple[dict | None, list[str]]:
    """从请求记录取回原响应 content 并解码（校验失败／解码失败都走这里）。

    购买已经发生、原响应已由 session 落盘：探针把「没通过」记作读数，而不是
    让异常把样本报告吞掉。返回 (wire, problems)。
    """
    try:
        record = _request_record(repository, identity)
    except FileNotFoundError:
        return None, ['请求记录缺失：购买未落盘']
    try:
        return _decode_wire(record.get('content') or ''), []
    except (ValueError, TypeError) as exc:
        return None, [f'原响应解码失败：{exc}']


def run_context_sample(args, prefix: ProbePrefix, boundary, label: str) -> dict:
    """上下文臂样本：逐章购买（或读回本根缓存）→ 编译读数 → 落样本根报告。"""
    repository = boundary.repository
    # 与生产同一形状：一个 compiler 跨章累积（前缀章已登记的 beat／命题是第 k 章
    # prompt 的 known_beats／kreg 来源，也是跨章因果边可解析的前提）。
    compiler = prefix.compiler
    compiled = list(prefix.compiled)
    chapters_report, requests_report, wires = [], [], {}
    for no in range(args.from_ch, args.to_ch + 1):
        prompt = arm_prompt(prefix, no, args.arm, compiled)
        request = boundary.build_request(
            f'ch{no:02d}', prompt, json_mode=True, system=SYS, temperature=0.3,
            reasoning_effort=args.reasoning_effort)
        identity = _identity_of(boundary, f'ch{no:02d}', request)
        inline_errors: list[str] = []

        def validate(wire, _no=no):
            inline_errors[:] = compiler.validate(wire, _no)
            return list(inline_errors)

        try:
            wire = boundary.session.ask(f'ch{no:02d}', request, validate)
        except ValueError as exc:
            # 校验失败不外抛：原响应已由 session 落盘，探针从请求文件取回，
            # 把「编译未通过」记作本样本的读数（预登记裁决要的就是失败率）。
            wire, problems = _recover_wire(repository, identity)
            if wire is None:
                chapters_report.append({'chapter': no, 'compiled': False,
                                        'compile_errors': problems + [str(exc)]})
                break
        record = _request_record(repository, identity)
        requests_report.append({'owner': f'ch{no:02d}', 'identity': identity,
                                **_usage_of(record)})
        blueprint, compile_errors = compiler.compile(wire, no)
        chapters_report.append({'chapter': no, **context_readings(wire, compile_errors)})
        wires[f'ch{no:02d}'] = wire
        if compile_errors:
            break
        compiled.append(blueprint)
    report = _sample_report(args, prefix, label, requests_report, {
        'mode': 'context', 'arm': args.arm, 'chapters': chapters_report})
    _persist(repository, label, report, wires)
    return report


def run_arch_sample(args, prefix: ProbePrefix, boundary, label: str) -> dict:
    """架构探针样本：单次购买 ch from_ch..to_ch，逐章交 ChapterCompiler 编译。"""
    repository = boundary.repository
    prompt = arch_prompt(prefix, args.from_ch, args.to_ch)
    request = boundary.build_request(
        'book', prompt, json_mode=True, system=SYS, temperature=0.3,
        reasoning_effort=args.reasoning_effort)
    identity = _identity_of(boundary, 'book', request)
    # 形状校验不作为探针闸：单次多章输出是否截断／是否逐章可编译正是本探针的读数，
    # 原响应一律先落盘（session 的职责），再由本函数取回编译。
    try:
        boundary.session.ask('book', request, lambda w: [])
    except ValueError:
        pass
    record = _request_record(repository, identity)
    wire, problems = _recover_wire(repository, identity)
    if wire is None or not isinstance(wire, dict):
        # 连原响应都取不回：购买本身的问题（unknown/传输失败），不是探针读数。
        raise SystemExit(f'架构探针未取得可解码响应：{problems}')
    requests_report = [{'owner': 'book', 'identity': identity, **_usage_of(record)}]
    # 架构探针从零产出 ch from_ch..to_ch：compiler 不带前缀章的 beat／命题，
    # 跨章引用只能落在本次输出内（prompt 已如此约束）。
    compiler = ChapterCompiler(prefix.core, evidence_ids=prefix.evidence)
    chapters = []
    entries = wire.get('chapters')
    if not isinstance(entries, list):
        chapters.append({'chapter': None, 'compiled': False, 'beats': [],
                         'compile_errors': ['响应缺 chapters 数组（形状失败）']})
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            chapters.append({'chapter': None, 'compiled': False, 'beats': [],
                             'compile_errors': ['chapters 项不是 object']})
            continue
        no = entry.get('chapter')
        if not isinstance(no, int) or not (args.from_ch <= no <= args.to_ch):
            chapters.append({'chapter': no, 'compiled': False,
                             'beats': entry.get('beats') or [],
                             'compile_errors': [f'章序 {no!r} 不在探针范围 '
                                                f'{args.from_ch}..{args.to_ch}']})
            continue
        _, errors = compiler.compile(
            {'beats': entry.get('beats'), 'causal_edges': entry.get('causal_edges')}, no)
        chapters.append({'chapter': no, 'compiled': not errors, 'compile_errors': errors,
                         'beats': entry.get('beats') or []})
    report = _sample_report(args, prefix, label, requests_report, {
        'mode': 'arch', 'arm': None,
        'readings': {**arch_readings(chapters),
                     'truncated': record.get('finish_reason') != 'stop',
                     'finish_reason': record.get('finish_reason')},
        'chapters': [{k: v for k, v in c.items() if k != 'beats'} for c in chapters]})
    _persist(repository, label, report, {'arch': wire})
    return report


def run_baseline_original(args, prefix: ProbePrefix, out_root: Path) -> dict:
    """臂 A 样本 1：只读复证前轮已购原响应（零购买、零写入前缀根）。"""
    if args.arm != 'A':
        raise SystemExit('--baseline-original 只对臂 A 有意义（样本 1＝前轮原响应）')
    if args.from_ch != args.to_ch:
        raise SystemExit('--baseline-original 只支持单章（原响应按章各购）')
    no = args.from_ch
    readable, _ = _read_wire(prefix.root, f'ch{no:02d}', original=True)
    cfg = _cfg()
    request = str06_chat_request(
        cfg, f'ch{no:02d}', arm_prompt(prefix, no, 'A', prefix.compiled),
        json_mode=True, system=SYS, temperature=0.3,
        reasoning_effort=args.reasoning_effort)
    identity = digest({'owner': f'ch{no:02d}', 'provider': cfg.base_url,
                       'request': request.cache_payload()})
    record_path = prefix.book_dir / 'runtime' / f'request_{identity}.json'
    fidelity = {'arm_a_identity': identity,
                'prefix_root_request': (record_path.name if record_path.is_file() else None)}
    if not record_path.is_file():
        raise SystemExit(
            f'臂 A 请求 identity {identity[:12]}… 在前缀根找不到已购请求记录：'
            '现行代码组装出的「现行请求」与前轮实际购买不一致，探针基线失效')
    record = json.loads(record_path.read_text(encoding='utf-8-sig'))
    # 请求记录的 content 是权威原响应；acceptance 可读副本只作结构核对。
    wire = _decode_wire(record['content'])
    fidelity['readable_copy_matches'] = (wire == readable)
    _, compile_errors = prefix.compiler.compile(
        {'beats': wire.get('beats'), 'causal_edges': wire.get('causal_edges')}, no)
    report = {'tool': 'stage2_context_probe', 'mode': 'baseline-original', 'arm': 'A',
              'sample': args.sample, 'chapter_range': [args.from_ch, args.to_ch],
              'prefix': _prefix_identity(prefix), 'fidelity': fidelity,
              'requests': [{'owner': f'ch{no:02d}', 'identity': identity,
                            **_usage_of(record)}],
              'chapters': [{'chapter': no, **context_readings(wire, compile_errors)}]}
    target = out_root / f'A_{args.sample}'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'probe_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (target / 'probe_wire.json').write_text(
        json.dumps(wire, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def _cfg():
    from novel.config import load_config
    return load_config()


def _prefix_identity(prefix: ProbePrefix) -> dict:
    return {'root': str(prefix.root), 'sid': prefix.sid,
            'source_identity': prefix.source_identity, 'root_sha1': prefix.root_sha1,
            'book_wire_sha256': prefix.book_wire_sha256,
            'prefix_chapters': list(range(1, prefix.prefix_through + 1)),
            'evidence_segments': len(prefix.evidence)}


def _sample_report(args, prefix: ProbePrefix, label: str, requests: list,
                   body: dict) -> dict:
    return {'tool': 'stage2_context_probe', 'sample': args.sample, 'label': label,
            'chapter_range': [args.from_ch, args.to_ch],
            'prefix': _prefix_identity(prefix),
            'requests': requests, **body}


def _persist(repository, label: str, report: dict, wires: dict) -> None:
    repository.write('runtime/probe_report.json', report)
    for name, wire in wires.items():
        repository.write(f'runtime/probe_wire_{name}.json', wire)


# ───────────────────────── 预览（0 成本） ─────────────────────────

def _preview_arm(args, prefix: ProbePrefix, arm: str) -> None:
    no = args.from_ch
    request = chapter_extraction_prompt(
        chapter=no, book_characters=prefix.book_wire['characters'], core=prefix.core,
        world_constraints=prefix.world_constraints, compiled=prefix.compiled,
        compiler=prefix.compiler, evidence=prefix.evidence)
    baseline = request.prompt
    prompt = baseline if arm == 'A' else arm_prompt(prefix, no, arm, prefix.compiled)
    cfg = _cfg()
    chat = str06_chat_request(cfg, f'ch{no:02d}', prompt, json_mode=True, system=SYS,
                              temperature=0.3, reasoning_effort=args.reasoning_effort)
    from novel.llm.budget import count_input_tokens
    print(f'── 臂 {arm}（第 {no} 章；与臂 A 的差异）──')
    diff = list(difflib.unified_diff(
        baseline.splitlines(), prompt.splitlines(), fromfile='arm-A', tofile=f'arm-{arm}',
        lineterm='', n=1))
    print('\n'.join(diff[2:]) if diff else '（与臂 A 逐字相同）')
    print(f'  prompt tokens（精确 tokenizer＋官方 framing）: {count_input_tokens(chat)}')
    identity = digest({'owner': f'ch{no:02d}', 'provider': cfg.base_url,
                       'request': chat.cache_payload()})
    print(f'  请求 identity: {identity}')
    record_path = prefix.book_dir / 'runtime' / f'request_{identity}.json'
    if arm == 'A' and record_path.is_file():
        print('  ✓ 与前轮已购请求逐字一致（前缀根存在同一 identity 的请求记录）')
    elif arm == 'A':
        print('  ⚠ 前缀根没有同一 identity 的已购请求记录：现行组装与前轮购买已漂移')
    if args.to_ch > args.from_ch:
        print(f'  ⚠ 第 {args.from_ch + 1}..{args.to_ch} 章的请求身份依赖本样本此前的响应，'
              '预览只给第 1 章的精确 token 数')


def _preview_arch(prefix: ProbePrefix, from_ch: int, to_ch: int,
                  reasoning_effort: str) -> None:
    prompt = arch_prompt(prefix, from_ch, to_ch)
    cfg = _cfg()
    chat = str06_chat_request(cfg, 'book', prompt, json_mode=True, system=SYS,
                              temperature=0.3, reasoning_effort=reasoning_effort)
    from novel.llm.budget import count_input_tokens
    print(f'── 架构探针（单次调用 ch{from_ch:02d}..ch{to_ch:02d}）──')
    print(f'  prompt tokens: {count_input_tokens(chat)}')
    print(f'  请求 identity: {digest({"owner": "book", "provider": cfg.base_url, "request": chat.cache_payload()})}')
    print('  ⚠ 单次多章输出有截断风险：finish_reason 与逐章编译结果是必读读数')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prefix-root', type=Path, default=DEFAULT_PREFIX_ROOT,
                    help='固定前缀来源根（只读；book 已修订 wire＋ch 原响应＋Stage1）')
    ap.add_argument('--prefix-through', type=int, default=2,
                    help='固定前缀含第 1..N 章（默认 2：book＋ch01–02）')
    ap.add_argument('--arm', choices=('A', 'B', 'C'), default=None,
                    help='上下文臂；不给则预览全部臂＋架构探针')
    ap.add_argument('--arch', action='store_true', help='架构探针（单次多章调用）')
    ap.add_argument('--from-ch', type=int, default=None, help='起始章（上下文臂默认 3）')
    ap.add_argument('--to-ch', type=int, default=None, help='结束章（上下文臂默认同起始章）')
    ap.add_argument('--sample', type=int, default=1, help='样本序号（每个样本一个独立根）')
    ap.add_argument('--baseline-original', action='store_true',
                    help='臂 A 样本 1：只读复证前轮已购原响应（零购买）')
    ap.add_argument('--out-root', type=Path, default=None,
                    help='样本根父目录（默认 outputs/str06_stage2_probe_<日期>）')
    ap.add_argument('--base', default=None,
                    help='付费账本的 base（默认取 --out-root 的父目录）')
    ap.add_argument('--reasoning-effort', default='high',
                    choices=('low', 'medium', 'high'), help='进请求身份（默认 high）')
    ap.add_argument('--execute', action='store_true',
                    help='真跑购买。不给时只做 0 成本预览，一个请求都不发')
    ap.add_argument('--yes', action='store_true', help='确认这一次会花钱（Q-10）')
    ap.add_argument('--allow-egress', action='store_true',
                    help='独立确认允许向 provider 外发小说正文（I-43，与 --yes 分开）')
    args = ap.parse_args(argv)
    if args.arm and args.arch:
        raise SystemExit('--arm 与 --arch 互斥：上下文形态与任务分解架构是两个问题')
    if args.baseline_original and args.execute:
        raise SystemExit('--baseline-original 是只读复证，不能与 --execute 同用')
    # 章范围默认：上下文臂第 3 章（QP-01 现场）；架构探针 ch01–05（预登记范围）。
    if args.from_ch is None:
        args.from_ch = 1 if args.arch else 3
    if args.to_ch is None:
        args.to_ch = 5 if args.arch else args.from_ch
    if not 1 <= args.from_ch <= args.to_ch:
        raise SystemExit('章范围非法：要求 1 <= --from-ch <= --to-ch')
    if args.prefix_through >= args.from_ch:
        raise SystemExit(
            f'固定前缀含到第 {args.prefix_through} 章，探针从第 {args.from_ch} 章起；'
            '前缀不得覆盖被探针的章')
    if args.execute and not (args.yes and args.allow_egress):
        raise SystemExit('--execute 需要同时给 --yes（付费）与 --allow-egress（外发）')
    out_root = args.out_root or Path('outputs') / f'str06_stage2_probe_{date.today():%Y%m%d}'
    prefix = load_probe_prefix(args.prefix_root, args.prefix_through)
    print(f'前缀根 {prefix.root}（sid={prefix.sid}，ch01–{prefix.prefix_through:02d} 已编译，'
          f'证据段 {len(prefix.evidence)}，源身份 {prefix.source_identity[:12]}…）')

    if args.baseline_original:
        report = run_baseline_original(args, prefix, out_root)
        _print_summary(report)
        return 0
    if not args.execute:
        if args.arm:
            _preview_arm(args, prefix, args.arm)
        else:
            for arm in ('A', 'B', 'C'):
                _preview_arm(args, prefix, arm)
            _preview_arch(prefix, 1, 5, args.reasoning_effort)
        label = f'{args.arm or "arch"}_{args.sample}'
        print(f'\n预览完成：未发任何请求、未写任何字节。真跑请加 '
              f'--execute --yes --allow-egress（样本根 {out_root / label}）。')
        return 0

    base = args.base or out_root.parent
    label = f'{args.arm or "arch"}_{args.sample}'
    boundary = assemble_paid_boundary(
        base=base, run_dir=out_root / label, source_identity=prefix.source_identity,
        root_sha1=prefix.root_sha1, yes=args.yes,
        allow_source_export=args.allow_egress)
    if args.arch:
        report = run_arch_sample(args, prefix, boundary, label)
    else:
        report = run_context_sample(args, prefix, boundary, label)
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    print(f"\n样本 {report.get('label') or report.get('mode')}："
          f"模式={report.get('mode')} 臂={report.get('arm')}")
    for request in report.get('requests') or []:
        print(f"  {request['owner']}: identity={request['identity'][:12]}… "
              f"prompt={request.get('prompt_tokens')} completion={request.get('completion_tokens')} "
              f"reasoning={request.get('reasoning_tokens')} finish={request.get('finish_reason')}")
    for chapter in report.get('chapters') or []:
        line = f"  ch{chapter.get('chapter')}: compiled={chapter.get('compiled')}"
        if chapter.get('compile_errors'):
            line += f" errors={chapter['compile_errors'][:2]}"
        print(line)
        for beat in chapter.get('sao_sao_beats') or []:
            print(f"    「嫂嫂」拍 {beat['beat']} participants={beat['participants']}")
        for rel in chapter.get('relation_effects') or []:
            print(f"    relation {rel['subject']}→{rel['object']} {rel['key']}={rel['value']!r}")
    for chapter in (report.get('readings') or {}).get('chapters') or []:
        print(f"  ch{chapter.get('chapter')}: compiled={chapter.get('compiled')} "
              f"beats={chapter.get('beat_count')}")
        for beat in chapter.get('bed_beats') or []:
            print(f"    床相关拍 {beat['beat']} reveals={bool(beat.get('reveals'))}")
        for beat in chapter.get('wardrobe_beats') or []:
            print(f"    衣柜拍 {beat['beat']} event={beat['event'][:40]}…")
    if (report.get('readings') or {}).get('truncated'):
        print('  ⚠ 架构探针响应被截断（finish_reason != stop）')


if __name__ == '__main__':
    raise SystemExit(main())
