"""Blueprint 引用订正的只读诊断；保留问题稿与人物候选，不代替 owner 编辑或接受。"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel import paths
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.store import ArtifactStore
from novel.stages.stage2.checks import _Ctx
from novel.stages.stage2.blueprint.checks import check_plot
from novel.stages.stage2.blueprint.compile import validate_book_sources

REPAIR_RULES = frozenset({'present_ids_dangling', 'plot_global_refs', 'plot_state_continuity'})
RECOVERY = '保留当前蓝图和审阅问题；由 stage2.blueprint 免费输出编辑模板并提交 --apply-edit，不能默认重抽。'


def diagnostics(main: MainText, blueprints: ChapterBlueprints,
                characters: CharacterRegistry, knowledge: KnowledgeGrounding) -> list[dict]:
    """只验证当前机器契约；缺身份不造名字，语义归属仍交原 owner。"""
    try:
        validate_book_sources(blueprints.blueprints, {row.chapter_ordinal: row.text for row in main.root})
    except ValueError as exc:
        raise ArtifactUnreadable('蓝图源文或证据尚未通过当前复验：' + str(exc)) from exc
    outcomes = check_plot(_Ctx(knowledge.model_dump(mode='json', by_alias=True),
        blueprints.model_dump(mode='json', by_alias=True), main.model_dump(mode='json'),
        characters=characters.model_dump(mode='json', by_alias=True)))
    return [{'owner': 'stage2.blueprint', 'chapter_ordinal': finding.output_ordinal,
             'rule': row.rule, 'detail': finding.detail, 'recovery': RECOVERY}
            for row in outcomes if row.rule in REPAIR_RULES for finding in row.findings]


def require_reviewable(rows: list[dict]) -> None:
    if rows:
        raise ArtifactUnreadable('当前蓝图引用待免费订正：' + '; '.join(
            f"第{row['chapter_ordinal']}章 {row['detail']}" for row in rows) + '。' + RECOVERY)


def read_repair_diagnostics(base, sid: str, *, code: str | None = None) -> dict | None:
    """供队列和只读视图复用；不存在与已检无问题分开，来源未知不降为空结果。"""
    from novel.orchestrator.state import readonly_ctx
    from novel import prov
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage2.blueprint', prov.code_version() if code is None else code))
    rel = paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
    rel = rel if store.exists(rel) else STAGE2_CHAPTER_BLUEPRINTS
    if not store.exists(rel):
        return None
    sources = ((STAGE1_MAIN_TEXT, MainText), (rel, ChapterBlueprints),
               (STAGE2_CHARACTERS, CharacterRegistry), (STAGE2_GLOBAL_GROUNDING, KnowledgeGrounding))
    hashes = {path: store.full_sha256_or_none(path) for path, _ in sources}
    values = []
    for path, model in sources:
        options = {'replaced_input_keys': (STAGE2_GLOBAL_GROUNDING, STAGE2_BLUEPRINT_CHARACTER_PROJECTION)} if path == rel else {}
        values.append(store.read_own(model, path, require_current=True, **options)[0])
    rows = diagnostics(*values)
    current_rel = (paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
                   if store.exists(paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS))
                   else STAGE2_CHAPTER_BLUEPRINTS)
    if current_rel != rel or hashes != {path: store.full_sha256_or_none(path) for path, _ in sources}:
        raise HumanEditProtected('读取 Blueprint 免费订正诊断期间来源改变')
    return {'blueprint_rel': rel, 'source_sha256s': hashes, 'diagnostics': rows}
