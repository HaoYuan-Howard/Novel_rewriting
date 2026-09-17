#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_docs_references.py —— 文档里的**引用**不许悬空。

【为什么要有】2026-08-10 的 stage1 审阅抓到五处问题，其中**两处是同一个形状**：
  · `stage1.md` §六 指着 `tests/fixtures_stage1.py`，而盘上叫 `fixtures_ingest.py`
  · `R-0.3` 拆成 `R-0.3a`/`R-0.3b` 之后，还有一处引用留在旧编号上

两处都**不会报错**——读的人会去找一个不存在的文件、或去查一条不存在的需求，
然后按自己的猜测把它读通。这正是判据 23 的文档版：
**读着已改名东西的消费方不会报错，它只是安静地什么也不做。**

本文件把这一类变成会红的测试。它查两件事：
  ① 文档里出现的每个 `R-x.y` / `Q-x` / `B-xx` 编号，在冻结后端契约里真的存在
  ② 文档里出现的每个**仓内路径**，盘上真的存在
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from contracts import requirements as reqs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = reqs.load()

# Git 给出 tracked + 当前未忽略新增文件；目录排除是用途边界，不是固定文档名单。
_EXCLUDED_PARTS = frozenset({
    '.git', 'node_modules', 'vendor', 'vendors', 'dist', 'outputs', 'Output',
    'Novel_data', 'Test_data', '.tr', '.test_cache', '.pytest_cache', '.llm_cache',
    '__pycache__', '.venv', 'venv',
})


def _git_paths(*, tracked_only: bool = False) -> set[str]:
    args = ['git', '-c', f'safe.directory={Path(ROOT).as_posix()}', 'ls-files', '-z', '--cached']
    if not tracked_only:
        args += ['--others', '--exclude-standard']
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=True,
                            text=True, encoding='utf-8')
    return {item for item in result.stdout.split('\0') if item}


def _purpose(doc: str) -> str:
    path = Path(doc)
    if path.name == 'NOTICE.md':
        return 'license'
    if 'fixtures' in path.parts:
        return 'fixture_source'
    if path.name.startswith('AUDIT_'):
        return 'temporary_plan'
    return 'normative'


#: 只有这些前缀才当「仓内路径」看。`stage0/clean_text.txt` 这类是**产物路径**，不在仓里。
REPO_PREFIXES = (
    'tests/', 'contracts/', 'tools/', 'novel/', 'docs/', 'web/', 'scripts/',
    '.workflow/', '.agents/', '.claude/',
)

#: **文档明说「它不在仓里」的那几条路径**，逐条登记、逐条写理由。
#:
#: 【为什么是手写登记表，而不是「凡被 .gitignore 忽略就放行」】
#: 两种烂法成本相同、方向相反（同 `novel/api/enums.py` 的三张登记表）：
#: 自动放行**静默地烂**——一整类路径被挪出扫描面，从此这里写错谁都不知道；
#: 手写登记**响亮地烂**——文档新引一条不存在的路径当场红，必须二选一：
#: 补出这个文件，或者登记进来并写出「为什么它本来就不该在仓里」。
#: ⚠ 这不是「已知豁免」，是一份账：每条理由必须说得出**文档那句话在说什么**。
#: ⚠ 只放行**逐字全等**的路径，不做前缀匹配——放行 `.claude/settings.local.json`
#: 不该顺带把 `.claude/settings.local.json.bak` 一起放行。
_DECLARED_ABSENT: dict[str, str] = {
    '.claude/settings.local.json': (
        '被 `.gitignore` 忽略、只存在于各人自己的工作区。`docs/OPERATIONS.md` '
        '里引用它的那段说的**正是它不存在**（「根 `.env`、`outputs/`、`Test_data/`…'
        '不存在」那一段）⇒ 要求它在盘上等于要求文档自我否定，任何新 clone / worktree '
        '都会红，而红的是宿主环境不是文档'),
    'tools/workflow_metrics.py': (
        '已随 Stage2 语义审阅退役（2026-09，工作流 v7）：读数主体是已退役的 '
        'Stage2 Commander 盘面。docs 里引用它的段落说的正是这次退役；'
        '历史实现按 Git 历史读取'),
    'tools/workflow_pairs.py': (
        '只消费已删除的 workflow_metrics 冻结报告，随同退役（见 docs/DECISIONS.md）'),
    'tools/stage2_acceptance.py': (
        '旧五轮实验解释器已退出维护并删除；docs 引用它的段落只解释历史边界'),
    'tools/verify_agent_e2e.py': (
        '随单 Executor 硬切删除（C3/C5）；docs 引用它的段落说明 Agent CLI '
        '整书验收工具与 claude-code-glm driver 已退役，不重建运行时对象'),
    'novel/stages/stage2/core_edits.py': (
        '统一双 owner typed 编辑批次已随 Stage2 语义审阅退役；'
        '独立 CharactersEdit/GlobalEdit 是唯一 typed 编辑入口'),
    'docs/schema/stage2_review.md': (
        '随 Stage2 语义审阅退役删除；docs 引用它的段落说明该退役'),
}

_ID = re.compile(r'`(R-\d+\.\d+[a-z]?|Q-\d+|B-\d+)`')
#: ⚠ 允许尾随 `::符号` 或 `['键']`：`novel/paths.py::edit_rel` 这种写法在
#: skill 经验库里是标准形态，只取路径那一段，符号那一段由 skill 校验器自己查。
_REPO_ROOT_PATTERN = '|'.join(
    re.escape(prefix.rstrip('/')) for prefix in REPO_PREFIXES)
_PATH = re.compile(
    r'`((?:' + _REPO_ROOT_PATTERN + r')/[A-Za-z0-9_./-]+)'
    r'(?:::[A-Za-z0-9_]+(?:\[[^\]]*\])?)?`')


def _docs() -> list[str]:
    return sorted(str(Path(ROOT) / rel) for rel in _git_paths()
                  if Path(rel).suffix.lower() == '.md'
                  and not set(Path(rel).parts) & _EXCLUDED_PARTS
                  and (Path(ROOT) / rel).is_file())


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, '/')


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_requirement_id_exists(doc):
    """引用一个不存在的需求编号 = 需求改过而文档没跟上。"""
    if _purpose(doc) in ('license', 'fixture_source'):
        return
    text = Path(doc).read_text(encoding='utf-8')
    known = set(DOC.requirements)
    missing = sorted({m for m in _ID.findall(text) if m not in known})
    assert not missing, (
        f'{_rel(doc)} 引用了不存在的编号：{missing}\n'
        f'→ 需求改过（拆分/改名/删除）而这里没跟上。'
        f'改编号时要 grep 全部文档，不只是校验器'
    )


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_referenced_repo_path_exists(doc):
    """引用一个不存在的文件 = 读的人会去找它，然后按自己的猜测把文档读通。"""
    text = open(doc, encoding='utf-8').read()
    bad = []
    for path in sorted(set(_PATH.findall(text))):
        if path in _DECLARED_ABSENT:      # 文档明说它不在仓里，见上表
            continue
        # 目录形（以 / 结尾）与文件形都接受
        full = os.path.join(ROOT, path)
        if not (os.path.exists(full) or os.path.exists(full.rstrip('/'))):
            bad.append(path)
    assert not bad, (
        f'{_rel(doc)} 引用了盘上不存在的路径：{bad}\n'
        f'→ 文件改名/搬家了而文档没跟上。这类引用不会报错，只会静默误导。'
        f'若它本来就不该在仓里（被 .gitignore 忽略等），'
        f'登记进 `_DECLARED_ABSENT` 并写出理由，别把它从扫描面上摘掉'
    )


_LINK = re.compile(r'!?\[[^\]\n]*\]\((?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)]*)?\)')


def _prose(text: str) -> str:
    return re.sub(r'^(`{3,}|~{3,}).*?^\1[^\n]*$', '', text, flags=re.M | re.S)


def _anchors(path: Path) -> set[str]:
    text = _prose(path.read_text(encoding='utf-8'))
    seen = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    for heading in re.findall(r'^ {0,3}#{1,6}\s+(.+?)\s*#*$', text, re.M):
        heading = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        candidate, suffix = slug, 0
        while candidate in seen:
            suffix += 1
            candidate = f'{slug}-{suffix}'
        seen.add(candidate)
    return seen


def _local_targets(doc: str):
    for angle, plain in _LINK.findall(_prose(Path(doc).read_text(encoding='utf-8'))):
        target = angle or plain
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target):
            continue
        path, _, anchor = unquote(target).partition('#')
        path = path.split('?', 1)[0]
        full = (Path(doc) if not path else Path(ROOT) / path.lstrip('/')
                if path.startswith('/') else Path(doc).parent / path)
        yield target, full.resolve(), anchor


def _missing_local_links(doc: str) -> list[str]:
    return sorted({target for target, full, anchor in _local_targets(doc)
                   if not full.exists() or (anchor and full.suffix.lower() == '.md'
                                             and anchor not in _anchors(full))})


def _references(doc: str) -> set[Path]:
    linked = {full for _, full, _ in _local_targets(doc) if full.suffix.lower() == '.md'}
    # 工程指南允许仓内路径形式；同目录文档从所在文件夹解析。
    for token in re.findall(r'`([^`\n]+?\.md)(?:::[^`]+)?`', Path(doc).read_text(encoding='utf-8')):
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', token):
            continue
        full = Path(ROOT) / token
        if not full.is_file():
            full = Path(doc).parent / token
        if full.is_file():
            linked.add(full.resolve())
    return linked


def _orphans(docs: list[str]) -> set[Path]:
    targets = {Path(doc).resolve() for doc in docs if _purpose(doc) in ('normative', 'temporary_plan')}
    reached, pending = set(), [Path(ROOT).resolve() / 'AGENTS.md']
    while pending:
        current = pending.pop()
        if current in reached or not current.is_file():
            continue
        reached.add(current)
        pending.extend(_references(str(current)) - reached)
    return targets - reached


def _defined_names(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
    return names


def _missing_symbols(doc: str) -> list[str]:
    missing = []
    defined = {}
    text = Path(doc).read_text(encoding='utf-8')
    for path, symbol in re.findall(r'`([A-Za-z0-9_./-]+\.(?:py|md))::([^`\[\n]+)(?:\[[^`]*\])?`', text):
        full = Path(ROOT) / path
        if not full.is_file():
            full = Path(doc).parent / path
        if not full.is_file():
            missing.append(path + '::' + symbol)
        elif full.suffix == '.py':
            if full not in defined:
                defined[full] = _defined_names(full)
            name = symbol.split('(', 1)[0]
            if any(part not in defined[full] for part in name.split('.')):
                missing.append(path + '::' + symbol)
        elif full.suffix == '.md':
            content = full.read_text(encoding='utf-8')
            if re.fullmatch(r'(?:R-\d+(?:\.\d+[a-z]?)?|Q-\d+|B-\d+)', symbol):
                known = set(DOC.requirements)
                if symbol in known or any(value.startswith(symbol + '.') for value in known):
                    continue
            if re.fullmatch(r'TODO-\d+', symbol) and re.search(
                    rf'^#+[^\n]*\b{re.escape(symbol)}\b', content, re.M):
                continue
            slug = re.sub(r'[^\w\- ]', '', symbol.lower()).replace(' ', '-')
            if slug not in _anchors(full):
                missing.append(path + '::' + symbol)
    return missing


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_critical_symbols_and_guide_headings_exist(doc):
    assert not _missing_symbols(doc), f'{_rel(doc)} 的符号或标题悬空：{_missing_symbols(doc)}'


def test_every_document_has_a_purpose_and_development_entry():
    docs = _docs()
    assert docs and str(Path(ROOT) / 'AGENTS.md') in docs
    assert {_purpose(doc) for doc in docs} <= {'normative', 'temporary_plan', 'license', 'fixture_source'}
    assert not _orphans(docs), f'新 MD 未接入任务阅读入口：{sorted(map(str, _orphans(docs)))}'


@pytest.mark.parametrize('doc', _docs(), ids=_rel)
def test_every_local_markdown_link_exists(doc):
    missing = _missing_local_links(doc)
    assert not missing, f'{_rel(doc)} 的本地 Markdown 链接悬空：{missing}'


def test_new_markdown_is_discovered_and_unlinked_docs_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', str(tmp_path))
    paths = {'AGENTS.md', 'novel/new/README.md', 'docs/new.md', 'docs/deleted.md',
             'web/node_modules/dependency/AGENTS.md', 'outputs/book/report.md'}
    monkeypatch.setattr(sys.modules[__name__], '_git_paths', lambda **_: paths)
    for rel in paths - {'docs/deleted.md'}:
        file = tmp_path / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('# 新文档\n', encoding='utf-8')
    docs = _docs()
    assert {Path(p).relative_to(tmp_path).as_posix() for p in docs} == {
        'AGENTS.md', 'novel/new/README.md', 'docs/new.md'}
    assert _orphans(docs) == {tmp_path / 'docs/new.md', tmp_path / 'novel/new/README.md'}
    (tmp_path / 'AGENTS.md').write_text('[新增](docs/new.md) `novel/new/README.md`\n', encoding='utf-8')
    assert not _orphans(docs)


def test_deleted_markdown_link_is_detected_relative_to_its_document(tmp_path):
    parent = tmp_path / 'docs'
    parent.mkdir()
    (tmp_path / 'AGENTS.md').write_text('# 入口\n', encoding='utf-8')
    (parent / 'with space.md').write_text('# 有效目标\n', encoding='utf-8')
    doc = parent / 'AGENTS.md'
    doc.write_text(
        '[父入口](../AGENTS.md#入口) [空格](with%20space.md)\n# 入口\n'
        '[失效计划](removed.md) [远端](https://example.com/docs) [本页](#入口)\n'
        '```markdown\n[示例](not-a-real-file.md)\n```\n', encoding='utf-8')
    assert _missing_local_links(str(doc)) == ['removed.md']


def _tracked_paths() -> set[str]:
    return _git_paths(tracked_only=True)


def test_declared_absent_entries_are_still_live_and_still_in_no_repo():
    """登记表自己的守卫：一条**死登记**比没有登记更坏。

    两侧都查，因为两侧都会烂：
      · 没有任何文档还在引用它 ⇒ 这条登记已经是死配置，删掉；
      · 它**已经被 git 跟踪**了 ⇒ 放行的前提没了，这条必须撤销，
        否则它从此把一条真能查的路径静默挪出扫描面（那正是本表拒绝
        「凡 gitignore 就放行」的同一个理由）。
    """
    referenced = set()
    for doc in _docs():
        referenced |= set(_PATH.findall(open(doc, encoding='utf-8').read()))
    tracked = _tracked_paths()
    assert tracked, 'git ls-files 一条都没返回 ⇒ 下面那半条守卫根本没在查'
    for path, why in _DECLARED_ABSENT.items():
        assert why.strip(), f'{path} 登记了却没写理由'
        assert path in referenced, (
            f'{_DECLARED_ABSENT} 里的 {path} 已经没有任何文档引用 ⇒ 死配置，删掉这一条')
        prefix = path.rstrip('/') + '/'
        hits = sorted(
            item for item in tracked if item == path.rstrip('/') or item.startswith(prefix))
        assert not hits, (
            f'{path} 已经被 git 跟踪了（{hits[:3]}）⇒ 放行的前提没了，'
            f'把这条从 `_DECLARED_ABSENT` 撤销，让它回到扫描面上')


def test_reference_parsers_do_not_pass_on_empty_scans(tmp_path):
    doc = tmp_path / 'example.md'
    doc.write_text('`R-1.1` `Q-1` `B-11` `novel/missing.py`\n'
                   '[标题](#稳定标题)\n## 稳定标题\n', encoding='utf-8')
    text = doc.read_text(encoding='utf-8')
    assert _ID.findall(text) == ['R-1.1', 'Q-1', 'B-11']
    assert _PATH.findall(text) == ['novel/missing.py']
    assert not _missing_local_links(str(doc))
    doc.write_text(text.replace('## 稳定标题', '## 已改名'), encoding='utf-8')
    assert _missing_local_links(str(doc)) == ['#稳定标题']


def test_deleted_python_symbol_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', str(tmp_path))
    source = tmp_path / 'novel/example.py'
    source.parent.mkdir()
    source.write_text('def current(): pass\n', encoding='utf-8')
    doc = tmp_path / 'AGENTS.md'
    doc.write_text('`novel/example.py::current` `novel/example.py::removed`', encoding='utf-8')
    assert _missing_symbols(str(doc)) == ['novel/example.py::removed']
