#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨 run（臂）对比读数器。**只读**：不写盘、不发请求、不产生新判定。

给模型/思考 A/B 用：N 个 `--arm`（每臂一个 base，同一个 sid），把
  ① 成本（账本 token 事实，生成/指挥分账）        ② 墙钟（span 分段 + Σlatency 双口径）
  ③ 质量闸（各层 badge / 四态计数 / HARD 残留） ④ `sev='metric'` 全量读数
  ⑤ inbox 全类                                   ⑥ 账本健康（限流/超时/重试）
  ⑦ 成书统计
摆成「列=臂」的 markdown 矩阵。**它是读数器不是判官**（R-13.6）：判定语义只归
check 报告；质量细排名一律不出——单次跑不可归因（`docs/DECISIONS.md`）。

【F8a：判官面整面删除】语义审判（`stage6.semantic` 判官节点与各臂
`semantic_{n}.json` 产物）随 2026-09-15 全自动裁决退役，新链无对应物
（STR-06 的语义未评以 `unver` 披露，不再有判官分账与审判摘要）⇒
`JUDGE_NODES`、`semantic_digest`、`diff_semantic` 与 `--compare-semantic`
一并删除；成本只剩指挥（Commander role）与生成两类。

⚠ 钱：账本只有 token 事实（Q-10 明令不存价格）。`--rates` 传一张**带日期**的费率表
  JSON 时才折算；折算只进 stdout/实验报告，不落任何仓内产物。
⚠ Σlatency_ms 在 32 并发下**远大于墙钟**（E2E_VALIDATION:257）。span 口径才接近墙钟：
  同 (sid, node) 的调用按 `at` 排序，间隔 > `--gap-seconds`（默认 600 = DEFAULT_TIMEOUT，
  响应到达间隔的合法上限）视为两次跑、分段计——防「修复回路重跑被并进一段」。
  span 量不到进程启动/上下文拼装/落盘校验，外层计时（timings_<sid>.md）是辅口径。

用法：
  python -X utf8 tools/ab_compare.py --sid <sid> \\
      --arm pro_think=<abs>/ab/pro_think --arm flash_think=... \\
      [--rates rates.json] [--gap-seconds 600] [--source 源.txt] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import INBOX_KIND, STAGES          # noqa: E402
from novel import paths, prov                                # noqa: E402
from novel.llm.ledger import ledger_path                     # noqa: E402
from novel.orchestrator.nodes import check_node              # noqa: E402
from novel.orchestrator.state import readonly_ctx            # noqa: E402
from novel.stages import check_only                          # noqa: E402
from novel.orchestrator.inbox_projection import inbox_view                     # noqa: E402

#: 章头两种实测形态：`第N章/回` 行头（可带标题），或**独行裸数字**（成书实测是后者）。
_CH_MARK = re.compile(r'^(?:第[0-9一二三四五六七八九十百千两]{1,7}[章回]|\d{1,4}\s*$)', re.M)


# ── 账本 ─────────────────────────────────────────────────────────────────────

def _cost_group(row: dict) -> str:
    """账本行的展示分组；Commander 是 role，不伪装成 ``NODES`` 成员。"""
    if row.get('role') == 'commander':
        if row.get('node') is not None:
            raise ValueError('commander 成本行的 node 必须为 null')
        return 'role:commander'
    node = row.get('node')
    if not isinstance(node, str) or not node:
        raise ValueError('非 commander 成本行必须带非空 node')
    return node

def load_rows(base: str, sid: str) -> list[dict]:
    """读 `<base>/_service/ledger.jsonl` 里这本书的行。容忍末尾半行（进程被杀）。"""
    p = Path(ledger_path(base))
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                       # 半行只可能在末尾；跳过不叫修复
        if row.get('sid') == sid:
            rows.append(row)
    return rows


def _tok(rows: list[dict], key: str) -> tuple[int, int, int]:
    """(合计, 有值行数, null 行数)。**None 与 0 分开**：null = 响应没给 usage。"""
    vals = [r.get(key) for r in rows]
    known = [v for v in vals if v is not None]
    return sum(known), len(known), len(vals) - len(known)


def cost_table(rows: list[dict]) -> dict:
    """按 node / Commander role 聚成本事实；生成与指挥分账。"""
    out: dict[str, dict] = {}
    groups = {_cost_group(row) for row in rows}
    for node in sorted(groups):
        nr = [row for row in rows if _cost_group(row) == node]
        wire = [r for r in nr if not r.get('cached')]
        p, p_n, p_null = _tok(wire, 'prompt_tokens')
        ph, ph_n, ph_null = _tok(wire, 'prompt_cache_hit_tokens')
        pm, pm_n, pm_null = _tok(wire, 'prompt_cache_miss_tokens')
        c, c_n, c_null = _tok(wire, 'completion_tokens')
        g, g_n, g_null = _tok(wire, 'reasoning_tokens')
        cache_known_rows = sum(
            row.get('prompt_cache_hit_tokens') is not None
            and row.get('prompt_cache_miss_tokens') is not None
            for row in wire)
        cache_partial_rows = sum(
            (row.get('prompt_cache_hit_tokens') is None)
            != (row.get('prompt_cache_miss_tokens') is None)
            for row in wire)
        cache_mismatch_rows = sum(
            row.get('prompt_tokens') is not None
            and row.get('prompt_cache_hit_tokens') is not None
            and row.get('prompt_cache_miss_tokens') is not None
            and (row['prompt_cache_hit_tokens'] + row['prompt_cache_miss_tokens']
                 != row['prompt_tokens'])
            for row in wire)
        by_purpose: dict[str, list[tuple[object, object, object]]] = defaultdict(list)
        for row in wire:
            key = str(row.get('purpose') or '（purpose 未记录）')
            configuration = (
                row.get('model'), row.get('reasoning_effort'), row.get('thinking'))
            # 网络重试/同配置重采会重复同一组；历史 effort 降档与现役 Flash→Pro
            # 都是配置转换。JSON 键保留 fallback_*，避免打碎已有报告读者。
            if not by_purpose[key] or by_purpose[key][-1] != configuration:
                by_purpose[key].append(configuration)
        fallback_purposes = sum(len(configs) > 1 for configs in by_purpose.values())
        fallback_steps = sum(max(0, len(configs) - 1) for configs in by_purpose.values())
        cache_total = ph + pm
        out[node] = {
            'models': sorted({r.get('model') for r in nr if r.get('model')}),
            'calls_made': len(wire),
            'calls_cached': len(nr) - len(wire),
            'prompt_tokens': p, 'prompt_null_rows': p_null,
            'prompt_cache_hit_tokens': ph,
            'prompt_cache_hit_null_rows': ph_null,
            'prompt_cache_miss_tokens': pm,
            'prompt_cache_miss_null_rows': pm_null,
            'prompt_cache_known_rows': cache_known_rows,
            'prompt_cache_partial_rows': cache_partial_rows,
            'prompt_cache_mismatch_rows': cache_mismatch_rows,
            'prompt_cache_hit_rate': (ph / cache_total if cache_total else None),
            'completion_tokens': c, 'completion_null_rows': c_null,
            'reasoning_tokens': g, 'reasoning_null_rows': g_null,
            # Provider completion is the total output count; reasoning is its diagnostic
            # subset and must never be added once more for totals or money.
            'completion_includes_reasoning': True,
            'efforts': dict(Counter(
                str(r.get('reasoning_effort') or 'null') for r in wire)),
            'thinking': dict(Counter(
                'null' if r.get('thinking') is None else str(bool(r.get('thinking'))).lower()
                for r in wire)),
            'fallback_purposes': fallback_purposes,
            'fallback_steps': fallback_steps,
            'conditional_surface_repair_calls': sum(
                str(r.get('purpose') or '').endswith(
                    '.surface_habits_targeted_repair')
                for r in wire),
            'outcomes': dict(Counter(str(r.get('outcome') or 'null') for r in wire)),
            'errors': dict(Counter(r.get('error_kind') for r in nr
                                   if r.get('outcome') == 'error' and r.get('error_kind'))),
            'retries': sum(1 for r in nr if (r.get('attempt') or 1) > 1),
        }
    return out


def wall_table(rows: list[dict], gap_s: float) -> dict:
    """按 node / Commander role 的墙钟两口径：span 分段与 Σlatency。"""
    out: dict[str, dict] = {}
    groups = {_cost_group(row) for row in rows}
    for node in sorted(groups):
        nr = sorted((row for row in rows if _cost_group(row) == node),
                    key=lambda r: r.get('at') or '')
        stamps = [(datetime.fromisoformat(r['at']), (r.get('latency_ms') or 0) / 1000.0)
                  for r in nr if r.get('at')]
        segments: list[float] = []
        seg_start = None
        prev_t = None
        first_lat = 0.0
        for t, lat in stamps:
            if seg_start is None or (t - prev_t).total_seconds() > gap_s:
                if seg_start is not None:
                    segments.append((prev_t - seg_start).total_seconds() + first_lat)
                seg_start, first_lat = t, lat
            prev_t = t
        if seg_start is not None:
            segments.append((prev_t - seg_start).total_seconds() + first_lat)
        out[node] = {
            'segments': len(segments),
            'span_s': round(sum(segments), 1),
            'sum_latency_s': round(sum(lat for _, lat in stamps), 1),
        }
    return out


# ── 质量（0 成本复检，纯读；照 `novel/orchestrator/inbox_projection.py::_report_of` 的姿势） ──

def stage_reports(base: str, sid: str) -> dict[str, dict]:
    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    out: dict[str, dict] = {}
    for stage in STAGES:
        # ⚠ 这里曾是 `nodes[0] if nodes else f'{stage}.review'`。那条兜底真走到时会
        #   **编出一个不存在的节点 id**，一路带进 `readonly_ctx`——而下面那个
        #   `except Exception` 会把它当成「够不着」记进读数，于是一个登记错误被
        #   如实报成了产物问题。⇒ 走 `check_node`，不变量破了就在那里出声。
        try:
            report = check_only(stage)(
                readonly_ctx(novel_dir, check_node(stage), code))
        except Exception as e:                       # 读数器：够不着如实报，不替它编绿灯
            out[stage] = {'unreachable': f'{type(e).__name__}: {e}'}
            continue
        hard_left = [f'{r.rule}({r.status})' for r in report.rules
                     if r.sev == 'hard' and r.status in ('hit', 'skipped')]
        metrics = {r.rule: {
            'scanned': r.scanned,
            'details': [f.detail for f in r.findings],
        } for r in report.rules if r.sev == 'metric'}
        out[stage] = {
            'badge': report.badge,
            'counts': report.counts.model_dump(),
            'hard_left': hard_left,
            'metrics': metrics,
        }
    return out


# ── inbox / 成书 ─────────────────────────────────────────────────────────────

def inbox_digest(base: str, sid: str) -> dict:
    v = inbox_view(base, sid)
    return {'counts': dict(v.counts), 'total': v.total, 'wontfix': v.wontfix}


def _text_stats(text: str) -> dict:
    return {
        'chars': len(text),
        'chars_no_ws': sum(1 for ch in text if not ch.isspace()),
        'chapter_marks': len(_CH_MARK.findall(text)),   # 「第N章/回」行头近似计数
    }


def book_stats(base: str, sid: str, source: str | None) -> dict:
    novel_dir = Path(paths.resolve_novel_dir(base, sid))
    # ⚠ 成书按书名命名 ⇒ **不许在这里手拼路径**（那是第二份路径知识，改名时会静默
    #   变成「这本书还没成书」）。落点是 `novel/paths.py::book_txt_rel`。
    p = novel_dir / paths.book_txt_rel(novel_dir)
    if not p.is_file():
        return {'book': None}
    out: dict = {'book': _text_stats(p.read_text(encoding='utf-8'))}
    if source:
        src = _text_stats(Path(source).read_text(encoding='utf-8'))
        out['source'] = src
        if src['chars_no_ws']:
            out['ratio_chars_no_ws'] = round(
                out['book']['chars_no_ws'] / src['chars_no_ws'], 3)
    return out


# ── 折钱（只在给了 --rates 时；结果不落仓内产物） ────────────────────────────

def money_of(cost: dict, rates: dict) -> dict:
    """折算输入价：缓存两列与两个费率都完整时按 hit/miss 分算；
    否则保守回退到全部 `input` 价。completion 含 reasoning。"""
    per = rates.get('per_mtok', {})
    out: dict[str, dict] = {}
    for node, c in cost.items():
        cell: dict = {}
        for model in c['models']:
            r = per.get(model)
            if r is None:
                cell[model] = None
                continue
            cache_complete = (
                c.get('prompt_cache_hit_null_rows', c['calls_made']) == 0
                and c.get('prompt_cache_miss_null_rows', c['calls_made']) == 0
                and c.get('prompt_cache_mismatch_rows', 0) == 0)
            cache_rates = all(
                key in r for key in ('input_cache_hit', 'input_cache_miss'))
            if cache_complete and cache_rates:
                input_money = (
                    c.get('prompt_cache_hit_tokens', 0) / 1e6 * r['input_cache_hit']
                    + c.get('prompt_cache_miss_tokens', 0) / 1e6 * r['input_cache_miss'])
            elif 'input' in r:
                input_money = c['prompt_tokens'] / 1e6 * r['input']
            else:
                cell[model] = None
                continue
            cell[model] = round(
                input_money + c['completion_tokens'] / 1e6 * r['output'], 4)
        out[node] = cell
    return out


# ── 渲染 ─────────────────────────────────────────────────────────────────────

def _fmt_int(n: int) -> str:
    return f'{n:,}'


def _cache_token_cell(cost: dict, key: str) -> str:
    """Provider omitted usage (`null`) must not render as the numeric fact zero."""
    null_key = key.replace('_tokens', '_null_rows')
    if cost['calls_made'] and cost[null_key] == cost['calls_made']:
        return '—（全部 null）'
    value = _fmt_int(cost[key])
    return value if not cost[null_key] else f'{value}（{cost[null_key]} 行 null）'


def _cache_rate_cell(cost: dict) -> str:
    rate = cost['prompt_cache_hit_rate']
    if rate is None:
        if cost['prompt_cache_known_rows']:
            return 'n/a（已知 token 合计为 0）'
        return '—（usage null）'
    suffix = (f'；{cost["prompt_cache_partial_rows"]} 行仅给一列'
              if cost['prompt_cache_partial_rows'] else '')
    return f'{rate:.1%}{suffix}'


def _row(cells: list[str]) -> str:
    return '| ' + ' | '.join(cells) + ' |'


def render(sid: str, arms: dict[str, dict], rates: dict | None) -> str:
    names = list(arms)
    arm_line = ', '.join('{}={}'.format(n, arms[n]['base']) for n in names)
    L: list[str] = [f'# 跨臂对比 · sid={sid}', '', f'臂：{arm_line}', '']

    L += ['## ① 成本（token 事实；指挥/生成分账；cached 行实付 0 不计入）', '',
          _row(['node / role', '账'] + names), _row(['---'] * (len(names) + 2))]
    all_nodes = sorted({n for a in arms.values() for n in a['cost']})
    for node in all_nodes:
        tag = ('指挥' if node == 'role:commander' else '生成')
        for key, label in (('calls_made', '真调用'), ('calls_cached', '缓存命中'),
                           ('prompt_tokens', 'prompt'),
                           ('completion_tokens', 'completion（已含 reasoning）'),
                           ('reasoning_tokens', 'reasoning（completion 子集）')):
            cells = []
            for n in names:
                c = arms[n]['cost'].get(node)
                cells.append('—' if c is None else _fmt_int(c[key]))
            L.append(_row([f'{node}·{tag}', label] + cells))
        for key, label in (
                ('prompt_cache_hit_tokens', '供应商 prompt cache hit'),
                ('prompt_cache_miss_tokens', '供应商 prompt cache miss')):
            cells = []
            for n in names:
                c = arms[n]['cost'].get(node)
                cells.append('—' if c is None else _cache_token_cell(c, key))
            L.append(_row([f'{node}·{tag}', label] + cells))
        L.append(_row(
            [f'{node}·{tag}', '供应商 prompt cache 命中率']
            + [('—' if arms[n]['cost'].get(node) is None
                else _cache_rate_cell(arms[n]['cost'][node])) for n in names]))
        for key, label in (
                ('models', 'model（调用行）'),
                ('efforts', 'effort（wire 行）'),
                ('thinking', 'thinking（wire 行）'),
                ('fallback_steps', '恢复配置转换步数（按 purpose 去除网络重试/同配置重采）'),
                ('conditional_surface_repair_calls',
                 'habit_quote 条件窄修复 wire（非基础调用）'),
                ('outcomes', 'wire outcome'),
                ('errors', 'error_kind')):
            cells = []
            for n in names:
                c = arms[n]['cost'].get(node)
                if c is None:
                    cells.append('—')
                elif isinstance(c[key], dict):
                    cells.append(json.dumps(c[key], ensure_ascii=False) if c[key] else '无')
                else:
                    cells.append(str(c[key]))
            L.append(_row([f'{node}·{tag}', label] + cells))
    for scope, pred in (
            ('指挥合计', lambda nd: nd == 'role:commander'),
            ('生成合计', lambda nd: nd != 'role:commander')):
        for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'calls_made'):
            cells = [_fmt_int(sum(c[key] for nd, c in arms[n]['cost'].items() if pred(nd)))
                     for n in names]
            L.append(_row([f'**{scope}**', key] + cells))
    null_note = {n: sum(c['prompt_null_rows'] + c['completion_null_rows']
                        for c in arms[n]['cost'].values()) for n in names}
    if any(null_note.values()):
        L += ['', f'⚠ usage 为 null 的行（≠0，响应没给）：{null_note}']
    mismatches = {
        n: sum(c['prompt_cache_mismatch_rows'] for c in arms[n]['cost'].values())
        for n in names
    }
    if any(mismatches.values()):
        L += ['', '⚠ 供应商 cache usage 不闭合（hit + miss != prompt）的行：'
                  f'{mismatches}']
    L += ['', '注：`completion_tokens` 已包含 `reasoning_tokens`；后者只作诊断，'
              '合计与折价均不会再次相加。']

    if rates:
        L += ['', f'### 折算（费率表 {rates.get("date")}，{rates.get("currency", "?")}；'
                  f'缓存 usage+费率完整时分算 hit/miss，否则 input 未命中价上界；'
                  f'completion 含 reasoning）', '',
              _row(['范围'] + names), _row(['---'] * (len(names) + 1))]
        for scope, pred in (
                ('指挥', lambda nd: nd == 'role:commander'),
                ('生成', lambda nd: nd != 'role:commander')):
            cells = []
            for n in names:
                money = money_of({nd: c for nd, c in arms[n]['cost'].items() if pred(nd)},
                                 rates)
                vals = [v for cell in money.values() for v in cell.values()]
                cells.append('含无费率模型' if any(v is None for v in vals)
                             else f'{sum(vals):.3f}')
            L.append(_row([scope] + cells))

    L += ['', '## ② 墙钟（span=近似墙钟；Σlatency 并发下高估，只作对照）', '',
          _row(['node / role', '口径'] + names), _row(['---'] * (len(names) + 2))]
    for node in all_nodes:
        for key, label in (('span_s', 'span(s)'), ('segments', '分段数'),
                           ('sum_latency_s', 'Σlatency(s)')):
            cells = []
            for n in names:
                w = arms[n]['wall'].get(node)
                cells.append('—' if w is None else str(w[key]))
            L.append(_row([node, label] + cells))
    cells = [str(round(sum(w['span_s'] for w in arms[n]['wall'].values()), 1)) for n in names]
    L.append(_row(['**全部 node / role span 合计**', 's'] + cells))

    L += ['', '## ③ 质量闸（badge / 四态 / HARD 残留；判定语义归 check 报告）', '',
          _row(['stage', '项'] + names), _row(['---'] * (len(names) + 2))]
    for stage in STAGES:
        for key in ('badge', 'counts', 'hard_left'):
            cells = []
            for n in names:
                r = arms[n]['quality'].get(stage, {})
                if 'unreachable' in r:
                    cells.append(f'够不着: {r["unreachable"][:60]}' if key == 'badge' else '—')
                elif key == 'counts':
                    c = r['counts']
                    cells.append(f'ok{c["ok"]}/hit{c["hit"]}/unv{c["unver"]}/skp{c["skipped"]}')
                elif key == 'hard_left':
                    cells.append('; '.join(r['hard_left']) or '无')
                else:
                    cells.append(str(r[key]))
            L.append(_row([stage, key] + cells))

    L += ['', '## ④ metric 读数（无条件产出；不参与徽章；不设新线）', '',
          _row(['metric', ''] + names), _row(['---'] * (len(names) + 2))]
    metric_rules = sorted({m for a in arms.values() for st in a['quality'].values()
                           for m in st.get('metrics', {})})
    for rule in metric_rules:
        cells_s, cells_d = [], []
        for n in names:
            got = None
            for st in arms[n]['quality'].values():
                if rule in st.get('metrics', {}):
                    got = st['metrics'][rule]
                    break
            if got is None:
                cells_s.append('—'); cells_d.append('—')
            else:
                cells_s.append(got['scanned'] or '')
                d = ' ⧸ '.join(got['details'])
                cells_d.append((d[:110] + '…') if len(d) > 110 else (d or '（无读数行）'))
        L.append(_row([rule, 'scanned'] + cells_s))
        L.append(_row(['', '读数'] + cells_d))

    L += ['', '## ⑤ inbox 待人工', '', _row(['类'] + names), _row(['---'] * (len(names) + 1))]
    for k in INBOX_KIND:
        L.append(_row([k] + [str(arms[n]['inbox']['counts'].get(k, 0)) for n in names]))
    L.append(_row(['**total / wontfix**'] +
                  [f'{arms[n]["inbox"]["total"]} / {arms[n]["inbox"]["wontfix"]}'
                   for n in names]))

    L += ['', '## ⑥ 账本健康', '', _row(['项'] + names), _row(['---'] * (len(names) + 1))]
    for key, label in (('errors', '错误(按 kind)'), ('retries', '重试行数')):
        cells = []
        for n in names:
            if key == 'errors':
                agg: Counter = Counter()
                for c in arms[n]['cost'].values():
                    agg.update(c['errors'])
                cells.append(json.dumps(dict(agg), ensure_ascii=False) if agg else '无')
            else:
                cells.append(str(sum(c['retries'] for c in arms[n]['cost'].values())))
        L.append(_row([label] + cells))

    L += ['', '## ⑦ 成书统计（成书三查的读数面；判定仍归人工三查）', '']
    for n in names:
        L.append(f'- **{n}**: {json.dumps(arms[n]["book"], ensure_ascii=False)}')
    L.append('')
    return '\n'.join(L)


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sid')
    ap.add_argument('--arm', action='append', default=[],
                    help='名=base路径，可多次')
    ap.add_argument('--rates', help='带日期费率表 JSON（不给则只报 token 事实）')
    ap.add_argument('--gap-seconds', type=float, default=600.0)
    ap.add_argument('--source', help='源书 txt（给了才算成书比值）')
    ap.add_argument('--json', dest='json_out', help='把全量结构化结果另存一份 JSON')
    a = ap.parse_args()

    if not a.sid or not a.arm:
        ap.error('矩阵模式需要 --sid 与至少一个 --arm 名=path')
    arms: dict[str, dict] = {}
    for spec in a.arm:
        name, _, base = spec.partition('=')
        if not base:
            ap.error(f'--arm 形状是 名=path，实得 {spec!r}')
        rows = load_rows(base, a.sid)
        arms[name] = {
            'base': base,
            'cost': cost_table(rows),
            'wall': wall_table(rows, a.gap_seconds),
            'quality': stage_reports(base, a.sid),
            'inbox': inbox_digest(base, a.sid),
            'book': book_stats(base, a.sid, a.source),
        }
    rates = json.loads(Path(a.rates).read_text(encoding='utf-8')) if a.rates else None
    print(render(a.sid, arms, rates))
    if a.json_out:
        Path(a.json_out).write_text(
            json.dumps({'sid': a.sid, 'arms': arms, 'rates': rates},
                       ensure_ascii=False, indent=2, default=str),
            encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
