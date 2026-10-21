"""当前三层的依赖方向；语义引擎保持纯计算，跨层仅消费正式contract。"""
from __future__ import annotations
from typing import Final, NamedTuple

class DependencyRule(NamedTuple):
    module: str
    allows: tuple[str, ...]
    forbids: tuple[str, ...]

STR06_DEPENDENCY_RULES: Final = (
    DependencyRule('novel.stages.stage2', ('contracts.schema.*', 'novel.core.source_index'),
        ('novel.stages.stage4', 'novel.stages.stage5', 'novel.stages.stage7', 'novel.llm', 'novel.store')),
    DependencyRule('novel.stages.stage4', ('contracts.schema.*',),
        ('novel.stages.stage2', 'novel.stages.stage5', 'novel.stages.stage7', 'novel.llm', 'novel.store')),
    DependencyRule('novel.stages.stage5', ('contracts.schema.*', 'novel.core.story_prose'),
        ('novel.stages.stage2', 'novel.stages.stage4', 'novel.stages.stage7', 'novel.llm', 'novel.store')),
    DependencyRule('novel.stages.stage7', ('contracts.schema.*', 'novel.core.story_prose'),
        ('novel.stages.stage2', 'novel.stages.stage4', 'novel.stages.stage5', 'novel.llm', 'novel.store')),
    DependencyRule('novel.core.story_prose', ('contracts.schema.*',),
        ('novel.stages', 'novel.llm', 'novel.store', 'novel.orchestrator', 'novel.api', 'novel.views')),
)
