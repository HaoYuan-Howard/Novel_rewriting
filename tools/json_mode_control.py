#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""json_mode_control.py —— 「flash@high 的空正文」是模型边界，还是 `json_object` 的交互？

历史裁决与实付读数见 `docs/DECISIONS.md` 2026-08-22。现役对照面是
``stage4.design`` 的 ``BlindPlanResponse``：同一份去序提示词、
同一组参数（`deepseek-v4-flash` / `reasoning_effort=high` / `thinking=True` /
`max_tokens=96000`），**只翻 `json_mode` 一位**，重复采样 N 次。

【为什么走 `ask_text` 而不是自己直连传输层】
`ask_text` 与 `ask_json` 共用同一套单配置采样/传输重试、同一个缓存键、同一份账，唯一的差别就是
`json_mode=False` ⇒ **被测变量恰好只有一个**。自己直连传输层要重造重试、记账、
留证三条路，而重造出来的那份与生产路的差异会变成结论里说不清的部分。
副作用还都是想要的：每次 wire 都记成本账；空正文会被判成 `answer_lost` 闸并
**自动留证**（`<书目录>/_answer_lost/`）——这次的核心证据正是那份 `reasoning` 全文。

【安全边界】
 · 不带 `--acknowledge-paid-network` 一次都不发；
 · wire 调用有硬上限（`--max-wire`），驱动循环与传输层各守一道，到顶即停；
 · 缓存目录必须显式给（`--cache-dir`）且强制 `CachePolicy.refresh()`
   ⇒ 既读不到既有缓存（否则实验会静默不发生），也污染不了生产缓存；
 · 模型与配置钉死：`('high', True)` 且关闭 Flash→Pro 兜底；任一切换都会把
   「flash@high 到底行不行」的问题换掉。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel.config import load_config                                  # noqa: E402
from novel.llm.cache import CachePolicy, ResponseCache                # noqa: E402
from novel.llm.client import (                                        # noqa: E402
    MAX_SAMPLES_PER_RUNG, LlmClient, LlmFailed, _strip_fences,
)
from novel.llm.transport import (                                     # noqa: E402
    ChatRequest, ChatResponse, DeepSeekTransport, Transport,
)
from novel.run import RunContext                                      # noqa: E402
from contracts.schema.stage4 import BlindPlanResponse                 # noqa: E402
from novel.stages.stage4 import plan as planmod, prompts              # noqa: E402

#: 被测臂钉死单档；client 构造时另显式关闭模型兜底。
ARM_RUNGS = (('high', True),)


class WireLimitReached(RuntimeError):
    """硬上限的最后一道闸。驱动循环先算过一遍，这里只兜底。"""


class RecordingTransport:
    """真传输层外面量一层：**每一次 wire 的形状**都留下来，正文不进报告。

    ⚠ 报告里只存长度、`usage`、`finish_reason` 与「解析得动吗」这几个读数。
    正文（尤其是空正文那次的 `reasoning`）由生产路的留证机制落到书目录下，
    这里再存一份等于把同一段正文写进第二个地方。
    """

    def __init__(self, delegate: Transport, limit: int) -> None:
        self.delegate = delegate
        self.limit = limit
        self.rows: list[dict[str, Any]] = []

    def send(self, req: ChatRequest) -> ChatResponse:
        if len(self.rows) >= self.limit:
            raise WireLimitReached(f'已达 wire 硬上限 {self.limit}，停在这里')
        resp = self.delegate.send(req)
        self.rows.append(_measure(len(self.rows) + 1, req, resp))
        _echo(self.rows[-1])
        return resp


def _measure(seq: int, req: ChatRequest, resp: ChatResponse) -> dict[str, Any]:
    """一次 wire 的读数。**主结局是「正文空不空」**，次结局是「还拿不拿得到合法盲设计」。"""
    content = _strip_fences(resp.content)
    parsed: Any = None
    parse_error: str | None = None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        parse_error = f'{type(exc).__name__}: {exc}'
    shape_error: str | None = None
    if parse_error is None:
        try:
            BlindPlanResponse.model_validate(parsed)
        except Exception as exc:                       # noqa: BLE001 —— 只记形状
            shape_error = f'{type(exc).__name__}: {str(exc)[:200]}'
    u = resp.usage or {}
    return {
        'seq': seq,
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'json_mode': req.json_mode,
        'reasoning_effort': req.reasoning_effort,
        'thinking': req.thinking,
        'max_tokens': req.max_tokens,
        'model': req.model,
        'finish_reason': resp.finish_reason,
        'content_chars': len(resp.content or ''),
        'reasoning_chars': len(resp.reasoning or ''),
        'prompt_tokens': u.get('prompt_tokens'),
        'completion_tokens': u.get('completion_tokens'),
        'reasoning_tokens': (u.get('reasoning_tokens')
                             or (u.get('completion_tokens_details') or {})
                             .get('reasoning_tokens')),
        # 主结局。⚠ 与 `client._answer_lost` 同一把尺子：空**且**非截断才是「答案流丢失」。
        'empty_body': not (resp.content or '').strip(),
        'truncated': resp.finish_reason == 'length',
        # 次结局：不开 json_object，还拿不拿得到合法 JSON / 合法盲设计。
        'parses_as_json': parse_error is None,
        'parse_error': parse_error,
        'validates_as_blind_design': parse_error is None and shape_error is None,
        'shape_error': shape_error,
    }


def _verdict(row: dict[str, Any]) -> str:
    if row['empty_body']:
        return '截断且空' if row['truncated'] else '空正文（答案流丢失）'
    if row['validates_as_blind_design']:
        return '合法盲设计'
    return 'JSON 但不合契约' if row['parses_as_json'] else '非 JSON'


def _echo(row: dict[str, Any]) -> None:
    print(f'  #{row["seq"]} finish={row["finish_reason"]} '
          f'content={row["content_chars"]}字 reasoning={row["reasoning_chars"]}字 '
          f'comp={row["completion_tokens"]} reas={row["reasoning_tokens"]} '
          f'→ {_verdict(row)}', flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True, help='Output 根（含各书目录的那一层）')
    ap.add_argument('--sid', required=True)
    ap.add_argument('--cache-dir', required=True, help='本次实验专用，必须与生产缓存分开')
    ap.add_argument('--out', required=True, help='报告落点（.json）')
    ap.add_argument('--samples', type=int, default=8, help='目标样本数（wire 次数）')
    ap.add_argument('--max-wire', type=int, default=10, help='wire 硬上限')
    ap.add_argument('--json-mode', choices=('off', 'on'), default='off',
                    help='被测的那一位。off = 对照臂（不发 response_format）')
    ap.add_argument('--acknowledge-paid-network', action='store_true')
    args = ap.parse_args(argv)

    if not args.acknowledge_paid_network:
        print('拒绝：这是真花钱的探针，必须显式带 --acknowledge-paid-network')
        return 2
    if args.samples > args.max_wire:
        print(f'拒绝：--samples {args.samples} > --max-wire {args.max_wire}')
        return 2

    cfg = load_config()
    ctx = RunContext.open(Path(args.base), args.sid, 'stage4.design', paid=True,
                          cache=CachePolicy.refresh())
    system, user = planmod.build_design_prompt(ctx)

    rec = RecordingTransport(DeepSeekTransport(cfg), limit=args.max_wire)
    client = LlmClient(ctx, rec, cache=ResponseCache(Path(args.cache_dir)),
                       model=cfg.model, thinking_mode=cfg.thinking_mode,
                       max_retries=cfg.max_retries, backoff_base=cfg.backoff_base,
                       concurrency=1, enable_model_fallback=False)

    print(f'臂：json_mode={args.json_mode} · model={cfg.model} · 显式配置={ARM_RUNGS} '
          f'· max_tokens={prompts.BLIND_DESIGN_MAX_TOKENS}')
    print(f'system {len(system)} 字 / user {len(user)} 字 · 目标 {args.samples} 样本 '
          f'· wire 硬上限 {args.max_wire}', flush=True)

    # ⚠ 一次 `ask_*` 最多烧 `MAX_SAMPLES_PER_RUNG` 次 wire（被拒要同档重采）
    #   ⇒ 剩余额度不够一整轮就不再起新的一轮，别让硬上限在半路上截断一次调用。
    while (len(rec.rows) < args.samples
           and len(rec.rows) + MAX_SAMPLES_PER_RUNG <= args.max_wire):
        kw: dict[str, Any] = dict(
            purpose=f'{ctx.node}:json_mode_control',
            max_tokens=prompts.BLIND_DESIGN_MAX_TOKENS, rungs=ARM_RUNGS,
            cache_policy=CachePolicy.refresh())
        try:
            if args.json_mode == 'on':
                client.ask_json(system, user, model_cls=BlindPlanResponse, **kw)
            else:
                client.ask_text(system, user, **kw)
        except LlmFailed as exc:
            print(f'  （这一轮走完单档仍无可用响应：{str(exc)[:70]}…）', flush=True)
        except WireLimitReached as exc:
            print(f'  {exc}')
            break

    rows = rec.rows
    empty = [r for r in rows if r['empty_body']]
    summary = {
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'arm': f'json_mode={args.json_mode}',
        'model': cfg.model,
        'rungs': [list(r) for r in ARM_RUNGS],
        'max_tokens': prompts.BLIND_DESIGN_MAX_TOKENS,
        'sid': args.sid,
        'base': str(args.base),
        'system_chars': len(system),
        'user_chars': len(user),
        'samples': len(rows),
        'empty_bodies': len(empty),
        'parses_as_json': sum(1 for r in rows if r['parses_as_json']),
        'validates_as_blind_design': sum(
            1 for r in rows if r['validates_as_blind_design']),
        'prompt_tokens_total': sum(r['prompt_tokens'] or 0 for r in rows),
        'completion_tokens_total': sum(r['completion_tokens'] or 0 for r in rows),
        'reasoning_tokens_total': sum(r['reasoning_tokens'] or 0 for r in rows),
        'rows': rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')

    n = len(rows)
    print(f'\n=== 臂 json_mode={args.json_mode} ===')
    if n:
        print(f'  样本 {n} · 空正文 {len(empty)}（{len(empty) / n * 100:.0f}%）')
        print(f'  解析得动 JSON {summary["parses_as_json"]}/{n}'
              f' · 合 BlindPlanResponse 契约 '
              f'{summary["validates_as_blind_design"]}/{n}')
    else:
        print('  样本 0（一次都没发出去）')
    print(f'  报告 → {out}')
    print(f'  空响应的 reasoning 全文 → {ctx.novel_dir / "_answer_lost"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
