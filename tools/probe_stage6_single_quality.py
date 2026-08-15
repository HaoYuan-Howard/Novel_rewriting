#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare one-shot Stage 6 hook generation with the existing three-candidate probe.

The script reuses the exact book packet assembled by ``probe_stage6_quality.py``.
It writes only isolated probe artifacts and never mutates production Stage 6 data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pydantic import Field

from contracts.schema.base import StrictModel
from novel import prov
from novel.llm.prompts import with_json_shape
from novel.llm.transport import ChatResponse
from tools.probe_stage6_quality import (
    APPROACHES,
    JudgeScore,
    _book_packet,
    _client,
    _dump,
    _json,
    _quality_score,
)


class SingleResponse(StrictModel):
    chosen_mechanism: Literal[
        'source_architecture',
        'payoff_preview',
        'scene_punchline',
        'hybrid',
    ]
    text: str = Field(min_length=20)
    rationale: str = Field(min_length=1)


class FiveWayJudgeResponse(StrictModel):
    ranking: list[str] = Field(min_length=5, max_length=5)
    scores: list[JudgeScore] = Field(min_length=5, max_length=5)
    winner_reason: str = Field(min_length=1)
    close_call: str = Field(min_length=1)


SINGLE_HINT = {
    'chosen_mechanism': 'source_architecture|payoff_preview|scene_punchline|hybrid',
    'text': 'str',
    'rationale': 'str',
}

JUDGE_HINT = {
    'ranking': ['A', 'B', 'C', 'D', 'E'],
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
    'close_call': 'str',
}


SINGLE_SYS = with_json_shape("""\
你是中文商业网文的资深开篇文案作者。第一目标只有一个：让目标读者看完文案后立刻继续读第一章。

你会拿到原始文案全文、完整故事事实、全书逐章故事地图、重写后的开篇正文，以及正文实际使用的声音和表面写法。原始文案是创作金标准：先判断它靠什么信息顺序、误导与揭示、反差、对白密度和最后一击吸引人，再用重写正文的声音写出更强的新文案。不得照抄原文连续 20 个内容字符。

剧透不是违规，也不是目的。能增强期待就使用后期兑现、结果倒叙或时间压缩；会把问题彻底说死就不用。允许为真实关系和真实事件重写台词、合成场面，但不得改变人物身份、关系、关键因果或故事结果，不得凭空增加改变读者理解的新设定。

不要写成编辑部梗概、设定清单或秘密罗列。避免“究竟会怎样”“是命还是债”“谁才是真正的……”等空泛收尾。最后一句必须落到具体人物欲望、危险、反转、笑点或行动。

长度以原始文案为基准，约为其 0.8～1.35 倍；尽量复现原作短句、短段、对白与停顿习惯。

你只交付一条最终文案。在心里分别探索以下机制，不要把三份草稿输出：
1. source_architecture：保住原文已经验证有效的结构和最后一击；
2. payoff_preview：挑全书最有吸引力的后期兑现或反转作预告；
3. scene_punchline：用具体场面或对白砸出核心关系，以人物反应或 punchline 收尾。

比较后选最能留住读者的一条；必要时可以混合机制。输出前逐句与原始文案比较：若更抽象、更像摘要、笑点或危险更弱、最后一击更空，就重写。只输出 JSON。""", SINGLE_HINT)


JUDGE_SYS = with_json_shape("""\
你是负责付费短篇与网文投放的主编。匿名评审五条开篇文案，目标是选出最能让目标读者继续读第一章的一条。

输入同时给出原始文案作为质量参照、重写后的开篇正文作为声线参照，以及全书事实作为矛盾核对面。原始文案不是默认冠军；它只说明这本书原本靠什么吸引人。不要猜候选来源。

逐条评分：
- read_on_desire：读完是否立刻想继续；
- body_voice_match：是否与重写正文像同一本书、同一个叙述者；
- concrete_scene_and_specificity：是否有具体人物、动作、对白和处境；
- curiosity_and_payoff_promise：是否给出值得期待的兑现，同时留下想追的缺口；
- hook_craft：起势、升级、节奏、反差和最后一击是否成熟；
- synopsis_penalty：越像剧情摘要、设定清单、秘密罗列或泛泛宣传语，分越高（0 最好，10 最差）。

剧透本身不加分也不扣分。允许重写台词、压缩时间和合成场面；只有改变人物身份、关系、关键因果或故事结果才记 material_contradictions。不要把“正文没有逐字出现这句台词”误判为矛盾。

不要偏爱信息最多、最安全或最规整的一条。空泛反问、抽象抒情和全知式剧情讲解要明确扣分。ranking 必须恰好覆盖 A/B/C/D/E，最优在前。只输出 JSON。""", JUDGE_HINT)


def _single_validator(resp: SingleResponse, _wire: ChatResponse) -> str | None:
    if not resp.text.strip():
        return 'text 不得为空'
    return None


def _judge_validator(labels: set[str]):
    def validate(resp: FiveWayJudgeResponse, _wire: ChatResponse) -> str | None:
        if set(resp.ranking) != labels or len(set(resp.ranking)) != len(labels):
            return f'ranking 必须恰好覆盖 {sorted(labels)}，实得 {resp.ranking}'
        scored = [score.label for score in resp.scores]
        if set(scored) != labels or len(set(scored)) != len(labels):
            return f'scores 必须恰好覆盖 {sorted(labels)}，实得 {scored}'
        return None

    return validate


def _book_from_three_way(three_out: Path) -> Path:
    generation = _json(three_out / 'generation.json')
    return Path(generation['book']).resolve()


def generate(three_out: Path, out_dir: Path, probe_root: Path) -> None:
    book = _book_from_three_way(three_out)
    packet, _hook, _drafts = _book_packet(book)
    user = (
        '下面是本书的完整创作包。先理解原始文案为何有效，在内部比较三种机制，'
        '最后只交付一条最强的新开篇文案。\n\n'
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )
    print(
        f'[single-generate] {book.name}: system={len(SINGLE_SYS)} chars, '
        f'user={len(user)} chars',
        flush=True,
    )
    client = _client(book, probe_root)
    response = client.ask_json(
        SINGLE_SYS,
        user,
        model_cls=SingleResponse,
        purpose='stage6.quality_probe.single_generate',
        max_tokens=64_000,
        validator=_single_validator,
    )
    artifact = {
        'book': str(book),
        'three_way_probe': str(three_out),
        'single': response.model_dump(mode='json'),
        'prompt': {
            'system_sha1': prov.fingerprint(SINGLE_SYS),
            'user_sha1': prov.fingerprint(user),
            'system_chars': len(SINGLE_SYS),
            'user_chars': len(user),
        },
        'model': client.model,
    }
    _dump(out_dir / 'single_generation.json', artifact)
    print(f'[single-generate] wrote {out_dir / "single_generation.json"}', flush=True)


def judge(three_out: Path, out_dir: Path, probe_root: Path) -> None:
    book = _book_from_three_way(three_out)
    three_generation = _json(three_out / 'generation.json')
    single_generation = _json(out_dir / 'single_generation.json')
    packet, _hook, drafts = _book_packet(book)

    candidates = [('current', three_generation['current_approved'])]
    candidates.extend(
        (candidate['approach'], candidate['text'])
        for candidate in three_generation['experimental']['candidates']
    )
    candidates.append(('single_final', single_generation['single']['text']))
    seed = int(hashlib.sha1((book.name + ':single').encode('utf-8')).hexdigest()[:8], 16)
    random.Random(seed).shuffle(candidates)
    labels = ['A', 'B', 'C', 'D', 'E']
    labelled = {label: {'text': text} for label, (_origin, text) in zip(labels, candidates)}
    label_origin = {label: origin for label, (origin, _text) in zip(labels, candidates)}

    judge_packet = {
        'title': packet['title'],
        'quality_reference_source_hook': packet['source_hook'],
        'rewritten_opening_voice_reference': [
            {'chapter': draft['output_ordinal'], 'text': draft['text']}
            for draft in drafts[:min(3, len(drafts))]
        ],
        'story_truth': packet['story_truth'],
        'anonymous_candidates': labelled,
    }
    user = (
        '请匿名比较 A/B/C/D/E。不要猜它们的来源，只按商业开篇文案质量裁决。\n\n'
        + json.dumps(judge_packet, ensure_ascii=False, indent=2)
    )
    print(
        f'[single-judge] {book.name}: system={len(JUDGE_SYS)} chars, '
        f'user={len(user)} chars',
        flush=True,
    )
    client = _client(book, probe_root)
    response = client.ask_json(
        JUDGE_SYS,
        user,
        model_cls=FiveWayJudgeResponse,
        purpose='stage6.quality_probe.single_judge',
        max_tokens=64_000,
        validator=_judge_validator(set(labels)),
    )

    scores = []
    score_by_origin: dict[str, float] = {}
    contradictions_by_origin: dict[str, list[str]] = {}
    for score in response.scores:
        row = score.model_dump(mode='json')
        origin = label_origin[score.label]
        quality_score = _quality_score(score)
        row['origin'] = origin
        row['quality_score'] = quality_score
        scores.append(row)
        score_by_origin[origin] = quality_score
        contradictions_by_origin[origin] = score.material_contradictions

    ranking_origins = [label_origin[label] for label in response.ranking]
    three_origins = set(APPROACHES)
    best_three = next(origin for origin in ranking_origins if origin in three_origins)
    artifact = {
        'book': str(book),
        'label_origin': label_origin,
        'ranking_labels': response.ranking,
        'ranking_origins': ranking_origins,
        'scores': scores,
        'winner_reason': response.winner_reason,
        'close_call': response.close_call,
        'comparison': {
            'single_rank': ranking_origins.index('single_final') + 1,
            'best_three_origin': best_three,
            'best_three_rank': ranking_origins.index(best_three) + 1,
            'single_quality_score': score_by_origin['single_final'],
            'best_three_quality_score': score_by_origin[best_three],
            'single_minus_best_three': round(
                score_by_origin['single_final'] - score_by_origin[best_three], 2
            ),
            'single_material_contradictions': contradictions_by_origin['single_final'],
        },
        'prompt': {
            'system_sha1': prov.fingerprint(JUDGE_SYS),
            'user_sha1': prov.fingerprint(user),
            'system_chars': len(JUDGE_SYS),
            'user_chars': len(user),
        },
        'model': client.model,
    }
    _dump(out_dir / 'single_judge.json', artifact)
    print(f'[single-judge] ranking={ranking_origins}', flush=True)
    print(f'[single-judge] comparison={artifact["comparison"]}', flush=True)
    print(f'[single-judge] wrote {out_dir / "single_judge.json"}', flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('generate', 'judge'))
    parser.add_argument('--three-out', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--probe-root', type=Path, required=True)
    args = parser.parse_args(argv)
    three_out = args.three_out.resolve()
    out_dir = args.out.resolve()
    probe_root = args.probe_root.resolve()
    if args.action == 'generate':
        generate(three_out, out_dir, probe_root)
    else:
        judge(three_out, out_dir, probe_root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
