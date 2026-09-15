from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.llm.client import DEFAULT_RUNGS, PRO_FALLBACK_MODEL
from novel.stages.stage0.removal import _context, run_approve, run_source
from novel.stages.stage1.partition import run_partition
from tests.fixtures_ingest import body
from tools import current_corpus_paid_preview as P

#: 阶梯档数。**从生产落点取，不写死。**
#: wire 上限恒是「逻辑请求数 × 档数 × 每档采样数」，于是这些期望值里被钉住的是
#: **逻辑请求数**（136 / 4 / 6 …），而两个乘数只是已知乘数。
#: ⚠⚠ 2026-08-24：Stage3 v2 变成免费机械节点后，基础逻辑请求数从 180
#: 收到 **136**；那 44 次是两本书原先的逐章定性抽取，不得继续计入付费范围。随后
#: `stage2.blueprint` 默认从每章两相的 v2 翻到每章一相的 v3.1，17+25 章再少 42 个
#: 逻辑请求，基础数成为 94。Stage2 v5 又把摘要改为 fact atoms 的确定性投影，
#: 双书各少一次书级摘要调用，当时基础数成为 92。2026-08-25
#: Stage4 拆为 design + plan 两个独立公开节点后，Stage4A 又把内部执行固定为
#: 「1 份整书策略 + N 份逐章私有 session」；17+25 章双书的 A 从 2 条变为
#: 44 条逻辑请求，B 仍为 2 条，现役基础数因此为 **136**。公开付费 dispatch
#: 仍是每书 A/B 各一条，不随内部逻辑拓扑增加。
#: 独立人物节点为双书各增加一条整书请求，现役基础数为 138。
#: `_logical_topology` 从现役契约派生；这里钉 138 是在守当前默认真实 consumer，
#: 翻默认时要随生产拓扑一起改，不能把预算预览留在旧臂。
#: ⚠ 2026-08-18 阶梯由 4 档降到 3 档（删掉被服务端静默映射回 `high` 的 `medium`，
#: 见 `novel/llm/client.py::LADDER`）⇒ 全部 wire 上限跟着降 1/4，**那是正确结果不是回归**。
#: ⚠ 2026-08-20 起答案流丢失同档先重采一次（`novel/llm/client.py::
#: MAX_SAMPLES_PER_RUNG`）⇒ wire 上限再 ×2，同理是正确结果不是回归。
from novel.llm.client import MAX_SAMPLES_PER_RUNG

_RUNGS = len(DEFAULT_RUNGS)


def test_workflow_role_budget_matches_current_seats_and_ambiguous_units():
    """Stage2 语义审阅退役后 stage2.* 只剩 executor 席位，不再授予 Commander 预算。"""
    budget = P.workflow_role_budget(17)
    rows = budget['units']
    assert not [row for row in rows if row['role'] == 'commander'
                and row['node'].startswith('stage2.')]
    binding = next(row for row in rows if row['node'] == 'stage4.plan'
                   and row['role'] == 'executor')
    assert (binding['minimum_units'], binding['maximum_units']) == (0, 17)
    assert binding['content_hard_limit'] == 68
    assert budget['token_estimate'] is None and budget['cost_estimate'] is None
_FLASH_MODELS = len(P.models_for(P.config.DEFAULT_MODEL))
_PRO_MODELS = len(P.models_for(PRO_FALLBACK_MODEL))

# 138 条非语义逻辑请求里，双书 Stage4 A（18+26）/B（1+1）共
# 46 条钉死 Pro；其余 92 条默认 Flash 并各带一次 Pro 兜底。
# stage6.semantic 判官退役（2026-09-15）后付费面不再有语义逻辑请求。
_BASE_WIRE = (92 * _FLASH_MODELS + 46 * _PRO_MODELS) \
    * _RUNGS * MAX_SAMPLES_PER_RUNG
_HOOK_RETRY_WIRE = 6 * _FLASH_MODELS * MAX_SAMPLES_PER_RUNG

#: 当前语料范围的 wire 硬上限：基础 138 按各自模型链放大、
#: 每档采样数，再加双书各剩 3 次、合计 6 条 stage6 定向修订 × 采样数；
#: 这是每书空缓存首次生成后共享的余额，不是三槽各领预算（它们走**单档**
#: `HOOK_RETRY_RUNGS`，只乘采样数不乘档数）。⚠ 并列的 `external_hard_limit=588`
#: 是**已有实验**的历史边界，不随当前阶梯变。
_FULL_WIRE = _BASE_WIRE + _HOOK_RETRY_WIRE


ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCES = (
    ROOT / 'Test_data' / '收养三只反派幼崽后_2019402867061048656.txt',
    ROOT / 'Test_data' / '常春杜若_1668963766200045568.txt',
)


def _synthetic_sources(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / 'sources'
    source_dir.mkdir(exist_ok=True)
    sources = (
        source_dir / 'FQ_合成甲_2019402867061048656.txt',
        source_dir / 'FQ_合成乙_1668963766200045568.txt',
    )
    for source, chapters in zip(sources, (17, 25), strict=True):
        source.write_text(body(chapters), encoding='utf-8')
    return sources


def _spec(
    tmp_path: Path,
    *,
    ledger: Path | None = None,
    sources: tuple[Path, Path] | None = None,
) -> P.PreviewSpec:
    sources = sources or _synthetic_sources(tmp_path)
    return P.PreviewSpec.model_validate({
        'historical_ledger': ledger,
        'books': [
            {
                'sid': P.paths.derive_source_meta(source.name)[0],
                'base': tmp_path / f'isolated-{index}',
                'input_kind': 'source',
                'source': source,
            }
            for index, source in enumerate(sources, 1)
        ],
    })


def _shared_spec(
    tmp_path: Path,
    *,
    input_kind: str = 'source',
    sources: tuple[Path, Path] | None = None,
) -> P.PreviewSpec:
    sources = sources or _synthetic_sources(tmp_path)
    shared = tmp_path / 'NOVEL_OUTPUT'
    return P.PreviewSpec.model_validate({
        'books': [
            {
                'sid': P.paths.derive_source_meta(source.name)[0],
                'base': shared,
                'input_kind': input_kind,
                **({'source': source} if input_kind == 'source' else {}),
            }
            for source in sources
        ],
    })


@pytest.mark.corpus
@pytest.mark.skipif(
    not all(source.is_file() for source in REAL_SOURCES),
    reason='缺少现役双书 Test_data 语料',
)
def test_real_current_corpus_has_the_frozen_17_and_25_chapter_shape(tmp_path):
    plan = P.build_plan(
        _spec(tmp_path, sources=REAL_SOURCES),
        token_counter=lambda _request: 1,
    )
    assert [(book['sid'], book['chapters']) for book in plan['books']] == [
        ('2019402867061048656', 17),
        ('1668963766200045568', 25),
    ]


def test_final_topology_lists_16_dispatches_and_real_conditional_limits(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 123)
    aggregate = plan['aggregate']
    assert aggregate == {
        'production_cli_dispatches': 16,
        'base_nonsemantic_logical_requests': 138,
        'base_nonsemantic_ladder_wire_hard_limit': _BASE_WIRE,
        'semantic_logical': 0,
        'semantic_wire_hard_limit': 0,
        'stage6_conditional_candidate_retry_logical': 6,
        'stage6_conditional_candidate_retry_wire_hard_limit':
            _HOOK_RETRY_WIRE,
        'full_conditional_logical_hard_limit': 144,
        'full_conditional_wire_hard_limit': _FULL_WIRE,
        'full_conditional_context_token_hard_ceiling': _FULL_WIRE * 1_000_000,
    }
    assert sorted(book['chapters'] for book in plan['books']) == [17, 25]

    assert not any(row['node'] == 'stage3.style' for row in plan['dispatches'])

    blueprints = [row for row in plan['dispatches']
                  if row['node'] == 'stage2.blueprint']
    assert sorted(row['base_logical_requests'] for row in blueprints) == [17, 25]
    assert all(
        [item['kind'] for item in row['request_envelopes']]
        == ['default']
        for row in blueprints
    ), '默认 v3.1 必须是每章一相；summary 由 fact atoms 确定性投影'

    designs = [row for row in plan['dispatches']
               if row['node'] == 'stage4.design']
    assert sorted(row['base_logical_requests'] for row in designs) == [18, 26]
    assert all(row['conditional_logical_requests'] == 0 for row in designs)
    assert all(
        [(item['kind'], item['max_tokens'])
         for item in row['request_envelopes']]
        == [('book_strategy', 16_000), ('chapter_design', 24_000)]
        for row in designs
    )
    plans = [row for row in plan['dispatches']
             if row['node'] == 'stage4.plan']
    assert [row['base_logical_requests'] for row in plans] == [1, 1]

    hooks = [row for row in plan['dispatches'] if row['node'] == 'stage6.hook']
    assert all('low/no-thinking' in row['conditional_reason'] for row in hooks)
    assert all('整书共用最多 4 次' in row['conditional_reason'] for row in hooks)
    assert all('全部槽位共用其余 3 次' in row['conditional_reason'] for row in hooks)
    assert [(
        row['base_logical_requests'],
        row['conditional_logical_requests'],
        row['logical_request_hard_limit'],
        row['base_ladder_wire_hard_limit'],
        row['conditional_wire_hard_limit'],
        row['wire_attempt_hard_limit'],
    ) for row in hooks] == [(
        1, 3, 4,
        _FLASH_MODELS * _RUNGS * MAX_SAMPLES_PER_RUNG,
        3 * _FLASH_MODELS * MAX_SAMPLES_PER_RUNG,
        _FLASH_MODELS * (_RUNGS + 3) * MAX_SAMPLES_PER_RUNG,
    )] * 2
    for row in hooks:
        retry = next(
            item for item in row['request_envelopes']
            if item['kind'] == 'conditional_candidate_retry')
        assert retry == {
            'kind': 'conditional_candidate_retry',
            'max_tokens': 24_000,
            'models': [P.config.DEFAULT_MODEL, PRO_FALLBACK_MODEL],
            'rungs': [['low', False]],
        }


def test_commands_use_production_builder_and_freeze_env_without_execution(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    assert plan['read_only'] is True and plan['has_execution_entry'] is False
    assert plan['authorization'].startswith('not_granted')
    assert len(plan['dispatches']) == 16
    caches = set()
    for row in plan['dispatches']:
        argv = row['production_cli_argv']
        assert argv[:4] == [P.sys.executable, '-X', 'utf8', '-u']
        assert argv[-1] == '--yes'
        assert '--execute' not in argv and '--effort' not in argv
        env = row['frozen_environment']
        # 冻结环境钉的是**全局默认模型**（`FIXED_MODEL = config.DEFAULT_MODEL`，派生）。
        # 2026-08-19/20 起默认 = flash；语义判官不因此翻臂——`NODES` 里钉死 pro
        #（`contracts/invariants.py::NodeSpec.model` 优先于环境变量）。
        assert env['DEEPSEEK_MODEL'] == P.config.DEFAULT_MODEL
        assert env['DEEPSEEK_THINKING'] == 'on'
        assert env['DEEPSEEK_MAX_RETRIES'] == '0'
        if row['node'] in {'stage4.design', 'stage4.plan'}:
            assert row['models'] == [PRO_FALLBACK_MODEL]
        else:
            assert row['models'] == [P.config.DEFAULT_MODEL, PRO_FALLBACK_MODEL]
        caches.add(env['NOVEL_CACHE_DIR'])
    assert len(caches) == 2
    write = next(row for row in plan['dispatches'] if row['node'] == 'stage5.write')
    assert '--deep' not in write['production_cli_argv']
    assert '--shallow-rest' not in write['production_cli_argv']
    assert '--mode' not in write['production_cli_argv']
    # stage6.semantic 已随判官节点退役（2026-09-15）：付费面不再有语义 dispatch。
    assert not [row for row in plan['dispatches']
                if row['node'] == 'stage6.semantic']
    with pytest.raises(SystemExit):
        P._parser().parse_args(['--execute'])


def test_source_preview_allows_one_canonical_output_root_with_per_sid_isolation(tmp_path):
    spec = _shared_spec(tmp_path)
    plan = P.build_plan(spec, token_counter=lambda _request: 1)
    shared = spec.books[0].base.resolve()

    assert {Path(book['base']) for book in plan['books']} == {shared}
    assert len({Path(book['novel_dir']) for book in plan['books']}) == 2
    assert all(Path(book['novel_dir']).parent == shared for book in plan['books'])
    assert len({Path(book['cache']) for book in plan['books']}) == 2
    assert all(Path(book['cache']).parent.parent == shared / '_service'
               for book in plan['books'])
    assert all(
        Path(row['production_cli_argv'][
            row['production_cli_argv'].index('--base') + 1]).resolve() == shared
        for row in plan['dispatches']
    )
    assert not shared.exists(), '只读 source 预览不得创建共享 output 根'


def test_stage1_preview_reads_two_books_from_one_canonical_output_root(tmp_path):
    source_spec = _shared_spec(tmp_path)
    shared = source_spec.books[0].base
    for book in source_spec.books:
        assert book.source is not None
        novel_dir, removed, _ = run_source(book.source, shared)
        if removed.report.approval.status != 'approved':
            run_approve(
                _context(novel_dir, 'stage0.approve'),
                actor='current-corpus-preview-test',
                reason='为共享根 Stage1 只读预览构造已批准 fixture',
            )
        run_partition(_context(novel_dir, 'stage1.partition'))

    plan = P.build_plan(
        _shared_spec(tmp_path, input_kind='stage1'),
        token_counter=lambda _request: 1,
    )

    assert {book['input_kind'] for book in plan['books']} == {'stage1'}
    assert {Path(book['base']) for book in plan['books']} == {shared.resolve()}
    assert len({Path(book['novel_dir']) for book in plan['books']}) == 2
    assert len({Path(book['cache']) for book in plan['books']}) == 2


def test_book_specs_still_reject_same_sid_and_ancestor_bases(tmp_path):
    payload = _spec(tmp_path).model_dump(mode='python')
    payload['books'][1]['sid'] = payload['books'][0]['sid']
    with pytest.raises(ValueError, match='不同 sid'):
        P.PreviewSpec.model_validate(payload)

    payload = _spec(tmp_path).model_dump(mode='python')
    payload['books'][0]['base'] = tmp_path / 'NOVEL_OUTPUT'
    payload['books'][1]['base'] = tmp_path / 'NOVEL_OUTPUT' / 'nested'
    with pytest.raises(ValueError, match='祖先/子孙'):
        P.PreviewSpec.model_validate(payload)


def test_profiles_and_human_gates_are_explicit_before_paid_calls(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    by_node = {
        row['node']: row
        for row in plan['dispatches'][:len(P.PAID_NODES)]
    }
    # stage5.semantic（2026-08-17）与 stage6.semantic（2026-09-15）先后整节点
    # 下线 ⇒ 付费面上不再有任何语义 dispatch。
    assert 'stage5.semantic' not in by_node
    assert 'stage6.semantic' not in by_node
    # Commander 已退役：s2 gate 由统一机械准入收口，不再有审阅席位接受语。
    assert any('机械准入' in line
               for line in by_node['stage4.design']['human_gate_dependencies'])
    assert not any('Commander' in line
                   for line in by_node['stage4.design']['human_gate_dependencies'])
    assert any('actor/reason' in line for line in by_node['stage2.global']['human_gate_dependencies'])
    assert any('approve' in line for line in by_node['stage7.rename']['human_gate_dependencies'])
    global_version = P.artifact_spec(P.STAGE2_GLOBAL_GROUNDING).current_version
    assert any(
        f'v{global_version} 产物' in line
        for line in by_node['stage2.blueprint']['human_gate_dependencies']
    )
    # ⚠ 收缩臂整臂下线后 stage5.write **没有条件回路**了。这一条断的是 0，不是「忘了写」：
    #   非零就说明有人把第二条付费回路接了回来而预览没跟上。
    write_row = by_node['stage5.write']
    assert write_row['conditional_logical_requests'] == 0
    assert write_row['conditional_wire_hard_limit'] == 0
    assert write_row['conditional_reason'] is None


def test_only_characters_prompt_is_exact_before_upstream_generation(tmp_path):
    requests = []

    def count(request):
        requests.append(request)
        return 321

    plan = P.build_plan(_spec(tmp_path), token_counter=count)
    estimates = plan['token_estimate']['stage2_characters_exact_before_generation']
    assert set(estimates) == {
        '2019402867061048656', '1668963766200045568'}
    for estimate in estimates.values():
        assert estimate['status'] == 'exact_first_request_dynamic_recovery_bounded'
        assert estimate['first_success_input_tokens'] == 321
        assert estimate['exhausted_ladder_input_tokens'] == (
            'unknown_dynamic_recovery_prompts')
        assert estimate['exhausted_ladder_input_token_hard_ceiling'] == (
            _FLASH_MODELS * _RUNGS * MAX_SAMPLES_PER_RUNG * (1_000_000 - 64_000))
        assert estimate['completion_tokens_ladder_upper_bound'] == (
            _FLASH_MODELS * _RUNGS * MAX_SAMPLES_PER_RUNG * 64_000)
        assert estimate['completion_includes_reasoning'] is True
    assert requests
    assert all(row.system == P.stage2_characters.SYSTEM for row in requests)
    assert all('fate_evidence' not in row.system for row in requests)
    assert all(row['status'] == 'unknown' for row in
               plan['token_estimate']['stage2_global_before_generation'].values())
    assert plan['token_estimate']['downstream_prompt_tokens'] == 'unknown'
    assert '不含费率' in plan['token_estimate']['price']


def test_historical_ledger_is_hashed_and_never_double_adds_reasoning(tmp_path):
    ledger = tmp_path / 'old-ledger.jsonl'
    rows = [
        {
            'sid': '2019402867061048656', 'node': 'stage2.global',
            'cached': True, 'outcome': 'ok',
            'prompt_tokens': 10, 'completion_tokens': 100, 'reasoning_tokens': 80,
        },
        {
            'sid': '2019402867061048656', 'node': 'stage2.global',
            'cached': False, 'outcome': 'error',
            'prompt_tokens': None, 'completion_tokens': None, 'reasoning_tokens': None,
        },
    ]
    ledger.write_text(
        '\n'.join([json.dumps(rows[0]), '{half', json.dumps(rows[1])]) + '\n',
        encoding='utf-8')
    observed = P.historical_observations(
        ledger, {'2019402867061048656', '1668963766200045568'})
    assert observed['available'] is True
    assert len(observed['sha256']) == 64
    assert observed['malformed_rows_skipped'] == 1
    group = observed['groups'][0]
    assert group['prompt_tokens']['sum'] == 10
    assert group['completion_tokens']['sum'] == 100
    assert group['reasoning_tokens_diagnostic_subset']['sum'] == 80
    assert group['prompt_plus_completion_observed_sum'] == 110
    assert group['completion_includes_reasoning'] is True


def test_existing_experiments_and_minimum_evaluator_are_separate_scopes(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    old = plan['existing_authorized_boundary']
    assert old['cli_runs'] == 10
    assert old['fc13_base_ladder_limit'] == 322
    assert old['fc13_conditional_habit_quote_limit'] == 14
    assert old['fc13_hard_limit'] == 336
    assert old['external_hard_limit'] == 588
    assert old['included_in_new_corpus_totals'] is False
    assert old['overlaps_current_corpus_scope'] is False
    assert old['combined_wire_hard_ceiling_once_each'] == 588 + _FULL_WIRE

    accounting = plan['scope_accounting']
    assert accounting['existing_fc08_fc13_overlaps_current_corpus'] is False
    assert accounting['existing_plus_current_once_each'] == 588 + _FULL_WIRE
    # ⚠ 这里曾有第三块：仍 blocked 的「最低 evaluator 标定 campaign（4800 次）」。
    #   evaluator profile 装置已被裁决删除，那场战役不会发生 ⇒ 预览不再为它报预算。
    #   断言写成「这两个键不许回来」，而不是删掉了事：一个不存在的计划出现在预算里，
    #   会被读成「还差这一步」。
    assert 'calibration_evaluation' not in plan
    assert 'all_three_minimum_campaign_once_each_if_later_unblocked' not in accounting


def test_dirty_source_base_or_wrong_input_scope_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    dirty = spec.books[0].base
    dirty.mkdir(parents=True)
    (dirty / 'old').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='必须不存在或为空'):
        P.build_plan(spec, token_counter=lambda _request: 1)

    bad = spec.model_dump()
    bad['books'][1]['source'] = spec.books[0].source
    bad['books'][0]['base'] = tmp_path / 'clean-a'
    bad['books'][1]['base'] = tmp_path / 'clean-b'
    with pytest.raises(ValueError, match='派生 sid'):
        P.build_plan(
            P.PreviewSpec.model_validate(bad), token_counter=lambda _request: 1)


def test_render_says_upper_bound_unknown_price_and_no_execution(tmp_path):
    text = P.render(P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1))
    assert '[16/16]' in text
    # 6 条定向重试 × 单档 × 每档采样数（重采对单档臂一样适用）。
    hook_retry_wire = (6 * _FLASH_MODELS * len(P.HOOK_RETRY_RUNGS)
                       * MAX_SAMPLES_PER_RUNG)
    assert f'Stage6 条件候选定向重试：logical=6 / wire 上限={hook_retry_wire}' in text
    assert f'全条件硬上限：logical=144 / wire={_FULL_WIRE}' in text
    assert '可达硬上限，不是预计实付' in text
    assert '已有 FC08/12/13：10 CLI run' in text
    assert '那场战役不会发生' in text
    assert '4800' not in text
    # ⚠ 「不知道」必须一直写在渲染里：`unknown` 消失就等于把「没算过」读成「不需要算」。
    assert '其余 prompt token：unknown' in text
    assert 'price：unknown' in text
    assert 'completion 已含 reasoning，不重复相加' in text
    assert '最终 schema v2 上游' not in text
    assert '本工具没有执行入口' in text
