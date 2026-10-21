"""当前三层所有权、源事件修订与依赖方向的真实边界反例。"""
from __future__ import annotations
import ast
from copy import deepcopy
from pathlib import Path
import pytest
from contracts.invariants import NODES
from contracts.schema.dependency import STR06_DEPENDENCY_RULES
from contracts.story_paths import STORY_ARTIFACTS
from novel.stages.stage2 import story as source_engine
from tests.story_facilities import pipeline_materials


def test_each_current_story_artifact_has_one_registered_writer():
    for rel in STORY_ARTIFACTS:
        writers = [node for node, spec in NODES.items() if rel in spec.produces]
        assert len(writers) == 1, (rel, writers)


def test_book_cannot_replace_local_event_or_invent_unobserved_event():
    chapters, index, wires = pipeline_materials()
    observed = {n: source_engine.compile_chapter(wires[f'ch{n:02d}'], index=index, chapter_ordinal=n)
                for n in range(1, len(chapters) + 1)}
    source_engine.compile_book(wires['book'], index=index, chapters=observed)
    bad = deepcopy(wires['book'])
    bad['events']['invented_event'] = next(iter(bad['events'].values()))
    with pytest.raises(ValueError):
        source_engine.compile_book(bad, index=index, chapters=observed)


def test_source_gap_routes_to_chapter_instead_of_silent_book_rewrite():
    chapters, index, wires = pipeline_materials()
    observed = {n: source_engine.compile_chapter(wires[f'ch{n:02d}'], index=index, chapter_ordinal=n)
                for n in range(1, len(chapters) + 1)}
    wire = deepcopy(wires['book'])
    wire['source_issues'] = [{'chapter_ordinal': 1, 'item': '遗漏事件', 'path': ['events'],
        'operation': 'append', 'kind': 'missing', 'message': '本章缺少原文已有的行动',
        'evidence_refs': [next(iter(index.chapter(1)))]}]
    with pytest.raises(ValueError) as raised:
        source_engine.compile_book(wire, index=index, chapters=observed)
    assert raised.value.node == 'str06.stage2.chapters'
    assert raised.value.source_issues[0].chapter_ordinal == 1


def test_pure_engines_obey_current_dependency_directions():
    root = Path(__file__).resolve().parents[1]
    checked = set()
    for rule in STR06_DEPENDENCY_RULES:
        path = root.joinpath(*rule.module.split('.'))
        paths = tuple(path.glob('*.py')) if path.is_dir() else (path.with_suffix('.py'),)
        assert paths
        for file in paths:
            tree = ast.parse(file.read_text(encoding='utf-8-sig'))
            imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
            imports += [name.name for n in ast.walk(tree) if isinstance(n, ast.Import) for name in n.names]
            for module in imports:
                assert not any(module == bad or module.startswith(bad + '.') for bad in rule.forbids), (file, module)
            checked.add(file)
    assert len(checked) >= 9
