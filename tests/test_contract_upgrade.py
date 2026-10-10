"""同根登记升级、崩溃补尾与逐章真实消费者；全程 fake transport，零真实付费。"""
from copy import deepcopy

import pytest

from contracts.contract_upgrades import HOOK_UPGRADE
from novel import prov
from novel.errors import ProvenanceRefused
from novel.llm.paid_requests import (
    WORKFLOW_REL, digest, init_or_resume_candidate_workflow,
    open_candidate_workflow, workflow_from_disk, workflow_to_disk,
)
from novel.orchestrator.contract_upgrade import PENDING_REL, apply, preview
from novel.store import Str06Repository
from tests.str06_facilities import candidate_epoch
from tests.test_str06_body import _make_session, _run, _full_chain_wires

SOURCE = 'a' * 64


def _old_workflow(repo, source=SOURCE):
    epoch = candidate_epoch()._replace(contract_version='str06-rc22')
    before = open_candidate_workflow(epoch=epoch, source_identity=source, created_at=epoch.created_at)
    before = before._replace(contract_identity=HOOK_UPGRADE['source_identity'])
    repo.write(WORKFLOW_REL, workflow_to_disk(before))
    return before


def _preview(repo):
    return preview(repo, source_identity=SOURCE, code_version=prov.code_version())


def _apply(repo, plan):
    return apply(repo, base=repo.root.parent, sid=repo.root.name,
                 source_identity=SOURCE, code_version=prov.code_version(),
                 subject_sha256=digest(plan.model_dump(mode='json')), actor='测试', reason='登记的 Hook 升级')


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
    with pytest.raises(ProvenanceRefused, match='源身份'):
        preview(repo, source_identity='b' * 64, code_version=prov.code_version())
    raw = repo.inspect(WORKFLOW_REL)
    raw['contract_identity'] = '0' * 64
    repo.write(WORKFLOW_REL, raw)
    with pytest.raises(ProvenanceRefused, match='未登记'):
        _preview(repo)


def test_apply_archives_original_bytes_keeps_body_and_is_idempotent(tmp_path):
    repo = _minimal(tmp_path)
    original = repo.path(WORKFLOW_REL).read_bytes()
    plan = _preview(repo)
    receipt = _apply(repo, plan)
    identity = receipt['subject_sha256']
    after = workflow_from_disk(repo.inspect(WORKFLOW_REL))
    assert after.epoch.reason == 'contract_drift' and after.epoch.ordinal == 2
    assert after.contract_identity == HOOK_UPGRADE['target_identity']
    assert repo.read_text('stage5/body/ch01.txt') == '保留章稿。'
    assert not repo.path('stage5/hook/hook.txt').exists()
    assert repo.read_text(f'runtime/upgrades/{identity}/files/stage5/hook/hook.txt') == '原楔子证据。'
    assert repo.path(f'runtime/upgrades/{identity}/workflow.json').read_bytes() == original
    repo.write_text('stage5/hook/hook.txt', '后来的新版。')
    assert _apply(repo, plan) == receipt
    assert repo.read_text('stage5/hook/hook.txt') == '后来的新版。'


def test_stale_preview_and_live_owner_refuse_before_invalidation(tmp_path):
    from novel.orchestrator.admission import owner_lease
    from novel.errors import TerminalState
    repo = _minimal(tmp_path)
    plan = _preview(repo)
    repo.write_text('stage5/body/ch01.txt', '变化的章稿。')
    with pytest.raises(ProvenanceRefused, match='预览已漂移'):
        _apply(repo, plan)
    plan = _preview(repo)
    with owner_lease(repo.root.parent, candidate_epoch().session_run_id):
        with pytest.raises(TerminalState, match='owner'):
            _apply(repo, plan)
    assert repo.path('stage5/hook/hook.txt').exists()
    assert not repo.path(PENDING_REL).exists()


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
    with pytest.raises(ProvenanceRefused, match='尚未完成'):
        init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
            code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
            task_id='task-1', created_at=1759000001)
    _apply(repo, plan)
    assert not repo.path(PENDING_REL).exists()
    assert workflow_from_disk(repo.inspect(WORKFLOW_REL)).epoch.ordinal == 2


@pytest.mark.parametrize('window', ['none', 'archive', 'workflow', 'receipt'])
def test_upgrade_rebuilds_hook_and_delivery_without_rebuying_body(tmp_path, monkeypatch, window):
    repo, session, transport = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    _old_workflow(repo)
    body = {p: p.read_bytes() for prefix in ('stage5/body', 'stage5/acceptance/body')
            for p in (repo.root / prefix).glob('*')}
    service = {p: p.read_bytes() for p in (repo.root.parent / '_service').rglob('*')
               if p.is_file() and not p.name.endswith('.lock')}
    calls = len(transport.calls)
    plan = _preview(repo)
    if window != 'none':
        original_archive, original_write = repo.archive_upgrade_file, repo.write

        def archive(upgrade, rel, sha):
            original_archive(upgrade, rel, sha)
            if window == 'archive' and rel == 'stage4/ir/writer_book_ir.json':
                raise OSError('模拟章稿仍保留、IR 已退出的窗口')

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
    _apply(repo, plan)
    assert all(p.read_bytes() == data for p, data in service.items()), '升级不改账／额度／响应'
    assert all(p.read_bytes() == data for p, data in body.items())
    # 真实初始化入口必须接受新 epoch，历史账沿用同一 base／sid。
    from novel.llm.paid_requests import PaidRequestSession
    _, context = init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
        code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
        task_id='task-1', configured_model='fake', created_at=1759000001)
    session = PaidRequestSession(repo, context.ledger, transport, yes=True,
                                allow_source_export=True, provider='fake', sid='run')
    # 更换 Hook 的正式输入，新 Hook 不能冒充旧证据；正文设计和章包不变。
    wires = deepcopy(transport.wires)
    wires['hook_design']['local_focus'] += '突出门后的未知。'
    transport.wires = wires
    _run(repo, session, node_filter=('str06.stage4.hook_design', 'str06.stage4.compile',
                                    'str06.stage5.hook', 'str06.stage5.body', 'str06.stage7.assemble'))
    assert all(p.read_bytes() == data for p, data in body.items())
    assert not any(c.startswith('body_') for c in transport.calls[calls:])
    assert repo.inspect('stage7/final/delivery_manifest.json')['chapters']
    assert repo.inspect('runtime/workflow_status.json')['status'] == 'delivered'


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
    assert renewed['validator_version'] == 'str06-body-mechanical-v2'
    bundles = [b for _, b in sealed_commit_bundles(repo.root)
               if any(o.key == rel and o.sha256 == repo.file_sha256(repo.path(rel)) for o in b.outputs)]
    assert bundles and bundles[0].adoptions
    assert bundles[0].adoptions[0].request_sha256 == original['request_identity']


@pytest.mark.parametrize('defect', ['withheld', 'ordinal', 'protocol', 'renderer'])
def test_validation_only_failure_does_not_sign_a_new_acceptance(tmp_path, defect):
    from contracts.schema.receipt import ChapterReceipt
    from novel.stages.stage5 import recheck_chapter
    from novel.stages.common import project_chapter_packet, chapter_identities
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _run(repo, session)
    packet = project_chapter_packet(repo.read_model('stage4/ir/writer_book_ir.json'), 1)
    receipt = ChapterReceipt.model_validate(repo.inspect('stage5/acceptance/body/ch01.json'))
    changed = packet
    if defect == 'withheld':
        changed = packet.model_copy(update={'chapter': packet.chapter.model_copy(
            update={'withheld_literals': (*packet.chapter.withheld_literals, '旧约')})})
    elif defect == 'ordinal':
        receipt = receipt.model_copy(update={'chapter_ordinal': 2})
    elif defect == 'protocol':
        receipt = receipt.model_copy(update={'protocol_version': 'unknown-protocol'})
    else:
        receipt = receipt.model_copy(update={'renderer_version': 'unknown-renderer'})
    assert chapter_identities(changed).writer_view_sha256 == receipt.writer_view_sha256
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
    _old_workflow(repo)
    before = repo.path('stage5/acceptance/body/ch02.json').read_bytes()
    calls = len(transport.calls)
    _apply(repo, _preview(repo))
    _run(repo, session, node_filter=('str06.stage4.hook_design', 'str06.stage4.compile', 'str06.stage5.hook'))
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


def test_unknown_call_and_accumulated_budget_survive_upgrade(tmp_path):
    from novel.llm.paid_requests import ask_json, ExecutionError, PaidRequestSession
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
    with pytest.raises((ExecutionError, TimeoutError)):
        ask_json(session, 'hook_design', request, lambda _: [])
    unit = WorkUnitBudget(repo.root.parent, 'run', 'str06.stage4.hook_design', 'executor', 'book')
    old_ledger = unit._read()
    old_budget = read_book_budget(repo.root.parent, 'run')
    assert old_ledger.turns[-1].state == 'unknown'
    _old_workflow(repo)
    _apply(repo, _preview(repo))
    assert unit._read() == old_ledger
    assert read_book_budget(repo.root.parent, 'run') == old_budget
    _, context = init_or_resume_candidate_workflow(repository=repo, base=repo.root.parent,
        code=prov.code_version(), root_sha1='a' * 40, source_identity=SOURCE,
        task_id='task-1', configured_model='fake', created_at=1759000001)
    session = PaidRequestSession(repo, context.ledger, Unconfirmed(), yes=True,
                                allow_source_export=True, provider='fake', sid='run')
    with pytest.raises((ExecutionError, ValueError), match='unknown|未知'):
        ask_json(session, 'hook_design', request, lambda _: [])
    assert unit._read() == old_ledger
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
        epoch=current.epoch._replace(contract_version=HOOK_UPGRADE['source_version']),
        contract_identity=HOOK_UPGRADE['source_identity'])
    repo.write(WORKFLOW_REL, workflow_to_disk(historical))
    original = {p: p.read_bytes() for prefix in ('stage5/body', 'stage5/acceptance/body')
                for p in (repo.root / prefix).glob('*')}
    calls = len(transport.calls)
    plan = preview(repo, source_identity=current.source_identity, code_version=prov.code_version())
    apply(repo, base=base, sid='9001', source_identity=current.source_identity,
          code_version=prov.code_version(), subject_sha256=digest(plan.model_dump(mode='json')),
          actor='测试', reason='同根恢复真实自动编排入口')
    next_action = operator.next_action(base, '9001')
    assert next_action.state == 'ready' and next_action.action is not None
    assert next_action.action.node == 'str06.stage4.hook_design'
    box = {'dispatched': [], 'processes': []}
    _install_inprocess_subprocess(monkeypatch, box)
    _install_step_argv(monkeypatch, box)
    run = agent.start(base, '9001', authorized=True, egress_authorized=True,
                      profiles=profiles, pause_before_node=None)
    assert agent.wait(run.run_id, 120)
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    assert {item['node'] for item in box['dispatched']} <= set(HOOK_UPGRADE['rebuild_nodes'])
    assert not any(owner.startswith('body_') for owner in transport.calls[calls:])
    assert all(p.read_bytes() == data for p, data in original.items())
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
    assert repo.read_text('stage5/body/ch01.txt') == '保留章稿。'
