#!/usr/bin/env python3
"""Build or verify the exact, text-free current-schema corpus manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from contracts.schema import (
    CorpusArtifactEntry, CorpusBookEntry, CorpusLedgerSummary, CorpusManifest,
    CorpusSkipExpectation, LedgerTokenSummary, artifact_spec, decode_artifact,
)
from novel.llm.ledger import LEDGER_REL
from novel.paths import derive_source_meta


_BYTE_ARTIFACTS = (
    'stage0/clean_text.txt',
    'stage5/draft.md',
    'stage5/deep/draft.md',
    'stage5/shallow/draft.md',
    'stage7/book.txt',
)
_TOKEN_COLUMNS = (
    'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
    'completion_tokens', 'reasoning_tokens',
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _book_dirs(base: Path) -> list[Path]:
    return sorted(
        (path for path in base.iterdir()
         if path.is_dir() and not path.name.startswith('_')),
        key=lambda path: derive_source_meta(path.name)[0],
    )


def _artifact_inventory(book: Path) -> tuple[list[CorpusArtifactEntry], dict[str, Any], set[str]]:
    entries: list[CorpusArtifactEntry] = []
    payloads: dict[str, Any] = {}
    codes: set[str] = set()
    for stage in sorted(book.glob('stage[0-7]')):
        for path in sorted(stage.rglob('*.json')):
            rel = path.relative_to(book).as_posix()
            spec = artifact_spec(rel)
            if spec is None:
                raise ValueError(
                    f'{book.name}/{rel}: 未登记 JSON；不能从 manifest 中静默忽略')
            decoded = decode_artifact(rel, _json(path))
            payload = spec.model.model_validate(decoded.payload).model_dump(
                by_alias=True, mode='json')
            entries.append(CorpusArtifactEntry(rel=rel, schema_version=decoded.version))
            payloads[rel] = payload
            if isinstance(payload, dict):
                code = payload.get('code')
                provenance = payload.get('_provenance')
                if isinstance(code, str) and code:
                    codes.add(code)
                if isinstance(provenance, dict) and isinstance(provenance.get('code'), str):
                    codes.add(provenance['code'])
    for rel in _BYTE_ARTIFACTS:
        if (book / Path(rel)).is_file():
            entries.append(CorpusArtifactEntry(rel=rel, schema_version=None))
    entries.sort(key=lambda item: item.rel)
    return entries, payloads, codes


def _ledger_rows(base: Path) -> list[dict[str, Any]]:
    path = base.joinpath(*LEDGER_REL)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{path}:{number}: ledger JSONL 损坏：{exc}') from exc
        if not isinstance(row, dict):
            raise ValueError(f'{path}:{number}: ledger 行必须是 object')
        rows.append(row)
    return rows


def _counter(rows: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    result = Counter(
        '<null>' if row.get(key) is None else str(row.get(key)) for row in rows)
    return dict(sorted(result.items()))


def _token_summary(rows: list[dict[str, Any]], key: str) -> LedgerTokenSummary:
    values: list[int] = []
    for index, row in enumerate(rows, start=1):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f'ledger row {index} 的 {key} 非非负整数/null：{value!r}')
        values.append(value)
    return LedgerTokenSummary(
        total=sum(values), reported_rows=len(values), null_rows=len(rows) - len(values))


def _ledger_summary(rows: list[dict[str, Any]], sid: str) -> CorpusLedgerSummary:
    selected = [row for row in rows if str(row.get('sid') or '') == sid]
    for index, row in enumerate(selected, start=1):
        prompt = row.get('prompt_tokens')
        hit, miss = row.get('prompt_cache_hit_tokens'), row.get('prompt_cache_miss_tokens')
        for key, value in (
            ('prompt_tokens', prompt),
            ('prompt_cache_hit_tokens', hit),
            ('prompt_cache_miss_tokens', miss),
        ):
            if (value is not None
                    and (isinstance(value, bool) or not isinstance(value, int) or value < 0)):
                raise ValueError(
                    f'{sid} ledger row {index}: {key} 非非负整数/null：{value!r}')
        if hit is not None and miss is not None and prompt is not None and hit + miss != prompt:
            raise ValueError(
                f'{sid} ledger row {index}: provider hit({hit}) + miss({miss}) != prompt({prompt})')
    return CorpusLedgerSummary(
        rows=len(selected),
        wire_attempts=sum(row.get('cached') is False for row in selected),
        local_cache_hits=sum(row.get('cached') is True for row in selected),
        outcomes=_counter(selected, 'outcome'),
        models=_counter(selected, 'model'),
        reasoning_efforts=_counter(selected, 'reasoning_effort'),
        **{key: _token_summary(selected, key) for key in _TOKEN_COLUMNS},
    )


def observe(base: str | Path) -> tuple[list[CorpusBookEntry], str, str]:
    """Return exact books, their common producing code identity, and inventory hash."""
    root = Path(base).resolve()
    if not root.is_dir():
        raise ValueError(f'corpus base 不存在或不是目录：{root}')
    ledger = _ledger_rows(root)
    books: list[CorpusBookEntry] = []
    all_codes: set[str] = set()
    for book in _book_dirs(root):
        sid, _title, _platform = derive_source_meta(book.name)
        artifacts, payloads, codes = _artifact_inventory(book)
        report = payloads.get('stage0/removal_report.json')
        main = payloads.get('stage1/main_text.json')
        if not isinstance(report, dict) or not isinstance(report.get('source_sha1'), str):
            raise ValueError(f'{book.name}: 缺 current stage0/removal_report.json source_sha1')
        if not isinstance(main, list) or not main:
            raise ValueError(f'{book.name}: 缺 current stage1/main_text.json 章节')
        all_codes.update(codes)
        books.append(CorpusBookEntry(
            sid=sid,
            source_sha1=report['source_sha1'],
            chapters=len(main),
            artifacts=artifacts,
            ledger=_ledger_summary(ledger, sid),
        ))
    books.sort(key=lambda item: item.sid)
    if not books:
        raise ValueError(f'{root}: 没有书目录')
    if len(all_codes) != 1:
        raise ValueError(
            f'current corpus 必须由同一冻结代码身份生成，实得 {sorted(all_codes)}')
    inventory = [
        {
            'sid': book.sid,
            'source_sha1': book.source_sha1,
            'chapters': book.chapters,
            'artifacts': [item.model_dump(mode='json') for item in book.artifacts],
        }
        for book in books
    ]
    return books, next(iter(all_codes)), _canonical_sha256(inventory)


def verify(base: str | Path, manifest: CorpusManifest) -> None:
    books, code, inventory_sha = observe(base)
    if code != manifest.code_identity:
        raise ValueError(
            f'corpus code identity 漂移：manifest={manifest.code_identity}, disk={code}')
    if inventory_sha != manifest.artifact_inventory_sha256:
        raise ValueError(
            'corpus artifact inventory hash 漂移：书目/原文 hash/章节数/产物路径或版本已变化')
    expected = [book.model_dump(mode='json') for book in manifest.books]
    actual = [book.model_dump(mode='json') for book in books]
    if actual != expected:
        raise ValueError('corpus manifest 与盘面精确内容不一致（含 ledger 汇总）')


def build_manifest(
    base: str | Path, *, commands: list[str],
    skips: list[CorpusSkipExpectation] | None = None,
) -> CorpusManifest:
    books, code, inventory_sha = observe(base)
    return CorpusManifest(
        manifest_version=1,
        generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        code_identity=code,
        artifact_inventory_sha256=inventory_sha,
        commands=commands,
        books=books,
        skip_allowlist=sorted(skips or [], key=lambda item: item.nodeid),
    )


def _load_skips(path: Path | None) -> list[CorpusSkipExpectation]:
    if path is None:
        return []
    raw = _json(path)
    if not isinstance(raw, list):
        raise ValueError('skip file 顶层必须是数组')
    return [CorpusSkipExpectation.model_validate(item) for item in raw]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    build = sub.add_parser('build')
    build.add_argument('--base', required=True)
    build.add_argument('--output', required=True)
    build.add_argument('--command', action='append', required=True)
    build.add_argument('--skip-file')
    check = sub.add_parser('verify')
    check.add_argument('--base', required=True)
    check.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)

    if args.action == 'build':
        manifest = build_manifest(
            args.base, commands=list(args.command),
            skips=_load_skips(Path(args.skip_file)) if args.skip_file else [])
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest.model_dump(mode='json'), ensure_ascii=False,
                       indent=2, sort_keys=True) + '\n',
            encoding='utf-8', newline='\n')
        print(output)
        return 0

    manifest = CorpusManifest.model_validate(_json(Path(args.manifest)))
    verify(args.base, manifest)
    print('corpus manifest: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
