#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated Stage 6 quality probe.

This script deliberately does not import or mutate the production Stage 6 prompt,
artifact, or rule registry.  It generates an experimental hook beside existing
artifacts and then asks a blinded editorial judge to compare it with the currently
approved hook.  Probe outputs live under the caller-provided directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Literal

# 支持从仓库根目录直接执行 ``python tools/probe_stage6_quality.py``，而不要求调用者
# 预先设置 PYTHONPATH；模块方式执行仍走同一份代码。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pydantic import Field

from contracts.schema.base import StrictModel
from novel import prov
from novel.config import load_config
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse, DeepSeekTransport
from novel.run import RunContext


APPROACHES = ('source_architecture', 'payoff_preview', 'scene_punchline')


class ProbeCandidate(StrictModel):
    approach: Literal['source_architecture', 'payoff_preview', 'scene_punchline']
    text: str = Field(min_length=20)


class ProbeResponse(StrictModel):
    candidates: list[ProbeCandidate] = Field(min_length=3, max_length=3)
    rationale: str = Field(min_length=1)


class JudgeScore(StrictModel):
    label: str = Field(min_length=1)
    read_on_desire: int = Field(ge=1, le=10)
    body_voice_match: int = Field(ge=1, le=10)
    concrete_scene_and_specificity: int = Field(ge=1, le=10)
    curiosity_and_payoff_promise: int = Field(ge=1, le=10)
    hook_craft: int = Field(ge=1, le=10)
    synopsis_penalty: int = Field(ge=0, le=10)
    material_contradictions: list[str]
    concise_reason: str = Field(min_length=1)


class JudgeResponse(StrictModel):
    ranking: list[str] = Field(min_length=4, max_length=4)
    scores: list[JudgeScore] = Field(min_length=4, max_length=4)
    winner_reason: str = Field(min_length=1)
    source_reference_gap: str = Field(min_length=1)


GEN_HINT = {
    'candidates': [{'approach': '|'.join(APPROACHES), 'text': 'str'}],
    'rationale': 'str',
}

JUDGE_HINT = {
    'ranking': ['A', 'B', 'C', 'D'],
    'scores': [{
        'label': 'A',
        'read_on_desire': 'int 1..10',
        'body_voice_match': 'int 1..10',
        'concrete_scene_and_specificity': 'int 1..10',
        'curiosity_and_payoff_promise': 'int 1..10',
        'hook_craft': 'int 1..10',
        'synopsis_penalty': 'int 0..10',
        'material_contradictions': ['str'],
        'concise_reason': 'str',
    }],
    'winner_reason': 'str',
    'source_reference_gap': 'str',
}


GEN_SYS = with_json_shape("""\
你是中文商业网文的资深开篇文案作者。你的第一目标只有一个：让目标读者看完文案后立刻继续读第一章。

你会看到原始文案、完整故事事实、全书逐章故事地图、重写后的开篇正文，以及正文实际使用的声音和写作手法。你知道全书，不等于必须把全书都说出来；由你选择披露多少。剧透不是违规，也不是目的，只是一种吸引读者的工具：能增强期待就用，会把悬念彻底说死就不用。

【原始文案是创作金标准】
- 保留它真正有效的钩子机制：信息出场顺序、误导与揭示、喜剧或危险节拍、对白密度、反差、最后一击的功能。
- 不得照抄其措辞；不要出现与原始文案连续 20 个内容字符相同的片段。短小的通用称谓和必要设定可以自然重合。
- 新文案必须与重写正文像同一本书，优先执行给出的逐章声音与 surface_habits，不要写成编辑部剧情简介。

【文案体裁自由】
- 允许压缩时间线、把数章合成一个高张力预告、从后期结果倒叙、为真实关系和真实事件重写新台词、用一个合成场面承载真实矛盾。
- 这些自由不能改变人物身份、关系、关键因果或故事结果；不能凭空增加会改变读者理解的新设定。
- 不要把秘密清单逐条翻译成摘要。每条候选只选一个最强承诺，围绕它形成起势、升级、最后一击。
- 避免空泛收尾，如“究竟会怎样”“是命还是债”“谁才是真正的……”；最后一句必须带具体人物欲望、危险、反转、笑点或行动。
- 长度以原始文案为基准，约为其 0.8~1.35 倍；按原作的短句、短段、对白与停顿习惯写。

产出三条真正不同的候选：
1. source_architecture：最大程度保住原始文案已经验证有效的结构与最后一击，替换为重写正文的声线和表述。
2. payoff_preview：从全书挑最有吸引力的后期兑现或反转作预告，再制造“怎么走到这一步”的阅读欲；不受开篇章节限制。
3. scene_punchline：用一个可视、可听的具体场面或对白把核心关系砸出来，以人物反应或 punchline 收尾。

输出前在心里逐条与原始文案比较：若更抽象、更像摘要、笑点/危险更弱或最后一句更空，就重写。只输出 JSON。""", GEN_HINT)


JUDGE_SYS = with_json_shape("""\
你是负责付费短篇与网文投放的主编。匿名评审四条开篇文案，目标是选出最能让目标读者继续读第一章的一条。

输入还会给出原始文案作为质量参照、重写后的开篇正文作为声线参照，以及全书事实作为矛盾核对面。原始文案不是默认冠军；它只告诉你这本书原本靠什么吸引人。

逐条评分：
- read_on_desire：读完是否立刻想继续；
- body_voice_match：是否与重写正文像同一本书、同一个叙述者；
- concrete_scene_and_specificity：是否有具体人物、动作、对白、处境，而非抽象概括；
- curiosity_and_payoff_promise：是否既给出值得期待的兑现，又留下真正想追的缺口；
- hook_craft：起势、升级、节奏、反差、最后一击是否成熟；
- synopsis_penalty：越像剧情摘要、设定清单、秘密罗列或泛泛宣传语，分越高（0 最好，10 最差）。

剧透本身不加分也不扣分，只判断它是增强阅读欲还是把问题彻底说完。文案体裁允许重写台词、压缩时间和合成场面；只有改变人物身份、关系、关键因果或故事结果才记 material_contradictions。不要把“正文没有逐字出现这句台词”误判为矛盾。

不要偏爱信息最多、最安全或最规整的一条。空泛反问、抽象抒情和全知式剧情讲解要明确扣分。ranking 必须恰好覆盖 A/B/C/D，最优在前。只输出 JSON。""", JUDGE_HINT)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _drafts(book: Path, hook: dict) -> list[dict]:
    drafts = []
    for rel, judged in (hook.get('judged') or {}).items():
        art = _json(book / Path(rel))
        text = '\n\n'.join(
            str(segment.get('text') or '').strip()
            for segment in (art.get('segments') or [])
            if str(segment.get('text') or '').strip()
        ).strip()
        drafts.append({
            'output_ordinal': judged.get('output_ordinal') or art.get('output_ordinal'),
            'rel': rel,
            'text': text,
        })
    return sorted(drafts, key=lambda x: int(x['output_ordinal'] or 0))


def _story_map(writer_context: dict) -> list[dict]:
    def goal(value: object) -> dict:
        raw = value if isinstance(value, dict) else {}
        return {'intent': raw.get('intent'), 'fact_refs': raw.get('fact_refs') or []}

    out = []
    for ch in writer_context.get('chapters') or []:
        events = []
        segments = []
        for segment in ch.get('segments') or []:
            segment_events = [
                str(e) for e in (segment.get('events') or []) if str(e).strip()
            ]
            events.extend(segment_events)
            segments.append({'slot': segment.get('slot'), 'events': segment_events})
        out.append({
            'chapter': ch.get('output_ordinal'),
            'from_source_ordinals': ch.get('from_source_ordinals') or [],
            'chapter_goal': goal(ch.get('chapter_goal')),
            'open_hook_goal': goal(ch.get('open_hook_goal')),
            'events': events,
            'segments': segments,
            'end_hook_goal': goal(ch.get('end_hook_goal')),
        })
    return out


def _style_packet(style: dict, lead_upto: int) -> dict:
    source_limit = max(1, min(int(lead_upto or 1), 3))
    chapter_styles = [
        item for item in (style.get('chapter_styles') or [])
        if int(item.get('source_ordinal') or 0) <= source_limit
    ]
    return {
        'hook_style': style.get('hook_style'),
        'book_voice': style.get('voice'),
        'book_surface_habits': style.get('surface_habits'),
        'opening_chapter_styles': chapter_styles,
        'measurable': style.get('measurable'),
    }


def _book_packet(book: Path) -> tuple[dict, dict, list[dict]]:
    hook = _json(book / 'stage6' / 'hook.json')
    source = _json(book / 'stage1' / 'hook_copy.json')
    grounding = _json(book / 'stage2' / 'global_grounding.json')
    style = _json(book / 'stage3' / 'style_grounding.json')
    writer_context = _json(book / 'stage4' / 'writer_context.json')
    drafts = _drafts(book, hook)
    lead_upto = int(hook.get('lead_upto') or 1)
    packet = {
        'title': book.name.rsplit('_', 1)[0],
        'source_hook': source.get('text') or '',
        'source_hook_target_chars': ((style.get('measurable') or {}).get('hook') or {}).get('chars'),
        'style': _style_packet(style, lead_upto),
        'story_truth': {
            'entities': grounding.get('entities'),
            'canon': grounding.get('canon'),
            'arc': grounding.get('arc'),
        },
        'whole_story_map': _story_map(writer_context),
        'rewritten_opening': [
            {'chapter': d['output_ordinal'], 'text': d['text']}
            for d in drafts if int(d['output_ordinal'] or 0) <= lead_upto
        ],
    }
    return packet, hook, drafts


def _client(book: Path, probe_root: Path) -> LlmClient:
    cfg = load_config()
    ledger = CostLedger(path=probe_root / '_service' / 'ledger.jsonl')
    ctx = RunContext(
        novel_dir=book,
        node='stage6.hook',
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
    )
    return LlmClient(
        ctx,
        DeepSeekTransport(cfg),
        cache=ResponseCache(cfg.cache_dir),
        model=cfg.model,
        thinking_mode=cfg.thinking_mode,
        max_retries=cfg.max_retries,
        backoff_base=cfg.backoff_base,
        concurrency=cfg.concurrency,
    )


def _generation_validator(resp: ProbeResponse, _wire: ChatResponse) -> str | None:
    approaches = [c.approach for c in resp.candidates]
    if len(set(approaches)) != 3 or set(approaches) != set(APPROACHES):
        return f'candidates 必须各覆盖一次 {APPROACHES}，实得 {approaches}'
    texts = [c.text.strip() for c in resp.candidates]
    if len(set(texts)) != 3:
        return '三个候选文本不得重复'
    return None


def _judge_validator(labels: set[str]):
    def validate(resp: JudgeResponse, _wire: ChatResponse) -> str | None:
        if set(resp.ranking) != labels or len(set(resp.ranking)) != len(labels):
            return f'ranking 必须恰好覆盖 {sorted(labels)}，实得 {resp.ranking}'
        got = [s.label for s in resp.scores]
        if set(got) != labels or len(set(got)) != len(labels):
            return f'scores 必须恰好覆盖 {sorted(labels)}，实得 {got}'
        return None
    return validate


def generate(book: Path, out_dir: Path, probe_root: Path) -> None:
    packet, hook, _drafts_list = _book_packet(book)
    user = ('下面是本书的完整创作包。先理解原始文案为何有效，再写三条新的开篇文案。\n\n'
            + json.dumps(packet, ensure_ascii=False, indent=2))
    print(f'[generate] {book.name}: system={len(GEN_SYS)} chars, user={len(user)} chars', flush=True)
    client = _client(book, probe_root)
    resp = client.ask_json(
        GEN_SYS,
        user,
        model_cls=ProbeResponse,
        purpose='stage6.quality_probe.generate',
        max_tokens=64_000,
        validator=_generation_validator,
    )
    artifact = {
        'book': str(book),
        'source_hook': packet['source_hook'],
        'current_approved': (hook.get('approved') or {}).get('text'),
        'experimental': resp.model_dump(mode='json'),
        'prompt': {
            'system_sha1': prov.fingerprint(GEN_SYS),
            'user_sha1': prov.fingerprint(user),
            'system_chars': len(GEN_SYS),
            'user_chars': len(user),
        },
        'model': client.model,
    }
    _dump(out_dir / 'generation.json', artifact)
    print(f'[generate] wrote {out_dir / "generation.json"}', flush=True)


def _quality_score(score: JudgeScore) -> float:
    return round(
        0.30 * score.read_on_desire
        + 0.20 * score.body_voice_match
        + 0.15 * score.concrete_scene_and_specificity
        + 0.20 * score.curiosity_and_payoff_promise
        + 0.15 * score.hook_craft
        - 0.10 * score.synopsis_penalty,
        2,
    )


def judge(book: Path, out_dir: Path, probe_root: Path) -> None:
    generation = _json(out_dir / 'generation.json')
    packet, _hook, drafts = _book_packet(book)
    candidates = [('current', generation['current_approved'])]
    candidates.extend((c['approach'], c['text']) for c in generation['experimental']['candidates'])
    seed = int(hashlib.sha1(book.name.encode('utf-8')).hexdigest()[:8], 16)
    random.Random(seed).shuffle(candidates)
    labels = ['A', 'B', 'C', 'D']
    labelled = {label: {'text': text} for label, (_origin, text) in zip(labels, candidates)}
    label_origin = {label: origin for label, (origin, _text) in zip(labels, candidates)}
    judge_packet = {
        'title': packet['title'],
        'quality_reference_source_hook': packet['source_hook'],
        'rewritten_opening_voice_reference': [
            {'chapter': d['output_ordinal'], 'text': d['text']}
            for d in drafts[:min(3, len(drafts))]
        ],
        'story_truth': packet['story_truth'],
        'anonymous_candidates': labelled,
    }
    user = ('请匿名比较 A/B/C/D。不要猜它们的来源，只按商业开篇文案质量裁决。\n\n'
            + json.dumps(judge_packet, ensure_ascii=False, indent=2))
    print(f'[judge] {book.name}: system={len(JUDGE_SYS)} chars, user={len(user)} chars', flush=True)
    client = _client(book, probe_root)
    resp = client.ask_json(
        JUDGE_SYS,
        user,
        model_cls=JudgeResponse,
        purpose='stage6.quality_probe.judge',
        max_tokens=64_000,
        validator=_judge_validator(set(labels)),
    )
    scores = []
    for score in resp.scores:
        row = score.model_dump(mode='json')
        row['origin'] = label_origin[score.label]
        row['quality_score'] = _quality_score(score)
        scores.append(row)
    artifact = {
        'book': str(book),
        'label_origin': label_origin,
        'ranking_labels': resp.ranking,
        'ranking_origins': [label_origin[label] for label in resp.ranking],
        'scores': scores,
        'winner_reason': resp.winner_reason,
        'source_reference_gap': resp.source_reference_gap,
        'prompt': {
            'system_sha1': prov.fingerprint(JUDGE_SYS),
            'user_sha1': prov.fingerprint(user),
            'system_chars': len(JUDGE_SYS),
            'user_chars': len(user),
        },
        'model': client.model,
    }
    _dump(out_dir / 'judge.json', artifact)
    print(f'[judge] ranking={artifact["ranking_origins"]}', flush=True)
    print(f'[judge] wrote {out_dir / "judge.json"}', flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('generate', 'judge'))
    ap.add_argument('--book', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--probe-root', type=Path, required=True)
    args = ap.parse_args(argv)
    book = args.book.resolve()
    out = args.out.resolve()
    probe_root = args.probe_root.resolve()
    if args.action == 'generate':
        generate(book, out, probe_root)
    else:
        judge(book, out, probe_root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
