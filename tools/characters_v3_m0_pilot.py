# -*- coding: utf-8 -*-
"""Characters v3 M1 lexicon Resolve 隔离试跑。

冻结《夫君的报恩》11 章 MainText，在独立输出根执行与生产相同的编译链：
书级 Extract（surface 发现）→ compile_book_candidates（字面回填 + 嵌套预排除）
→ lexicon Resolve（surface_bindings）→ compile_registry_v3。

保存请求、原始响应、候选、编译结果、ledger 与通过条件核对。
付费真机需 ``--yes``；``--replay-responses`` 可对已保存的 M1 形响应离线重放
（须含 ``surface_bindings``，旧 per-mention ``decisions`` 无法再解析）。

用法（仓根 / worktree，conda novel_refactor）：

  python tools/characters_v3_m0_pilot.py --yes
  python tools/characters_v3_m0_pilot.py --replay-responses <pilot>/stage2/characters_v3_pilot

默认源：outputs/acceptance_fujun 中已有 stage1 MainText。
默认输出：outputs/m0_characters_v3_pilot（不得覆盖源书正式产物）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contracts.schema.characters import (  # noqa: E402
    BookExtractResponse, BookResolveResponse, CharacterRegistryV3,
    RegisteredCharacter,
)
from novel import config, paths, prov  # noqa: E402
from novel.llm.providers import create_legacy_client, default_legacy_backend  # noqa: E402
from novel.run import RunContext  # noqa: E402
from novel.stages.stage2.characters.candidates import (  # noqa: E402
    NestedExclusion, compile_book_candidates,
)
from novel.stages.stage2.characters.compile_v3 import (  # noqa: E402
    compile_registry_v3, name_usable_in_chapter,
)
from novel.stages.stage2.characters.prompts_v3 import (  # noqa: E402
    BOOK_EXTRACT_SYSTEM, BOOK_RESOLVE_SYSTEM,
)
from novel.stages.stage2.characters.registry import (  # noqa: E402
    build_extract_user, build_resolve_user,
)
from novel.work_units import require_workflow  # noqa: E402

SID = '2059579376044396719'
TITLE = '夫君的报恩'
DEFAULT_SOURCE_BASE = ROOT / 'outputs' / 'acceptance_fujun'
DEFAULT_PILOT_BASE = ROOT / 'outputs' / 'm0_characters_v3_pilot'
EXTRACT_MAX_TOKENS = 32_768
RESOLVE_MAX_TOKENS = 64_000


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')


def _load_main_text(novel_dir: Path) -> list[dict[str, Any]]:
    path = novel_dir / 'stage1' / 'main_text.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(raw, dict) and 'payload' in raw:
        chapters = raw['payload']
    elif isinstance(raw, list):
        chapters = raw
    else:
        raise ValueError(f'无法解析 MainText：{path}')
    if not isinstance(chapters, list) or not chapters:
        raise ValueError(f'MainText 章列表为空：{path}')
    out = []
    for row in chapters:
        if not isinstance(row, dict):
            raise ValueError('MainText 章行非法')
        out.append({
            'chapter_ordinal': int(row['chapter_ordinal']),
            'text': str(row['text']),
        })
    out.sort(key=lambda row: row['chapter_ordinal'])
    return out


def _prepare_pilot_tree(*, source_base: Path, pilot_base: Path) -> Path:
    src = paths.resolve_novel_dir(source_base, SID)
    if not (src / 'stage1' / 'main_text.json').is_file():
        raise FileNotFoundError(f'源书缺少 MainText：{src}')
    if pilot_base.exists() and any(pilot_base.iterdir()):
        raise FileExistsError(
            f'试点输出根必须为空或不存在，拒绝复用：{pilot_base}')
    pilot_base.mkdir(parents=True, exist_ok=True)
    # 先初始化空工作流，再拷贝 stage0/1；否则 _require_clean_base 会拒已有 stage 目录。
    require_workflow(pilot_base)
    dst = pilot_base / paths.novel_dirname(TITLE, SID)
    dst.mkdir(parents=True, exist_ok=False)
    for stage in ('stage0', 'stage1'):
        shutil.copytree(src / stage, dst / stage)
    (dst / 'stage2' / 'characters_v3_pilot').mkdir(parents=True, exist_ok=True)
    return dst


def _load_saved_responses(responses_dir: Path) -> tuple[BookExtractResponse, BookResolveResponse]:
    extract_path = responses_dir / 'extract_book.json'
    resolve_path = responses_dir / 'resolve.json'
    if not extract_path.is_file() or not resolve_path.is_file():
        alt = responses_dir / 'responses'
        if (alt / 'extract_book.json').is_file() and (alt / 'resolve.json').is_file():
            extract_path = alt / 'extract_book.json'
            resolve_path = alt / 'resolve.json'
        else:
            raise FileNotFoundError(
                f'离线重放需要 extract_book.json 与 resolve.json：{responses_dir}')
    extract = BookExtractResponse.model_validate(
        json.loads(extract_path.read_text(encoding='utf-8')))
    raw_resolve = json.loads(resolve_path.read_text(encoding='utf-8'))
    if 'surface_bindings' not in raw_resolve and 'decisions' in raw_resolve:
        raise ValueError(
            '保存的 Resolve 仍是 per-mention decisions 形；'
            'M1 lexicon Resolve 需要 surface_bindings，无法离线重放旧响应')
    resolve = BookResolveResponse.model_validate(raw_resolve)
    return extract, resolve


def _eval_pass(payload: dict[str, Any]) -> dict[str, Any]:
    """对 compile_registry_v3 载荷做试点通过条件核对（不依赖正式 provenance）。"""
    characters = {
        cid: RegisteredCharacter.model_validate(row)
        for cid, row in payload['characters'].items()
    }
    mentions = payload['mentions']
    by_canon = {
        row.canonical_surface: (cid, row) for cid, row in characters.items()
    }
    checks: dict[str, Any] = {}
    daughter = by_canon.get('女儿')
    checks['daughter_registered'] = daughter is not None
    checks['daughter_first_mention_ch10'] = (
        bool(daughter) and daughter[1].first_mention_chapter == 10)
    checks['daru_registered'] = '大儒' in by_canon

    pei = None
    for surface, (cid, row) in by_canon.items():
        if surface == '裴书远' or '裴书远' in surface:
            pei = (cid, row)
            break
        if any(s.surface == '裴书远' for s in row.surfaces):
            pei = (cid, row)
            break
    checks['peishuyuan_registered'] = pei is not None
    if pei:
        cid, row = pei
        checks['peishuyuan_first_mention_ch1'] = row.first_mention_chapter == 1
        checks['peishuyuan_no_name_license_ch1'] = not name_usable_in_chapter(row, 1)
        checks['peishuyuan_mentioned_ch1'] = any(
            m.get('char_id') == cid
            and m.get('disposition') == 'bound'
            and m.get('chapter_ordinal') == 1
            for m in mentions.values())
        typo = [
            m for m in mentions.values()
            if m.get('surface') == '裴知远'
            and m.get('disposition') == 'bound'
            and m.get('char_id') == cid
        ]
        checks['peizhiyuan_typo_bound'] = (
            len(typo) == 1 and typo[0].get('kind') == 'source_typo'
            and typo[0].get('chapter_ordinal') == 6)
    else:
        checks['peishuyuan_first_mention_ch1'] = False
        checks['peishuyuan_no_name_license_ch1'] = False
        checks['peishuyuan_mentioned_ch1'] = False
        checks['peizhiyuan_typo_bound'] = False

    relational = {'娘子', '嫂嫂', '嫂夫人', '嫂子'}
    bound_rel = [
        m for m in mentions.values()
        if m.get('surface') in relational and m.get('disposition') == 'bound'
    ]
    checks['relational_titles_bound_count'] = len(bound_rel)
    checks['relational_titles_bound'] = len(bound_rel) >= 1
    checks['no_role_field'] = 'role' not in CharacterRegistryV3.model_fields
    checks['compiled_has_no_role'] = all(
        'role' not in row for row in payload['characters'].values())
    checks['passed'] = all(
        bool(checks[k]) for k in (
            'daughter_registered', 'daughter_first_mention_ch10',
            'daru_registered', 'peishuyuan_registered',
            'peishuyuan_first_mention_ch1', 'peishuyuan_no_name_license_ch1',
            'peishuyuan_mentioned_ch1', 'peizhiyuan_typo_bound',
            'relational_titles_bound', 'compiled_has_no_role',
        )
    )
    return checks


def _assert_extract_coverage(
    main_text: list[dict[str, Any]], extract: BookExtractResponse,
) -> list[dict[str, Any]]:
    expected_ords = {row['chapter_ordinal'] for row in main_text}
    by_ord = {row.chapter_ordinal: row for row in extract.chapters}
    missing = sorted(expected_ords - set(by_ord))
    extra = sorted(set(by_ord) - expected_ords)
    if missing or extra:
        raise ValueError(f'Extract 章覆盖不完整：missing={missing} extra={extra}')
    return [
        {
            'chapter_ordinal': row.chapter_ordinal,
            'surfaces': list(row.surfaces),
            'surface_count': len(row.surfaces),
        }
        for row in sorted(extract.chapters, key=lambda r: r.chapter_ordinal)
    ]


def _write_candidates(
    out: Path,
    candidates: list[Any],
    lexicon: list[str],
    nested_exclusions: list[NestedExclusion],
) -> None:
    _write_json(out / 'candidates.json', {
        'lexicon': lexicon,
        'nested_exclusions': [
            {
                'mention_id': row.mention_id,
                'covered_by_mention_id': row.covered_by_mention_id,
            }
            for row in nested_exclusions
        ],
        'count': len(candidates),
        'rows': [row.model_dump(mode='json') for row in candidates],
    })
    print(
        f'[backfill] lexicon={len(lexicon)} candidates={len(candidates)} '
        f'nested_exclusions={len(nested_exclusions)}',
        flush=True)


def _read_cost(pilot_base: Path) -> dict[str, Any]:
    ledger_path = pilot_base / '_service' / 'ledger.jsonl'
    cost: dict[str, Any] = {
        'ledger': str(ledger_path), 'rows': 0, 'prompt_tokens': 0,
        'completion_tokens': 0, 'cached_tokens': 0, 'cached_calls': 0,
        'mode': 'paid',
    }
    if ledger_path.is_file():
        for line in ledger_path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('sid') != SID:
                continue
            cost['rows'] += 1
            cost['prompt_tokens'] += int(row.get('prompt_tokens') or 0)
            cost['completion_tokens'] += int(row.get('completion_tokens') or 0)
            if row.get('cached'):
                cost['cached_calls'] += 1
            usage = row.get('usage') or {}
            if isinstance(usage, dict):
                cost['cached_tokens'] += int(usage.get('cached_tokens') or 0)
    cost['total_tokens'] = cost['prompt_tokens'] + cost['completion_tokens']
    return cost


def run_pilot(
    *,
    source_base: Path,
    pilot_base: Path,
    dotenv: Path | None,
    replay_responses: Path | None = None,
) -> dict[str, Any]:
    novel_dir = _prepare_pilot_tree(source_base=source_base, pilot_base=pilot_base)
    out = novel_dir / 'stage2' / 'characters_v3_pilot'
    main_text = _load_main_text(novel_dir)
    if len(main_text) != 11:
        raise ValueError(f'期望 11 章，实得 {len(main_text)}')
    main_sha = _sha(json.dumps(main_text, ensure_ascii=False, sort_keys=True))
    _write_json(out / 'main_text.freeze.json', {
        'sid': SID, 'title': TITLE, 'chapters': len(main_text),
        'sha256': main_sha, 'code_version': prov.code_version(),
        'frozen_at': datetime.now(timezone.utc).isoformat(),
        'resolve_path': 'm1_lexicon_surface_bindings',
    })

    model_name = 'offline-replay'
    thinking_mode = None

    if replay_responses is not None:
        extract, resolve = _load_saved_responses(replay_responses.resolve())
        _write_json(out / 'responses' / 'extract_book.json',
                    extract.model_dump(mode='json'))
        _write_json(out / 'responses' / 'resolve.json',
                    resolve.model_dump(mode='json'))
        print('[replay] loaded saved Extract + Resolve (no LLM)', flush=True)
        extract_meta = _assert_extract_coverage(main_text, extract)
        candidates, lexicon, nested_exclusions = compile_book_candidates(
            main_text, extract)
        _write_candidates(out, candidates, lexicon, nested_exclusions)
        cost = {
            'ledger': None, 'rows': 0, 'prompt_tokens': 0,
            'completion_tokens': 0, 'cached_tokens': 0, 'cached_calls': 0,
            'total_tokens': 0, 'mode': 'replay',
        }
    else:
        cfg = config.load_config(dotenv_path=dotenv) if dotenv else config.load_config()
        if not cfg.has_key:
            raise RuntimeError('DEEPSEEK_API_KEY 未设置；拒绝发请求')

        backend = default_legacy_backend(node='stage2.characters')
        model_name = backend.profile.model
        thinking_mode = backend.profile.thinking_mode
        ctx = RunContext.open(
            str(pilot_base), SID, 'stage2.characters', paid=True,
            executor_backend=backend,
            executor_profile=backend.profile,
        )
        client = create_legacy_client(ctx, backend.profile)

        # BOOK_*_SYSTEM 已在 prompts_v3 挂 schema，勿再次 with_json_shape。
        system = BOOK_EXTRACT_SYSTEM
        user = build_extract_user(TITLE, main_text)
        purpose = 'characters_v3_m1_extract_book'
        _write_json(out / 'requests' / 'extract_book.json', {
            'purpose': purpose, 'system_sha256': _sha(system),
            'user_sha256': _sha(user),
        })
        (out / 'requests' / 'extract_book.user.txt').write_text(user, encoding='utf-8')
        print('[extract] book (11 chapters) …', flush=True)
        extract = client.ask_json(
            system, user, model_cls=BookExtractResponse, purpose=purpose,
            max_tokens=EXTRACT_MAX_TOKENS,
            rungs=(('low', False),),
        )
        _write_json(out / 'responses' / 'extract_book.json',
                    extract.model_dump(mode='json'))
        extract_meta = _assert_extract_coverage(main_text, extract)

        candidates, lexicon, nested_exclusions = compile_book_candidates(
            main_text, extract)
        _write_candidates(out, candidates, lexicon, nested_exclusions)

        resolve_system = BOOK_RESOLVE_SYSTEM
        resolve_user = build_resolve_user(
            TITLE, main_text,
            lexicon=lexicon,
            candidates=candidates,
            nested_exclusions=nested_exclusions,
        )
        _write_json(out / 'requests' / 'resolve.json', {
            'purpose': 'characters_v3_m1_resolve',
            'system_sha256': _sha(resolve_system),
            'user_sha256': _sha(resolve_user),
            'lexicon_size': len(lexicon),
            'candidate_count': len(candidates),
            'nested_exclusion_count': len(nested_exclusions),
        })
        (out / 'requests' / 'resolve.user.json').write_text(
            resolve_user, encoding='utf-8')
        print('[resolve] book lexicon …', flush=True)
        resolve = client.ask_json(
            resolve_system, resolve_user, model_cls=BookResolveResponse,
            purpose='characters_v3_m1_resolve', max_tokens=RESOLVE_MAX_TOKENS,
            rungs=(('low', False),),
        )
        _write_json(out / 'responses' / 'resolve.json',
                    resolve.model_dump(mode='json'))
        cost = _read_cost(pilot_base)

    payload = compile_registry_v3(
        main_text=main_text,
        candidates=candidates,
        resolve=resolve,
        nested_exclusions=nested_exclusions,
    )
    _write_json(out / 'registry_v3.json', payload)
    checks = _eval_pass(payload)
    _write_json(out / 'pass_checks.json', checks)
    _write_json(out / 'cost_envelope.json', cost)

    summary = {
        'sid': SID,
        'title': TITLE,
        'pilot_base': str(pilot_base),
        'novel_dir': str(novel_dir),
        'main_text_sha256': main_sha,
        'code_version': prov.code_version(),
        'model': model_name,
        'thinking_mode': thinking_mode,
        'resolve_path': 'm1_lexicon_surface_bindings',
        'extract_meta': extract_meta,
        'lexicon_size': len(lexicon),
        'candidate_count': len(candidates),
        'nested_exclusion_count': len(nested_exclusions),
        'character_count': len(payload['characters']),
        'mention_count': len(payload['mentions']),
        'characters': {
            cid: {
                'canonical_surface': row['canonical_surface'],
                'first_mention_chapter': row['first_mention_chapter'],
                'name_usable_from': row['name_usable_from'],
                'surfaces': [s['surface'] for s in row['surfaces']],
            }
            for cid, row in payload['characters'].items()
        },
        'pass_checks': checks,
        'cost': cost,
        'artifact_dir': str(out),
    }
    _write_json(out / 'summary.json', summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes', action='store_true',
                        help='确认付费与正文外发（DeepSeek）')
    parser.add_argument(
        '--replay-responses', type=Path, default=None,
        help='离线重放：指向含 extract_book.json+resolve.json 的目录'
             '（或 characters_v3_pilot 根）；不发付费请求')
    parser.add_argument('--source-base', type=Path, default=DEFAULT_SOURCE_BASE)
    parser.add_argument('--pilot-base', type=Path, default=DEFAULT_PILOT_BASE)
    parser.add_argument('--dotenv', type=Path, default=None,
                        help='可选 .env 路径；默认工作树/仓根 .env')
    args = parser.parse_args(argv)
    if args.replay_responses is None and not args.yes:
        print(
            '未收到 --yes 或 --replay-responses；没有发请求、没有创建试点输出根',
            file=sys.stderr)
        return 2
    if args.replay_responses is not None and args.yes:
        print('请勿同时指定 --yes 与 --replay-responses', file=sys.stderr)
        return 2
    summary = run_pilot(
        source_base=args.source_base.resolve(),
        pilot_base=args.pilot_base.resolve(),
        dotenv=args.dotenv.resolve() if args.dotenv else None,
        replay_responses=(
            args.replay_responses.resolve() if args.replay_responses else None),
    )
    print(json.dumps({
        'passed': summary['pass_checks']['passed'],
        'pass_checks': summary['pass_checks'],
        'characters': summary['characters'],
        'cost': summary['cost'],
        'artifact_dir': summary['artifact_dir'],
        'resolve_path': summary['resolve_path'],
    }, ensure_ascii=False, indent=2))
    return 0 if summary['pass_checks']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
