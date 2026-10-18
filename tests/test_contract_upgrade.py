"""同根登记升级、崩溃补尾与逐章真实消费者；全程 fake transport，零真实付费。"""
from copy import deepcopy
import json
import re

import pytest
from pydantic import ValidationError

from contracts.contract_upgrades import REGISTERED_UPGRADES

#: 升级机制测试跟随最新登记边；边的语义断言（归档集／返还节点）全部由它派生。
EDGE = REGISTERED_UPGRADES[-1]
from novel import prov
from novel.errors import ProvenanceRefused, TerminalState, UpgradeRefused
from novel.llm.paid_requests import (
    WORKFLOW_REL, digest, init_or_resume_candidate_workflow,
    open_candidate_workflow, workflow_from_disk, workflow_to_disk,
)
from novel.orchestrator.contract_upgrade import (
    PENDING_REL, abort_pending, apply, preview,
)
from novel.store import Str06Repository
from tests.str06_facilities import candidate_epoch
from tests.test_str06_hook import _full_chain_wires as _chain_wires

SOURCE = 'a' * 64

# ───────────────────────── 本地夹具（不依赖 tests/test_str06_body.py） ─────────────────────────

#: rc26：正文直接以目标姓名成文（无身份标记、无改名前中间态）。
GOOD_CH1 = '拜堂之夜，柳婉望着夫君，旧约出口，烛火到天亮才熄，她记住了每一个字。'
GOOD_CH2 = '夫君夜半离家，婉在他枕下发现玉佩，字迹陌生，她把玉佩藏回原处，坐到天明。'


def _chapters():
    return [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]


class _RoutingTransport:
    """按 owner 路由的假 transport：JSON owner 走 wires，文本 owner 出正文。

    owner 路由与 `tests/test_str06_body.py::_RoutingTransport` 同一套内容嗅探
    （ PaidRequestSession 以请求身份缓存，owner 只影响路由）；此处内联一份，
    避免跨文件依赖其夹具（rc26 批次 C 后该文件自行迁移中）。
    """

    def __init__(self, wires, truncate=(), hook_text='柳婉望着空房。'):
        self.wires = wires
        self.truncate = tuple(truncate)
        self.hook_text = hook_text
        self.calls: list[str] = []

    def send(self, request):
        from novel.llm.transport import ChatResponse
        import json as _json
        import re
        owner = self._owner(request)
        self.calls.append(owner)
        if owner == 'hook_writer':
            return ChatResponse(content=self.hook_text, finish_reason='stop',
                                usage={'prompt_tokens': 10, 'completion_tokens': 5},
                                model='fake')
        if owner.startswith('body_ch'):
            if owner in self.truncate:
                return ChatResponse(content='截断的开头。', finish_reason='length',
                                    usage={'prompt_tokens': 100, 'completion_tokens': 40000},
                                    model='fake')
            text = GOOD_CH1 if owner.endswith('ch01') else GOOD_CH2
            return ChatResponse(content=text, finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                                model='fake')
        return ChatResponse(content=_json.dumps(self.wires[owner], ensure_ascii=False),
                            finish_reason='stop',
                            usage={'prompt_tokens': 10, 'completion_tokens': 10},
                            model='fake')

    @staticmethod
    def _owner(request) -> str:
        wires = _RoutingTransport._current_wires
        user = request.user
        if '楔子' in request.system:
            return 'hook_writer'
        if '【本章交付要求】' in user:
            return 'body_ch01' if '本章目的（第1章）' in user else 'body_ch02'
        if '人物账、初始状态、世界约束与书级命题词表' in user:
            return 'book'
        if '条目级关联判定' in user:
            return 'associate'
        if '跨章因果判定' in user:
            return 'cross_cause'
        if '拆成有序 beats' in user:
            marks = re.findall(r'【第 (\d+) 章正文】', user)
            return f'ch{int(marks[-1]):02d}' if marks else 'ch01'
        for owner, marker in (
                ('names', '登记全书源名称结构'), ('preserve', '最小语义义务'),
                ('design', '设计目标书'),
                ('hook_design', 'stage4-hook-design-v4'), ('naming', '表层姓名')):
            if marker in user:
                return owner
        raise AssertionError(f'无法路由的请求：{user[:80]}')


def _make_session(tmp_path, wires, ceiling=None, truncate=(), name='out', transport=None):
    _RoutingTransport._current_wires = wires
    if transport is None:
        transport = _RoutingTransport(wires, truncate=truncate)
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.llm.paid_requests import PaidRequestSession
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context
    base = tmp_path / name
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=ceiling, sid='run')
    return repo, session, transport


def _run(repo, session, *, budget=2000, concurrency=4, node_concurrency=None,
         node_filter=None, body_chapters=None, through='stage7'):
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import body_ask, json_ask, text_ask
    return run_workflow(repository=repo, chapters=_chapters(),
                        ask=json_ask(session), ask_text=text_ask(session),
                        ask_body=body_ask(session),
                        budget=budget, through=through, book_title='报恩',
                        length_band=(0.01, 0.05), concurrency=concurrency,
                        node_concurrency=node_concurrency,
                        node_filter=node_filter, body_chapters=body_chapters)


def _full_chain_wires():
    """现役协议（rc26 批次 C）的全链 fake wires。

    `tests/test_str06_hook.py::_full_chain_wires` 的 design wire 仍是
    identity-text 时代的 v11（带 `[[name:…]]` 标记、自由文本用源侧称谓）。
    现役链只接受 v12 普通**目标**文本：此处按同一故事改写成 v12——剥掉标记，
    自由文本（ev/state/driver/brief）改用 naming wire 给出的目标姓名
    （fu_jun→顾、yun_niang→柳婉）；`initial_surfaces`／`release` 仍是 Design
    对**登记面**的声明（源侧），由 compile 经目标字面表映射。
    """
    wires = _chain_wires()
    design = wires['design']
    design['protocol_version'] = 'stage4-design-v12'
    wires['design'] = _plain_target_text(design, _TARGET_NAMES,
                                         keep_source=('initial_surfaces', 'release'))
    return wires


#: 源登记称谓 → naming wire 给出的目标姓名（只改自由文本，不改登记面声明）。
_TARGET_NAMES = {
    '苏芸娘': '柳婉', '芸娘': '婉', '苏姑娘': '柳姑娘',
    '陆大哥': '顾大哥', '陆兄': '顾兄', '陆家': '顾家',
}


def _plain_target_text(value, targets=None, keep_source=()):
    """剥掉 identity-text 标记；rc26 后 Design 自有文本一律普通目标文本。

    `targets` 给源称谓 → 目标姓名映射（写手材料只许出现目标字面）；
    `keep_source` 列的键是登记面声明，保持源侧字面（compile 负责映射）。
    """
    if isinstance(value, str):
        text = re.sub(r'\[\[(?:name:[^|]+\|)?([^\]]*)\]\]', r'\1', value)
        for source, target in (targets or {}).items():
            text = text.replace(source, target)
        return text
    if isinstance(value, dict):
        return {key: (_plain_target_text(item) if key in keep_source
                      else _plain_target_text(item, targets, keep_source))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_target_text(item, targets, keep_source) for item in value]
    return value

# 此边的真实历史执行版本。只改变版本声明，不改任何原轮、响应、采用或费用证据；
# 不能把当前执行账仅换 workflow 标签后，期待同版本再次恢复额度。
SOURCE_NODE_VERSION_OVERRIDES = {
    # 命名前移批（2026-10-18）：naming 版本轴随 NameMapV5→v6 与产物路径换桩
    # （canonical 12→26）；旧根账上记的是换桩前版本，同步据此恢复基础额度。
    'str06.stage4.naming': {'canonical': 12, 'validator': 1,
                            'artifact:stage4/naming/name_map.json': 12},
    'str06.stage4.body_design': {'wire': 10, 'artifact:stage4/design/body_design.json': 22},
    'str06.stage4.hook_design': {'wire': 3, 'artifact:stage4/design/presentation_graph.json': 23},
    'str06.stage5.body': {'protocol': 11, 'renderer': 12, 'validator': 3,
                        'artifact:stage5/acceptance/body/ch{nn}.json': 17},
    'str06.stage5.hook': {'context': 24, 'renderer': 12, 'validator': 4,
                        'artifact:stage5/hook/receipt.json': 12},
}


def _old_node_versions(repo):
    from novel import work_units
    for unit_id in work_units.registered_unit_ids(repo.root.parent, repo.root.name):
        budget = work_units.readonly_budget(repo.root.parent, repo.root.name, unit_id)
        overrides = SOURCE_NODE_VERSION_OVERRIDES.get(budget.identity['node'])
        if overrides is None:
            continue
        raw = budget._read().model_dump(mode='json')
        for reset in raw['version_resets']:
            reset['versions'].update(overrides)
            reset['upgrade_id'] = work_units._digest({'node': raw['node'], 'versions': reset['versions']})
        work_units._write(repo.root.parent, budget.path,
                          work_units.WorkUnitLedgerV3.model_validate(raw))


def _archive_bytes(repo):
    """归档范围直接取登记边，不把任何旧正文解释成当前身份声明。"""
    return {path.relative_to(repo.root).as_posix(): path.read_bytes()
            for pattern in EDGE['archive_patterns'] for path in repo.root.glob(pattern)
            if path.is_file()}


def _assert_archived_bytes(repo, receipt, originals, *, current_absent=True):
    subject = receipt['subject_sha256']
    for rel, data in originals.items():
        assert repo.path(f'runtime/upgrades/{subject}/files/{rel}').read_bytes() == data, rel
        if current_absent:
            assert not repo.path(rel).exists(), rel


def _old_workflow(repo, source=SOURCE):
    epoch = candidate_epoch()._replace(contract_version=EDGE['source_version'])
    before = open_candidate_workflow(epoch=epoch, source_identity=source, created_at=epoch.created_at)
    before = before._replace(contract_identity=EDGE['source_identity'])
    repo.write(WORKFLOW_REL, workflow_to_disk(before))
    _old_node_versions(repo)
    return before


def _preview(repo):
    return preview(repo, source_identity=SOURCE, code_version=prov.code_version())


def _apply(repo, plan):
    return apply(repo, base=repo.root.parent, sid=repo.root.name,
                 source_identity=SOURCE, code_version=prov.code_version(),
                 subject_sha256=digest(plan.model_dump(mode='json')), actor='测试', reason='登记契约升级')


def _minimal(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path / 'out')
    repo = Str06Repository(tmp_path / 'out' / 'run')
    _old_workflow(repo)
    repo.write_text('stage5/hook/hook.txt', '原楔子证据。')
    repo.write_text('stage5/body/ch01.txt', '保留章稿。')
    return repo


def test_plan_is_deterministic_readonly_and_unregistered_drift_refused(tmp_path):
    repo = _minimal(tmp_path)
    before = {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}
    assert _preview(repo) == _preview(repo)
    assert before == {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}
    with pytest.raises(UpgradeRefused, match='源身份'):
        preview(repo, source_identity='b' * 64, code_version=prov.code_version())
    raw = repo.inspect(WORKFLOW_REL)
    raw['contract_identity'] = '0' * 64
    repo.write(WORKFLOW_REL, raw)
    with pytest.raises(UpgradeRefused, match='未登记'):
        _preview(repo)


def test_already_upgraded_root_refuses_preview_as_up_to_date(tmp_path):
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    _apply(repo, plan)
    with pytest.raises(UpgradeRefused, match='无需升级'):
        _preview(repo)


def test_apply_archives_registered_original_bytes_and_is_idempotent(tmp_path):
    repo = _minimal(tmp_path)
    original = repo.path(WORKFLOW_REL).read_bytes()
    archived = _archive_bytes(repo)
    plan = _preview(repo)
    receipt = _apply(repo, plan)
    identity = receipt['subject_sha256']
    after = workflow_from_disk(repo.inspect(WORKFLOW_REL))
    assert after.epoch.reason == 'contract_drift' and after.epoch.ordinal == 2
    assert after.contract_identity == EDGE['target_identity']
    assert {item.rel for item in plan.archive} == set(archived)
    _assert_archived_bytes(repo, receipt, archived)
    assert archived['stage5/body/ch01.txt'] == '保留章稿。'.encode()
    assert repo.path(f'runtime/upgrades/{identity}/workflow.json').read_bytes() == original
    repo.write_text('stage5/hook/hook.txt', '后来的新版。')
    assert _apply(repo, plan) == receipt
    assert repo.read_text('stage5/hook/hook.txt') == '后来的新版。'


def test_stale_preview_and_live_owner_refuse_before_invalidation(tmp_path):
    """预览漂移与 live AgentRun owner 都在失效前拒绝；探测的是生产真实持锁身份。"""
    from contracts.schema.orchestration import AgentRunEvent, AgentRunStart
    from novel.orchestrator import agent
    from novel.orchestrator.admission import owner_lease
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    repo.write_text('stage5/body/ch01.txt', '变化的章稿。')
    with pytest.raises(UpgradeRefused, match='预览已漂移'):
        _apply(repo, plan)
    plan = _preview(repo)
    # 生产里持有 owner lease 的是 AgentRun 的 run_id；账上未结束的 run 必须
    # 被探到（候选根 epoch 会话 id 不再误当 owner）。
    agent.append(repo.root.parent, AgentRunEvent(
        run_id='live-book-owner', kind='started', ts=1759000000,
        start=AgentRunStart(sid=repo.root.name, base=str(repo.root.parent),
                            authorized=True, egress_authorized=True)))
    with owner_lease(repo.root.parent, 'live-book-owner'):
        with pytest.raises(TerminalState, match='owner'):
            _apply(repo, plan)
    # 候选根 epoch 会话即使真被人拿锁，也不再被当作这本书的 owner 证据。
    with owner_lease(repo.root.parent, candidate_epoch().session_run_id):
        receipt = _apply(repo, plan)
    assert repo.read_text(f'runtime/upgrades/{receipt["subject_sha256"]}/files/'
                          f'stage5/hook/hook.txt') == '原楔子证据。'


def test_frozen_output_root_refuses_upgrade_without_any_write(tmp_path):
    from novel import paths
    from novel.work_units import WorkUnitRefused
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    paths.workflow_manifest_path(repo.root.parent).unlink()
    before = {p: p.read_bytes() for p in repo.root.parent.rglob('*') if p.is_file()}
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        _apply(repo, plan)
    assert before == {p: p.read_bytes() for p in repo.root.parent.rglob('*') if p.is_file()}


def test_prepared_epoch_must_be_derived_from_original_epoch(tmp_path, monkeypatch):
    repo = _minimal(tmp_path)
    plan = _preview(repo)

    def interrupt(*args):
        raise OSError('模拟失效前中断')

    monkeypatch.setattr(repo, 'archive_upgrade_file', interrupt)
    with pytest.raises(OSError, match='模拟'):
        _apply(repo, plan)
    raw = repo.inspect(PENDING_REL)
    raw['after']['epoch']['epoch_id'] = '0' * 64
    raw['seal'] = digest({k: v for k, v in raw.items() if k != 'seal'})
    repo.write(PENDING_REL, raw)
    with pytest.raises(ProvenanceRefused, match='epoch'):
        _preview(repo)
    assert repo.path('stage5/hook/hook.txt').exists()


@pytest.mark.parametrize('window', ['archive', 'workflow', 'receipt', 'pending'])
def test_interrupted_upgrade_blocks_nodes_and_replays_same_plan(tmp_path, monkeypatch, window):
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    original_archive, original_write = repo.archive_upgrade_file, repo.write
    original_clear = repo.clear_upgrade_pending
    hit = False

    def archive(*args):
        nonlocal hit
        original_archive(*args)
        if window == 'archive' and not hit:
            hit = True
            raise OSError('模拟归档中断')

    def write(rel, *args, **kwargs):
        nonlocal hit
        result = original_write(rel, *args, **kwargs)
        if ((window == 'workflow' and rel == WORKFLOW_REL)
                or (window == 'receipt' and rel.endswith('/receipt.json'))) and not hit:
            hit = True
            raise OSError('模拟提交中断')
        return result

    def clear():
        nonlocal hit
        if window == 'pending' and not hit:
            hit = True
            raise OSError('模拟清理标记中断')
        original_clear()

    monkeypatch.setattr(repo, 'archive_upgrade_file', archive)
    monkeypatch.setattr(repo, 'write', write)
    monkeypatch.setattr(repo, 'clear_upgrade_pending', clear)
    with pytest.raises(OSError, match='模拟'):
        _apply(repo, plan)
    assert repo.path(PENDING_REL).exists()
    with pytest.raises(UpgradeRefused, match='尚未完成'):
        init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
            code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
            task_id='task-1', created_at=1759000001)
    _apply(repo, plan)
    assert not repo.path(PENDING_REL).exists()
    assert workflow_from_disk(repo.inspect(WORKFLOW_REL)).epoch.ordinal == 2


@pytest.mark.parametrize('window', ['pending', 'archive'])
def test_abort_pending_only_before_any_archive(tmp_path, monkeypatch, window):
    """未归档任何文件的恢复日志可显式放弃；已开始归档的必须重放至完成。"""
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    original_archive, original_write = repo.archive_upgrade_file, repo.write
    interrupted = False

    def write(rel, *args, **kwargs):
        nonlocal interrupted
        result = original_write(rel, *args, **kwargs)
        if window == 'pending' and rel == PENDING_REL and not interrupted:
            interrupted = True
            raise OSError('模拟恢复日志写盘后、归档前中断')
        return result

    def archive(*args):
        nonlocal interrupted
        original_archive(*args)
        if window == 'archive' and not interrupted:
            interrupted = True
            raise OSError('模拟首个对象归档后中断')

    monkeypatch.setattr(repo, 'write', write)
    monkeypatch.setattr(repo, 'archive_upgrade_file', archive)
    with pytest.raises(OSError, match='模拟'):
        _apply(repo, plan)
    assert repo.path(PENDING_REL).exists()

    if window == 'pending':
        result = abort_pending(repo, base=repo.root.parent, sid=repo.root.name,
                               source_identity=SOURCE, code_version=prov.code_version(),
                               actor='测试', reason='代码身份漂移后重新规划')
        assert result['status'] == 'aborted'
        assert not repo.path(PENDING_REL).exists()
        assert repo.path('stage5/hook/hook.txt').exists()
        # 放弃后可按当前身份重新规划；旧计划不再是唯一出路。
        assert _preview(repo).archive == plan.archive
    else:
        # 第一个归档对象已退出正式位：没有「放弃」出路，只能重放至完成。
        with pytest.raises(UpgradeRefused, match='不能放弃'):
            abort_pending(repo, base=repo.root.parent, sid=repo.root.name,
                          source_identity=SOURCE, code_version=prov.code_version(),
                          actor='测试', reason='试图半途放弃')
        _apply(repo, plan)
        assert not repo.path(PENDING_REL).exists()


@pytest.mark.parametrize('window', ['none', 'archive', 'workflow', 'receipt'])
def test_upgrade_rebuilds_registered_consumers_and_preserves_archived_body_bytes(tmp_path, monkeypatch, window):
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    _old_workflow(repo)
    body = {p: p.read_bytes() for prefix in ('stage5/body', 'stage5/acceptance/body')
            for p in (repo.root / prefix).glob('*')}
    archived = _archive_bytes(repo)
    service = {p: p.read_bytes() for p in (repo.root.parent / '_service').rglob('*')
               if p.is_file() and not p.name.endswith('.lock')
               # 轮次返还会追加登记节点的单元账行（裁决「升级清账」），
               # 其余服务账面（费用／响应／登记）字节不变。
               and p.name != 'ledger.json'}
    plan = _preview(repo)
    assert {item.rel for item in plan.archive} == set(archived)
    if window != 'none':
        original_archive, original_write = repo.archive_upgrade_file, repo.write

        def archive(upgrade, rel, sha):
            original_archive(upgrade, rel, sha)
            if window == 'archive' and rel == 'stage4/context/hook/hook.json':
                raise OSError('模拟登记产物部分归档的窗口')

        def write(rel, *args, **kwargs):
            result = original_write(rel, *args, **kwargs)
            if ((window == 'workflow' and rel == WORKFLOW_REL)
                    or (window == 'receipt' and rel.endswith('/receipt.json'))):
                raise OSError('模拟正式盘面提交中断')
            return result

        with monkeypatch.context() as interrupted:
            interrupted.setattr(repo, 'archive_upgrade_file', archive)
            interrupted.setattr(repo, 'write', write)
            with pytest.raises(OSError, match='模拟'):
                _apply(repo, plan)
        assert _preview(repo) == plan
    receipt = _apply(repo, plan)
    assert all(p.read_bytes() == data for p, data in service.items()), '升级不改账／额度／响应'
    # 清账裁决：登记重建节点的付费单元获得轮次返还，历史轮与响应证据不变。
    from novel.work_units import readonly_budget, registered_unit_ids
    for unit_id in registered_unit_ids(repo.root.parent, 'run'):
        ledger = readonly_budget(repo.root.parent, 'run', unit_id)._read()
        if ledger.node in EDGE['refund_nodes']:
            assert ledger.version_resets[-1].additional > 0, ledger.node
    _assert_archived_bytes(repo, receipt, archived)
    assert all(p.relative_to(repo.root).as_posix() in archived for p in body)
    # 真实初始化入口必须接受新 epoch，历史账沿用同一 base／sid。
    from novel.llm.paid_requests import PaidRequestSession
    _, context = init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
        code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
        task_id='task-1', configured_model='fake', created_at=1759000001)
    session = PaidRequestSession(repo, context.ledger, transport, yes=True,
                                allow_source_export=True, provider='fake', sid='run')
    # 新 Hook 选择仍由当前作者协议重做；旧正文只有原字节证据，不能补空表假装合法。
    wires = deepcopy(transport.wires)
    wires['hook_design']['local_focus'] += '突出门后的未知。'
    transport.wires = wires
    _run(repo, session, node_filter=EDGE['rebuild_nodes'])
    _assert_archived_bytes(repo, receipt, archived, current_absent=False)
    for ordinal in (1, 2):
        current = repo.read_model(f'stage5/acceptance/body/ch{ordinal:02d}.json')
        # rc26：正文即最终成文——回执绑定最终正文摘要与当前机械检查项，
        # 不再有 name_mentions／non_name_spans 两类区间表。
        from novel.stages.stage5 import sha256_text
        from contracts.schema.receipt import BODY_MECHANICAL_CHECKS
        assert current.prose_sha256 == sha256_text(
            repo.read_text(f'stage5/body/ch{ordinal:02d}.txt'))
        assert current.mechanical_checks == BODY_MECHANICAL_CHECKS
    assert repo.inspect('stage7/final/delivery_manifest.json')['chapters']
    assert repo.inspect('runtime/workflow_status.json')['status'] == 'delivered'


def test_upgrade_from_rejected_body_design_candidate_runs_current_author_protocol(tmp_path):
    """当前用户根形态：Stage4 前置已在，只有未接受的旧 Design 答案，没有正式设计。"""
    from novel.llm.transport import ChatRequest
    from novel.stages.chain import NODE_ORDER
    from novel.stages.common import SYS
    from novel.work_units import WorkUnitBudget, read_book_budget

    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, deepcopy(wires))
    preserve_end = NODE_ORDER.index('str06.stage4.preserve') + 1
    _run(repo, session, node_filter=NODE_ORDER[:preserve_end])
    assert repo.path('stage4/grounding/reconstruction_brief.json').exists()
    assert not repo.path('stage4/design/body_design.json').exists()

    # rc26：夹具 wire 已是普通目标文本；历史候选只把协议字面量降到 v10
    # （v10 与 v12 同形，只差协议声明），不再需要剥身份文本标记。
    old_design = deepcopy(wires['design'])
    old_design['protocol_version'] = 'stage4-design-v10'
    transport.wires['design'] = old_design
    old_answer = session.send('design', ChatRequest(
        model='fake', system=SYS, user='设计目标书：冻结历史已购但未接受的候选。',
        reasoning_effort='high', thinking=True, json_mode=True, max_tokens=1000))
    # 冻结旧判据留下的拒稿事实；不重新运行已经退役的姓名子串判据。
    session.record_acceptance(old_answer, mode='json', parse='ok',
                              errors=('历史姓名字面预检未通过',), accepted=False)
    unit = WorkUnitBudget(repo.root.parent, repo.root.name, 'str06.stage4.body_design')
    turns = unit._read().turns
    evidence = {path: path.read_bytes() for path in unit.directory.glob('*.json')
                if path != unit.path}
    cost_path = repo.root.parent / '_service' / 'ledger.jsonl'
    cost_bytes = cost_path.read_bytes()
    book_budget = read_book_budget(repo.root.parent, repo.root.name)
    assert json.loads(old_answer.text)['protocol_version'] == 'stage4-design-v10'
    assert any(not row['accepted'] for _key, row in unit.acceptance_records())

    _old_workflow(repo)
    plan = _preview(repo)
    assert plan.rebuild_nodes[0] == 'str06.stage4.naming'
    assert not any(row.rel == 'stage4/design/body_design.json' for row in plan.archive)
    receipt = _apply(repo, plan)
    assert unit._read().turns == turns
    assert cost_path.read_bytes() == cost_bytes
    assert read_book_budget(repo.root.parent, repo.root.name) == book_budget
    assert all(path.read_bytes() == data for path, data in evidence.items())
    assert workflow_from_disk(repo.inspect(WORKFLOW_REL)).contract_identity == EDGE['target_identity']
    assert receipt['status'] == 'applied'

    _, context = init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
        code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
        task_id='task-1', configured_model='fake', created_at=1759000001)
    from novel.llm.paid_requests import PaidRequestSession
    transport.wires = wires
    session = PaidRequestSession(repo, context.ledger, transport, yes=True,
                                allow_source_export=True, provider='fake', sid=repo.root.name)
    calls = len(transport.calls)
    # 升级归档旧 Preserve checkpoint／Brief：重跑从 preserve 起（命名前移批
    # 的登记重建链第一项即 str06.stage4.naming，其新路径产物已在场则跳过）。
    _run(repo, session, node_filter=('str06.stage4.preserve', 'str06.stage4.body_design'))
    current = repo.read_model('stage4/design/body_design.json')
    # 产物契约版本标签按自身形状轴走（命名前移批不换 Design 形状，仍 rc25）；
    # 「当前协议」由现役模型字面量承载，不随 epoch 目标版本（版式头）混淆。
    from contracts.schema.design import BodyDesign
    assert current.contract_version == BodyDesign.model_fields['contract_version'].default
    assert transport.calls[calls:] == ['design']
    historical_turns = unit._read().turns[:len(turns)]
    assert len(historical_turns) == len(turns)
    for before, after in zip(turns, historical_turns):
        # 升级不动已购历史；新请求开始时正式 reconcile 才把未采用旧回答终结。
        # 转态允许且必须准确，原请求、响应、attempt 和全部证据身份仍逐项不变。
        assert before.state == 'delivered' and before.reason_code == ''
        assert after.state == 'failed' and after.reason_code == 'superseded_unadopted'
        assert after.model_dump(exclude={'state', 'reason_code'}) == before.model_dump(
            exclude={'state', 'reason_code'})
    assert cost_path.read_bytes().startswith(cost_bytes), '重跑只追加新调用，历史成本行不得改写'
    assert all(path.read_bytes() == data for path, data in evidence.items())


def test_validation_only_change_rechecks_original_prose_and_preserves_adoption(tmp_path):
    from novel.work_units import sealed_commit_bundles
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    rel = 'stage5/acceptance/body/ch01.json'
    original = repo.inspect(rel)
    # 登记一个历史校验版本；章包和被接受请求保持原身份。
    receipt = dict(original, validator_version='historical-mechanical-v1')
    from contracts.schema.receipt import ChapterReceipt
    model = ChapterReceipt.model_validate(receipt)
    repo.context.begin_run()
    repo.read_model('stage4/ir/writer_book_ir.json')
    repo.publish_chapter_recheck(rel, model, 'stage5/body/ch01.txt')
    before = repo.path('stage5/body/ch01.txt').read_bytes()
    calls = len(transport.calls)
    _run(repo, session, node_filter=('str06.stage5.body', 'str06.stage7.assemble'))
    renewed = repo.inspect(rel)
    assert len(transport.calls) == calls
    assert repo.path('stage5/body/ch01.txt').read_bytes() == before
    assert renewed['request_identity'] == original['request_identity']
    from contracts.schema.receipt import BODY_VALIDATOR_VERSION
    assert renewed['validator_version'] == BODY_VALIDATOR_VERSION
    # 免费重验不丢生成事实：回执继承原付费来源账（model/prompts/usage 原样，
    # inputs 换成本次验证真读到的），partial_rewrite 留痕。
    inherited = renewed['_provenance']
    assert inherited['model'] == original['_provenance']['model']
    assert inherited['prompts'] == original['_provenance']['prompts']
    assert inherited['usage'] == original['_provenance']['usage']
    assert inherited['partial_rewrite'] == 'free-recheck'
    bundles = [b for _, b in sealed_commit_bundles(repo.root)
               if any(o.key == rel and o.sha256 == repo.file_sha256(repo.path(rel)) for o in b.outputs)]
    assert bundles and bundles[0].adoptions
    assert bundles[0].adoptions[0].request_sha256 == original['request_identity']


@pytest.mark.parametrize('defect', ['writer_view', 'ordinal', 'protocol', 'renderer'])
def test_validation_only_failure_does_not_sign_a_new_acceptance(tmp_path, defect):
    """只改版本声明不能伪造新接受：章包/正文/协议任一不符即拒（不签新接受）。"""
    from contracts.schema.receipt import ChapterReceipt
    from novel.stages.stage5 import recheck_chapter
    from novel.stages.common import project_chapter_packet, chapter_identities
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    packet = project_chapter_packet(repo.read_model('stage4/ir/writer_book_ir.json'), 1)
    receipt = ChapterReceipt.model_validate(repo.inspect('stage5/acceptance/body/ch01.json'))
    changed = packet
    if defect == 'writer_view':
        # 章包写手视图变化（导演说明/驱动摘要进写手视图摘要）：旧回执不再绑定
        scene = packet.chapter.scenes[0]
        redone = scene.model_copy(update={'writer_brief': scene.writer_brief.model_copy(
            update={'scene_driver_summary': '改过的驱动摘要'})})
        changed = packet.model_copy(update={'chapter': packet.chapter.model_copy(
            update={'scenes': (redone, *packet.chapter.scenes[1:])})})
        assert chapter_identities(changed).writer_view_sha256 != receipt.writer_view_sha256
    elif defect == 'ordinal':
        receipt = receipt.model_copy(update={'chapter_ordinal': 2})
    elif defect == 'protocol':
        receipt = receipt.model_copy(update={'protocol_version': 'unknown-protocol'})
    else:
        receipt = receipt.model_copy(update={'renderer_version': 'unknown-renderer'})
    assert recheck_chapter(changed, repo.read_text('stage5/body/ch01.txt'), receipt) is None


def test_recompile_changes_only_one_chapter_and_keeps_other_commit(tmp_path, monkeypatch):
    """重编译可逐章变化：旧投影使第 1 章材料多出一行，不扩大到第 2 章。"""
    from dataclasses import replace
    from novel.stages import chain
    real = chain.inspect_body_materials

    def old_projection(*args, **kwargs):
        inspection = real(*args, **kwargs)
        ir = inspection.ir
        # 模拟旧背景投影的额外字面；它真实进入旧章包与请求身份。
        first = ir.chapters[0]
        first = first.model_copy(update={'purpose': first.purpose + '（旧背景投影）'})
        nav = ir.book_brief.chapters[0].model_copy(update={'purpose': first.purpose})
        brief = ir.book_brief.model_copy(update={'chapters': (nav, *ir.book_brief.chapters[1:])})
        return replace(inspection, ir=ir.model_copy(update={'chapters': (first, *ir.chapters[1:]), 'book_brief': brief}))

    monkeypatch.setattr(chain, 'inspect_body_materials', old_projection)
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    monkeypatch.setattr(chain, 'inspect_body_materials', real)
    before = repo.path('stage5/acceptance/body/ch02.json').read_bytes()
    calls = len(transport.calls)
    # 同一当前协议内的免费重编译才有逐章精确失效语义；跨身份协议升级须归档旧正文。
    _run(repo, session, node_filter=('str06.stage4.compile',))
    # 监控／planner 必须识别局部绑定，不能只看正文字节仍存在。
    from novel.store import ArtifactStore
    from novel.orchestrator.state import artifact_state, readonly_ctx
    store = ArtifactStore(readonly_ctx(repo.root, 'str06.stage5.body', prov.code_version()))
    assert artifact_state(store, 'stage5/body/ch01.txt').freshness == 'stale'
    assert artifact_state(store, 'stage5/body/ch02.txt').freshness == 'fresh'
    report = _run(repo, session, node_filter=('str06.stage5.body', 'str06.stage7.assemble'))
    assert [c for c in transport.calls[calls:] if c.startswith('body_')] == ['body_ch01']
    assert repo.path('stage5/acceptance/body/ch02.json').read_bytes() == before
    assert report['assembly']['status'] == 'ready'


def test_ir_upstream_stale_keeps_overview_alive_and_points_to_compile(tmp_path, monkeypatch):
    """回归：盘上正文 + IR 上游已变时，只读完成度不得抛 UpstreamStale。

    IR 只记 reconstruction_brief 一条边：Preserve 重做（回派修订正是这种情形）
    后 IR 待重编译，逐章核对须落 unknown/consumed_unresolved，planner 排出
    compile，而不是让 artifact_state → overview → next_action 整条链崩溃。
    """
    from novel import paths
    from novel.cli import main as cli
    from novel.config import Config
    from contracts.invariants import NODES
    from novel.orchestrator import operator
    from novel.orchestrator.nodes import build_steps
    from novel.stages.chain import NODE_ORDER
    from tests.test_str06_product_entry import (
        FILENAME, SOURCE_TEXT, _profiles, _install_fake_transport,
    )
    from tests.test_str06_hook import _full_chain_wires as entry_wires

    base = tmp_path / 'out'
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    source = tmp_path / FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source), '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9001', '--base', str(base)]) == 0
    monkeypatch.setattr('novel.stages.chain.load_config', lambda: Config(
        model='deepseek-v4-pro', thinking_mode='on'))
    _install_fake_transport(monkeypatch, entry_wires())
    profiles = _profiles()
    for node in NODE_ORDER:
        for step in build_steps(node, '9001', base=base,
                                executor_backend=profiles.executor_default if NODES[node].paid else None,
                                egress_authorized=True if NODES[node].paid else None):
            assert cli.main(step.argv[step.argv.index('run'):]) == 0
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    # 让 IR 记录的上游（reconstruction_brief）字节变化 ⇒ IR 待重编译。
    novel_dir = paths.resolve_novel_dir(base, '9001')
    brief = novel_dir / 'stage4' / 'grounding' / 'reconstruction_brief.json'
    raw = brief.read_text(encoding='utf-8')
    brief.write_text(raw + '\n', encoding='utf-8', newline='\n')
    # 回归点：这条只读链不再抛 UpstreamStale；planner 能点名待重建的 stage4
    # 节点（brief 同时被 presentation_graph 与 IR 消费，最靠前的 stale 节点
    # 先被选中）。人工改字节（无 open 回派账）按设计停在待决策——真实触发
    # （Preserve 回派修订级联）经 revision cascade 以默认 flags 放行为 ready。
    next_action = operator.next_action(base, '9001')
    assert next_action.action is not None
    assert next_action.action.node in ('str06.stage4.hook_design',
                                       'str06.stage4.compile')
    from novel.orchestrator.state import overview_at
    overview = overview_at(novel_dir, prov.code_version())
    chapter = next(a for stage in overview.stages for a in stage.artifacts
                   if a.rel == 'stage5/body/ch01.txt')
    assert chapter.freshness == 'unknown' and chapter.reason == 'consumed_unresolved'


def test_corrupt_commit_bundle_shape_stops_recovery_instead_of_rebuying(tmp_path):
    """回归：CommitBundle 形状损坏是全局事实，不得被吞成「全书章稿无提交」。

    2026-10-10 审阅：形状错以裸 ValidationError 冒出时，`_committed_set` 的
    逐章 except 把每章都判成待补——全书进入重购集合。关闭式上抛才是正解。
    """
    import json as _json

    from novel.errors import ProvenanceRefused
    from novel.stages import chain
    from novel.work_units import sealed_commit_bundles
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    writer_ir = repo.read_model('stage4/ir/writer_book_ir.json')
    identities = chain._packet_identities(writer_ir)
    path = next(iter(sorted((repo.root / 'runtime' / 'commit').rglob('*.json'))))
    payload = _json.loads(path.read_text(encoding='utf-8'))
    payload['node'] = 12345                      # seal 一致但形状不合约
    from contracts.schema.workflow import commit_bundle_seal
    payload['seal'] = commit_bundle_seal(payload)
    path.write_text(_json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ProvenanceRefused, match='形状不合约'):
        sealed_commit_bundles(repo.root)
    with pytest.raises(ProvenanceRefused, match='形状不合约'):
        chain._committed_set(repo, identities, (1, 2), writer_ir=writer_ir)


def test_workflow_lock_contention_is_a_terminal_stop_with_zero_writes(tmp_path):
    """升级撞上在跑执行：显式终态（非 exit 70 崩溃），本次不写任何字节。"""
    from novel.appendlog import path_transaction_lock

    repo = _minimal(tmp_path)
    plan = _preview(repo)
    before = {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}

    class _Busy:
        def __enter__(self):
            raise TimeoutError('timed out locking shared append log')

        def __exit__(self, *args):
            return False

    import novel.orchestrator.contract_upgrade as cu
    original = path_transaction_lock
    (repo.root.parent / '_service').mkdir(exist_ok=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cu, 'path_transaction_lock', lambda _p: _Busy())
        with pytest.raises(UpgradeRefused, match='争用|超时|正在进行'):
            _apply(repo, plan)
    assert before == {p: p.read_bytes() for p in repo.root.rglob('*') if p.is_file()}
    assert not repo.path(PENDING_REL).exists()


def test_open_hook_design_issue_is_resolved_when_product_archived(tmp_path):
    """新边归档 graph，旧产物回派账随之收口，重建不能读取退出的修订模板。"""
    from contracts.schema.workflow import OwnerIssueV1
    from novel.stages.owner_revision import (
        OwnerRevisionRequired, discover_owner_issues, read_owner_issue_file,
    )
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(
            repo, raised_by='str06.stage4.compile', owner='hook_design',
            node='str06.stage4.hook_design',
            issues=[OwnerIssueV1(code='hook_combination', item='materials',
                                 message='组合检查不闭合（测试回派）')])
    assert read_owner_issue_file(repo, 'hook_design').status == 'open'
    _old_workflow(repo)
    graph_bytes = repo.path('stage4/design/presentation_graph.json').read_bytes()
    receipt = _apply(repo, _preview(repo))
    assert read_owner_issue_file(repo, 'hook_design').status == 'resolved'
    assert not repo.path('stage4/design/presentation_graph.json').exists()
    assert repo.path(f'runtime/upgrades/{receipt["subject_sha256"]}/files/'
                     'stage4/design/presentation_graph.json').read_bytes() == graph_bytes
    rebuild = list(EDGE['rebuild_nodes'])
    _run(repo, session,
         node_filter=rebuild[:rebuild.index('str06.stage4.hook_design') + 1])
    graph = repo.inspect('stage4/design/presentation_graph.json')
    # rc26（命名前移批 C）：Design/Graph 形状同批升版（canonical 普通目标文本），
    # 产物契约版本标签随自身形状轴走；世代身份由 epoch 与契约表承载
    # （EDGE['target_version'] 是版式头，不是每个产物的标签）。
    assert graph['contract_version'] == 'str06-rc26'


def test_unknown_call_and_accumulated_budget_survive_upgrade(tmp_path):
    from novel.llm.paid_requests import (
        ExecutionError, PaidRequestSession, ask_json, ask_text_once,
    )
    from novel.llm.transport import ChatRequest
    from novel.work_units import WorkUnitBudget, read_book_budget
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)

    class Unconfirmed:
        def send(self, request):
            raise TimeoutError('已发送，结果未知')

    request = ChatRequest(model='fake', system='未知调用', user='独立的新请求',
                          reasoning_effort='high', thinking=True, json_mode=True, max_tokens=1024)
    session.transport = Unconfirmed()
    text_request = ChatRequest(model='fake', system='未知调用', user='独立的新文本请求',
                               reasoning_effort='high', thinking=True, json_mode=False,
                               max_tokens=1024)
    with pytest.raises((ExecutionError, TimeoutError)):
        ask_text_once(session, 'hook_writer', text_request, lambda _: [])
    unit = WorkUnitBudget(repo.root.parent, 'run', 'str06.stage5.hook', 'executor', 'book')
    old_ledger = unit._read()
    # 登记返还范围（版本轴本批变化的节点）的单元：命名前移批是 naming。
    refund_unit = WorkUnitBudget(repo.root.parent, 'run', EDGE['refund_nodes'][0],
                                 'executor', 'book')
    old_refund_ledger = refund_unit._read()
    old_budget = read_book_budget(repo.root.parent, 'run')
    assert old_ledger.turns[-1].state == 'unknown'
    # 预览附带的重建额度投影与账面一致：应用前就能看见「第一步会不会撞额度」。
    from novel.orchestrator.work_unit_evidence import rebuild_budget_views
    hook_view = next(v for v in rebuild_budget_views(
        repo.root.parent, 'run', EDGE['rebuild_nodes'])
        if v['node'] == 'str06.stage5.hook')
    assert hook_view['content_used'] == len(old_ledger.turns)
    assert hook_view['content_remaining'] == hook_view['content_limit'] - len(old_ledger.turns)
    _old_workflow(repo)
    pre = unit._read()
    pre_refund = refund_unit._read()
    receipt = _apply(repo, _preview(repo))
    # 裁决「升级清账」：历史轮（含 unknown）与费用总闸原样保留。
    refunded = unit._read()
    assert refunded.turns == old_ledger.turns
    assert read_book_budget(repo.root.parent, 'run') == old_budget
    # hook 的版本轴本批未变（命名前移只换 naming 轴）⇒ 不新增重置、不返还；
    # 登记返还范围内的单元才新增一次版本基线重置并恢复基础额度，历史轮不动。
    assert refunded.version_resets == pre.version_resets
    new_refund_ledger = refund_unit._read()
    assert new_refund_ledger.turns == pre_refund.turns
    assert len(new_refund_ledger.version_resets) == len(pre_refund.version_resets) + 1
    assert (new_refund_ledger.content_limit
            == pre_refund.content_limit + new_refund_ledger.version_resets[-1].additional)
    _, context = init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
        code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
        task_id='task-1', configured_model='fake', created_at=1759000001)
    session = PaidRequestSession(repo, context.ledger, Unconfirmed(), yes=True,
                                allow_source_export=True, provider='fake', sid='run')
    with pytest.raises((ExecutionError, ValueError), match='unknown|未知'):
        ask_text_once(session, 'hook_writer', text_request, lambda _: [])
    assert unit._read() == refunded
    assert read_book_budget(repo.root.parent, 'run') == old_budget


def test_operator_and_agent_resume_same_root_to_delivery(tmp_path, monkeypatch):
    from novel import paths
    from novel.cli import main as cli
    from novel.config import Config
    from contracts.invariants import NODES
    from novel.orchestrator import agent, operator
    from novel.orchestrator.nodes import build_steps
    from novel.stages.chain import NODE_ORDER
    from novel.work_units import initialize_workflow
    from tests.test_str06_product_entry import (
        FILENAME, SOURCE_TEXT, _profiles, _install_fake_transport,
        _install_inprocess_subprocess, _install_step_argv,
    )
    from tests.test_str06_hook import _full_chain_wires as entry_wires

    base = tmp_path / 'out'
    initialize_workflow(base)
    source = tmp_path / FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source), '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9001', '--base', str(base)]) == 0
    monkeypatch.setattr('novel.stages.chain.load_config', lambda: Config(
        model='deepseek-v4-pro', thinking_mode='on'))
    transport = _install_fake_transport(monkeypatch, entry_wires())
    profiles = _profiles()
    for node in NODE_ORDER:
        for step in build_steps(node, '9001', base=base,
                                executor_backend=profiles.executor_default if NODES[node].paid else None,
                                egress_authorized=True if NODES[node].paid else None):
            assert cli.main(step.argv[step.argv.index('run'):]) == 0
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    repo = Str06Repository(paths.resolve_novel_dir(base, '9001'))
    current = workflow_from_disk(repo.inspect(WORKFLOW_REL))
    historical = current._replace(
        epoch=current.epoch._replace(contract_version=EDGE['source_version']),
        contract_identity=EDGE['source_identity'])
    repo.write(WORKFLOW_REL, workflow_to_disk(historical))
    _old_node_versions(repo)
    original = {p: p.read_bytes() for prefix in ('stage5/body', 'stage5/acceptance/body')
                for p in (repo.root / prefix).glob('*')}
    calls = len(transport.calls)
    plan = preview(repo, source_identity=current.source_identity, code_version=prov.code_version())
    receipt = apply(repo, base=base, sid='9001', source_identity=current.source_identity,
          code_version=prov.code_version(), subject_sha256=digest(plan.model_dump(mode='json')),
          actor='测试', reason='同根恢复真实自动编排入口')
    next_action = operator.next_action(base, '9001')
    assert next_action.state == 'ready' and next_action.action is not None
    # 首节点＝重建链上第一个产物被归档的节点。命名前移批的合成根已跑过新链，
    # 新路径 name_map 在场 ⇒ naming 跳过，从被归档的 preserve 续跑。
    first_missing = next(
        n for n in EDGE['rebuild_nodes']
        if not all(repo.path(rel).is_file() for rel in NODES[n].produces))
    assert next_action.action.node == first_missing
    _assert_archived_bytes(repo, receipt,
        {path.relative_to(repo.root).as_posix(): data for path, data in original.items()})
    box = {'dispatched': [], 'processes': []}
    _install_inprocess_subprocess(monkeypatch, box)
    _install_step_argv(monkeypatch, box)
    run = agent.start(base, '9001', authorized=True, egress_authorized=True,
                      profiles=profiles, pause_before_node=None)
    assert agent.wait(run.run_id, 120)
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    assert {item['node'] for item in box['dispatched']} <= set(EDGE['rebuild_nodes'])
    _assert_archived_bytes(repo, receipt,
        {path.relative_to(repo.root).as_posix(): data for path, data in original.items()},
        current_absent=False)
    assert repo.inspect('runtime/workflow_status.json')['status'] == 'delivered'


@pytest.mark.real_code_version
def test_cli_subprocess_preview_and_apply_are_free_and_reachable(tmp_path):
    import json
    import subprocess
    import sys
    from contracts.schema import encode_artifact
    from novel.stages.chain import load_stage1_chapters
    repo = _minimal(tmp_path)
    path = repo.root / 'stage1' / 'main_text.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(encode_artifact('stage1/main_text.json', [
        {'chapter_ordinal': 1, 'text': '第一章源事实。'},
        {'chapter_ordinal': 2, 'text': '第二章源事实。'},
    ]), ensure_ascii=False), encoding='utf-8')
    _old_workflow(repo, source=load_stage1_chapters(repo.root.parent, 'run').source_identity)
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'novel.cli',
               'contract-upgrade', 'run', '--base', str(repo.root.parent)]
    read = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert read.returncode == 0, read.stderr
    plan = json.loads(read.stdout)
    written = subprocess.run([*command, '--apply', '--subject-sha256', plan['subject_sha256'],
                              '--actor', '测试', '--reason', '登记升级'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)['status'] == 'applied'
    receipt = json.loads(written.stdout)
    _assert_archived_bytes(repo, receipt, {'stage5/body/ch01.txt': '保留章稿。'.encode()})


# ──────────── 裁决「升级清账」：登记节点轮次返还（2026-10-10） ────────────

def _exhausted_refund_unit(repo):
    """耗尽**本边 refund_nodes 第一个节点**的单元（边驱动，不钉死具体节点）。

    命名前移批（2026-10-18）起该节点是 str06.stage4.naming（版本轴随
    NameMapV5→v6 换桩）；历史边曾为 str06.stage5.hook。测试只验证「登记
    返还范围＋版本轴变化 ⇒ 恢复基础额度」的机制，节点身份跟随边。
    """
    from novel import work_units
    base, sid = repo.root.parent, repo.root.name
    node = EDGE['refund_nodes'][0]
    budget = work_units.WorkUnitBudget(base, sid, node)
    for i in range(4):
        reservation = budget.reserve('a' * 64, run_id=f'r{i}')
        budget.finish(reservation.turn_id, outcome='failed',
                      reason_code='content_rejected')
    assert budget.remaining == 0 and budget.used == 4
    _old_workflow(repo)
    return budget


def test_apply_refunds_registered_unit_rounds_to_base(tmp_path):
    repo = _minimal(tmp_path)
    budget = _exhausted_refund_unit(repo)
    plan = _preview(repo)
    receipt = _apply(repo, plan)
    row = next(r for r in receipt['round_refunds']
               if r['node'] == EDGE['refund_nodes'][0])
    assert row['status'] == 'refunded' and row['additional'] == 4
    assert budget.remaining == 4 and budget.used == 4
    ledger = budget._read()
    assert [r.additional for r in ledger.version_resets] == [0, 4]
    assert ledger.content_limit == 8
    # 历史轮与费用证据保留：轮次只增可用额度，不清账面。
    assert len(ledger.turns) == 4 and all(
        t.reason_code == 'content_rejected' for t in ledger.turns)
    # 重放幂等：同一升级不重复返还。
    again = _apply(repo, plan)
    row2 = next(r for r in again['round_refunds']
                if r['node'] == EDGE['refund_nodes'][0])
    assert row2['status'] == 'already_refunded' and budget.remaining == 4


def test_ledger_validator_orders_refunds_and_grants_chronologically():
    from contracts.schema.workflow import (
        BudgetGrant, UpgradeRoundRefund, WorkUnitContentTurn, WorkUnitLedgerV2,
    )
    def turn(i):
        return WorkUnitContentTurn(turn_id=f't{i}', ordinal=i, request_sha256='a' * 64,
                                   run_id='r', created_at=1000 + i)
    turns = [turn(i) for i in range(1, 11)]
    refund = UpgradeRoundRefund(upgrade_id='f' * 64, reason='升级作废已购产物',
                                created_at=2000, before_ledger_sha256='b' * 64,
                                refunded_after_ordinal=6, additional=4)
    grant = BudgetGrant(intent_id='codex-refund-then-grant', preview_token='c' * 64,
                        additional=2, reason='返还额度耗尽后的人工追加',
                        created_at=3000, before_ledger_sha256='d' * 64,
                        surface_sha256='e' * 64, authorized_after_ordinal=8,
                        owner_run_ids=('run-1',))
    ledger = WorkUnitLedgerV2(task_id='1' * 64, unit_id='2' * 64, sid='s',
                              node='str06.stage4.hook_design', role='executor',
                              scope='book', grants=(grant,), refunds=(refund,),
                              turns=tuple(turns))
    assert ledger.content_limit == 10
    # 反例：追加越过合并序运行额度（8）时拒绝。
    with pytest.raises(ValidationError, match='耗尽后累计'):
        WorkUnitLedgerV2.model_validate({**ledger.model_dump(mode='json'),
            'grants': (grant.model_copy(update={'authorized_after_ordinal': 9}),
                       grant.model_copy(update={'intent_id': 'codex-second-grant'}))})


# ──────────── 裁决③：已购产物免费换算层（2026-10-10） ────────────

def test_hook_edge_has_no_converter_and_refuses_explicitly(tmp_path):
    from novel.orchestrator.upgrade_converters import (
        CONVERTERS, convert_archived_wire,
    )
    assert (EDGE['id'], 'str06.stage5.hook') not in CONVERTERS
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    _apply(repo, plan)
    from novel.errors import TerminalState
    with pytest.raises(TerminalState, match='未登记无损换算器'):
        convert_archived_wire(repo, upgrade_id=EDGE['id'],
                              node='str06.stage5.hook',
                              wire_dir=tmp_path / 'w', actor='测试', reason='语义缺口')


def test_registered_converter_writes_wire_for_free_adoption(tmp_path):
    from novel.orchestrator.upgrade_converters import CONVERTERS, WireConverter
    from novel.orchestrator.upgrade_converters import convert_archived_wire
    repo = _minimal(tmp_path)
    repo.write_text('stage5/hook/hook.txt', '{"prose": "原楔子证据。"}')
    plan = _preview(repo)
    receipt = _apply(repo, plan)
    subject = receipt['subject_sha256']
    key = (EDGE['id'], 'str06.stage5.hook')
    CONVERTERS[key] = WireConverter(
        upgrade_id=EDGE['id'], node='str06.stage5.hook',
        owner='hook_writer', source_rel='stage5/hook/hook.txt',
        note='测试边：演示无损换算登记面', convert=lambda p: dict(p))
    try:
        out = convert_archived_wire(repo, upgrade_id=key[0], node=key[1],
                                    wire_dir=tmp_path / 'w', actor='测试',
                                    reason='登记的免费换算')
        assert out['status'] == 'converted'
        wire = json.loads((tmp_path / 'w' / 'hook_writer_wire.json')
                          .read_text(encoding='utf-8'))
        assert wire == {'prose': '原楔子证据。'}
        record = repo.inspect(
            f'runtime/upgrades/{subject}/converted/hook_writer.json')
        assert record['source_rel'] == 'stage5/hook/hook.txt'
        # 幂等：同一字节再换算不拒绝。
        again = convert_archived_wire(repo, upgrade_id=key[0], node=key[1],
                                      wire_dir=tmp_path / 'w', actor='测试',
                                      reason='登记的免费换算')
        assert again['wire_sha256'] == out['wire_sha256']
    finally:
        del CONVERTERS[key]
