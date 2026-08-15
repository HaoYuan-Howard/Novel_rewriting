#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨 run（臂）对比读数器。**只读**：不写盘、不发请求、不产生新判定。

给模型/思考 A/B 用：N 个 `--arm`（每臂一个 base，同一个 sid），把
  ① 成本（账本 token 事实，生成/判官分账）      ② 墙钟（span 分段 + Σlatency 双口径）
  ③ 质量闸（各层 badge / 四态计数 / HARD 残留） ④ `sev='metric'` 全量读数
  ⑤ inbox 六类                                   ⑥ 语义审判摘要
  ⑦ 账本健康（限流/超时/重试）                   ⑧ 成书统计
摆成「列=臂」的 markdown 矩阵。**它是读数器不是判官**（R-13.6）：判定语义只归
check 报告；质量细排名一律不出——单次跑不可归因（`docs/E2E_VALIDATION_20260813.md` §10）。

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
  python -X utf8 tools/ab_compare.py --compare-semantic A.json B.json
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

from contracts.invariants import STAGES                      # noqa: E402
from novel import paths, prov                                # noqa: E402
from novel.llm.ledger import ledger_path                     # noqa: E402
from novel.orchestrator.nodes import check_nodes             # noqa: E402
from novel.orchestrator.state import readonly_ctx            # noqa: E402
from novel.stages import check_only                          # noqa: E402
from novel.views.inbox import inbox_view                     # noqa: E402

#: 判官节点。质量测量仪器与生成分账（A/B 里判官统一固定配置，成本不归臂）。
JUDGE_NODES = frozenset({'stage5.semantic', 'stage6.semantic'})

#: 章头两种实测形态：`第N章/回` 行头（可带标题），或**独行裸数字**（成书实测是后者）。
_CH_MARK = re.compile(r'^(?:第[0-9一二三四五六七八九十百千两]{1,7}[章回]|\d{1,4}\s*$)', re.M)


# ── 账本 ─────────────────────────────────────────────────────────────────────

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
    """按 node 聚成本事实；生成与判官分账。"""
    out: dict[str, dict] = {}
    for node in sorted({r['node'] for r in rows}):
        nr = [r for r in rows if r['node'] == node]
        wire = [r for r in nr if not r.get('cached')]
        p, p_n, p_null = _tok(wire, 'prompt_tokens')
        c, c_n, c_null = _tok(wire, 'completion_tokens')
        g, g_n, g_null = _tok(wire, 'reasoning_tokens')
        out[node] = {
            'judge': node in JUDGE_NODES,
            'models': sorted({r.get('model') for r in nr if r.get('model')}),
            'calls_made': len(wire),
            'calls_cached': len(nr) - len(wire),
            'prompt_tokens': p, 'prompt_null_rows': p_null,
            'completion_tokens': c, 'completion_null_rows': c_null,
            'reasoning_tokens': g, 'reasoning_null_rows': g_null,
            'errors': dict(Counter(r.get('error_kind') for r in nr
                                   if r.get('outcome') == 'error' and r.get('error_kind'))),
            'retries': sum(1 for r in nr if (r.get('attempt') or 1) > 1),
        }
    return out


def wall_table(rows: list[dict], gap_s: float) -> dict:
    """按 node 的墙钟两口径：span 分段（近似墙钟）与 Σlatency（并发下的高估口径）。"""
    out: dict[str, dict] = {}
    for node in sorted({r['node'] for r in rows}):
        nr = sorted((r for r in rows if r['node'] == node),
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


# ── 质量（0 成本复检，纯读；照 `novel/views/inbox.py::_report_of` 的姿势） ──

def stage_reports(base: str, sid: str) -> dict[str, dict]:
    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    out: dict[str, dict] = {}
    for stage in STAGES:
        nodes = check_nodes(stage)
        node = nodes[0] if nodes else f'{stage}.review'
        try:
            report = check_only(stage)(readonly_ctx(novel_dir, node, code))
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


# ── 语义审判摘要 ────────────────────────────────────────────────────────────

def _latest(novel_dir: Path, stage_dir: str) -> Path | None:
    got = sorted((novel_dir / stage_dir).glob('semantic_*.json'),
                 key=lambda p: int(p.stem.split('_')[1]))
    return got[-1] if got else None


def semantic_digest(base: str, sid: str) -> dict:
    novel_dir = Path(paths.resolve_novel_dir(base, sid))
    out: dict = {}
    p5 = _latest(novel_dir, 'stage5')
    if p5 is not None:
        d = json.loads(p5.read_text(encoding='utf-8'))
        out['stage5'] = {
            'file': p5.name,
            'judge_model': (d.get('_provenance') or {}).get('model'),
            'review_seq': d.get('mechanical_review_seq'),
            'secret_status': dict(Counter(s.get('status') for s in d.get('secrets', []))),
            'chapter_coverage': dict(Counter(c.get('coverage') for c in d.get('chapters', []))),
            'chapters_uncertain': sum(1 for c in d.get('chapters', [])
                                      if c.get('uncertainty_reason')),
        }
    p6 = _latest(novel_dir, 'stage6')
    if p6 is not None:
        d = json.loads(p6.read_text(encoding='utf-8'))
        per_label: dict[str, Counter] = defaultdict(Counter)
        for dec in d.get('decisions', []):
            per_label[dec.get('label', '?')][dec.get('status')] += 1
        out['stage6'] = {
            'file': p6.name,
            'judge_model': (d.get('_provenance') or {}).get('model'),
            'hook_file_sha1': d.get('hook_file_sha1'),
            'decision_status': dict(Counter(x.get('status') for x in d.get('decisions', []))),
            'per_label': {k: dict(v) for k, v in sorted(per_label.items())},
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
    p = novel_dir / 'stage7' / 'book.txt'
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


# ── 判官一致性：两份 semantic 产物逐条对比 ──────────────────────────────────

def diff_semantic(file_a: str, file_b: str) -> dict:
    a = json.loads(Path(file_a).read_text(encoding='utf-8'))
    b = json.loads(Path(file_b).read_text(encoding='utf-8'))
    if 'secrets' in a:                                   # stage5 形
        ka = {s['secret_id']: s for s in a.get('secrets', [])}
        kb = {s['secret_id']: s for s in b.get('secrets', [])}
        flips = [{'secret_id': k,
                  'a': {'status': ka[k].get('status'),
                        'earliest_chapter': ka[k].get('earliest_chapter')},
                  'b': {'status': kb[k].get('status'),
                        'earliest_chapter': kb[k].get('earliest_chapter')}}
                 for k in sorted(ka.keys() & kb.keys())
                 if ka[k].get('status') != kb[k].get('status')]
        cov_a = {c['output_ordinal']: c.get('coverage') for c in a.get('chapters', [])}
        cov_b = {c['output_ordinal']: c.get('coverage') for c in b.get('chapters', [])}
        cov_flips = [{'chapter': k, 'a': cov_a[k], 'b': cov_b[k]}
                     for k in sorted(cov_a.keys() & cov_b.keys())
                     if cov_a[k] != cov_b[k]]
        total = len(ka.keys() & kb.keys())
        return {'kind': 'stage5', 'compared_secrets': total,
                'secret_flips': flips, 'coverage_flips': cov_flips,
                'only_in_a': sorted(ka.keys() - kb.keys()),
                'only_in_b': sorted(kb.keys() - ka.keys())}
    ka = {(d['label'], d['claim_id']): d.get('status') for d in a.get('decisions', [])}
    kb = {(d['label'], d['claim_id']): d.get('status') for d in b.get('decisions', [])}
    flips = [{'label': lb, 'claim_id': cid, 'a': ka[(lb, cid)], 'b': kb[(lb, cid)]}
             for lb, cid in sorted(ka.keys() & kb.keys())
             if ka[(lb, cid)] != kb[(lb, cid)]]
    return {'kind': 'stage6', 'compared_decisions': len(ka.keys() & kb.keys()),
            'flips': flips,
            'only_in_a': len(ka.keys() - kb.keys()),
            'only_in_b': len(kb.keys() - ka.keys()),
            'same_hook_sha1': a.get('hook_file_sha1') == b.get('hook_file_sha1')}


# ── 折钱（只在给了 --rates 时；结果不落仓内产物） ────────────────────────────

def money_of(cost: dict, rates: dict) -> dict:
    """折算上界：input 全按未命中价（服务端上下文缓存折扣在账本里看不见）。
    completion 按「含 reasoning」计（DeepSeek 计费口径把思维链算进输出）。"""
    per = rates.get('per_mtok', {})
    out: dict[str, dict] = {}
    for node, c in cost.items():
        cell: dict = {}
        for model in c['models']:
            r = per.get(model)
            if r is None:
                cell[model] = None
                continue
            cell[model] = round(
                c['prompt_tokens'] / 1e6 * r['input']
                + c['completion_tokens'] / 1e6 * r['output'], 4)
        out[node] = cell
    return out


# ── 渲染 ─────────────────────────────────────────────────────────────────────

def _fmt_int(n: int) -> str:
    return f'{n:,}'


def _row(cells: list[str]) -> str:
    return '| ' + ' | '.join(cells) + ' |'


def render(sid: str, arms: dict[str, dict], rates: dict | None) -> str:
    names = list(arms)
    arm_line = ', '.join('{}={}'.format(n, arms[n]['base']) for n in names)
    L: list[str] = [f'# 跨臂对比 · sid={sid}', '', f'臂：{arm_line}', '']

    L += ['## ① 成本（token 事实；生成/判官分账；cached 行实付 0 不计入）', '',
          _row(['node', '账'] + names), _row(['---'] * (len(names) + 2))]
    all_nodes = sorted({n for a in arms.values() for n in a['cost']})
    for node in all_nodes:
        tag = '判官' if node in JUDGE_NODES else '生成'
        for key, label in (('calls_made', '真调用'), ('calls_cached', '缓存命中'),
                           ('prompt_tokens', 'prompt'), ('completion_tokens', 'completion'),
                           ('reasoning_tokens', 'reasoning')):
            cells = []
            for n in names:
                c = arms[n]['cost'].get(node)
                cells.append('—' if c is None else _fmt_int(c[key]))
            L.append(_row([f'{node}·{tag}', label] + cells))
    for scope, pred in (('生成合计', lambda nd: nd not in JUDGE_NODES),
                        ('判官合计', lambda nd: nd in JUDGE_NODES)):
        for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'calls_made'):
            cells = [_fmt_int(sum(c[key] for nd, c in arms[n]['cost'].items() if pred(nd)))
                     for n in names]
            L.append(_row([f'**{scope}**', key] + cells))
    null_note = {n: sum(c['prompt_null_rows'] + c['completion_null_rows']
                        for c in arms[n]['cost'].values()) for n in names}
    if any(null_note.values()):
        L += ['', f'⚠ usage 为 null 的行（≠0，响应没给）：{null_note}']

    if rates:
        L += ['', f'### 折算（费率表 {rates.get("date")}，{rates.get("currency", "?")}；'
                  f'input 全按未命中价=上界，completion 含 reasoning）', '',
              _row(['范围'] + names), _row(['---'] * (len(names) + 1))]
        for scope, pred in (('生成', lambda nd: nd not in JUDGE_NODES),
                            ('判官', lambda nd: nd in JUDGE_NODES)):
            cells = []
            for n in names:
                money = money_of({nd: c for nd, c in arms[n]['cost'].items() if pred(nd)},
                                 rates)
                vals = [v for cell in money.values() for v in cell.values()]
                cells.append('含无费率模型' if any(v is None for v in vals)
                             else f'{sum(vals):.3f}')
            L.append(_row([scope] + cells))

    L += ['', '## ② 墙钟（span=近似墙钟；Σlatency 并发下高估，只作对照）', '',
          _row(['node', '口径'] + names), _row(['---'] * (len(names) + 2))]
    for node in all_nodes:
        for key, label in (('span_s', 'span(s)'), ('segments', '分段数'),
                           ('sum_latency_s', 'Σlatency(s)')):
            cells = []
            for n in names:
                w = arms[n]['wall'].get(node)
                cells.append('—' if w is None else str(w[key]))
            L.append(_row([node, label] + cells))
    cells = [str(round(sum(w['span_s'] for w in arms[n]['wall'].values()), 1)) for n in names]
    L.append(_row(['**全部节点 span 合计**', 's'] + cells))

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
    kinds = ('invalid', 'rejected', 'stalled', 'human_only', 'upstream', 'approval')
    for k in kinds:
        L.append(_row([k] + [str(arms[n]['inbox']['counts'].get(k, 0)) for n in names]))
    L.append(_row(['**total / wontfix**'] +
                  [f'{arms[n]["inbox"]["total"]} / {arms[n]["inbox"]["wontfix"]}'
                   for n in names]))

    L += ['', '## ⑥ 语义审判摘要（判官模型以各臂产物 `_provenance.model` 为准）', '']
    for n in names:
        L.append(f'- **{n}**: {json.dumps(arms[n]["semantic"], ensure_ascii=False)}')

    L += ['', '## ⑦ 账本健康', '', _row(['项'] + names), _row(['---'] * (len(names) + 1))]
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

    L += ['', '## ⑧ 成书统计（成书三查的读数面；判定仍归人工三查）', '']
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
    ap.add_argument('--compare-semantic', nargs=2, metavar=('A', 'B'),
                    help='判官一致性：对比两份 semantic_{n}.json')
    a = ap.parse_args()

    if a.compare_semantic:
        print(json.dumps(diff_semantic(*a.compare_semantic), ensure_ascii=False, indent=2))
        return 0

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
            'semantic': semantic_digest(base, a.sid),
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
