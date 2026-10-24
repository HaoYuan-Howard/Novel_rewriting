"""旧根拒识与历史保护；当前契约的免费重验、提交恢复和真实消费者。

rc27 不登记跨版本迁移。历史边的目标身份保持原值，只作为旧字节证据；
当前恢复在独立新根执行，不借升级、换标签或换算器沿用旧语义产物。
测试均用 fake transport，不调用真实模型。锁文件不属于业务证据快照。
"""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.contract_upgrades import REGISTERED_UPGRADES
from contracts.schema.upgrades import ContractUpgradePlanV1, UpgradeFile
from novel import prov
from novel.errors import ProvenanceRefused, TerminalState, UpgradeRefused
from novel.llm.paid_requests import (
    WORKFLOW_REL, candidate_contract_identity, digest,
    init_or_resume_candidate_workflow, open_candidate_workflow,
    roll_candidate_epoch, workflow_from_disk, workflow_to_disk,
)
from novel.orchestrator.contract_upgrade import (
    PENDING_REL, abort_pending, apply, preview,
)
from novel.store import Str06Repository
from tests.str06_facilities import candidate_epoch
from tests.test_str06_hook import _full_chain_wires as _chain_wires

EDGE = REGISTERED_UPGRADES[-1]
SOURCE = 'a' * 64
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
    避免跨文件依赖其夹具（避免跨文件引入额外夹具状态）。
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
                ('hook_design', 'stage4-hook-design-v5'), ('naming', '表层姓名')):
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
    """直接使用当前协议夹具，不通过历史 wire 转换生成现役输入。"""
    return _chain_wires()


def _old_workflow(repo, source=SOURCE, *, edge=EDGE):
    """合成历史身份拒识夹具；不声称旧语义已经满足当前契约。"""
    epoch = candidate_epoch()._replace(contract_version=edge['source_version'])
    record = open_candidate_workflow(epoch=epoch, source_identity=source,
                                     created_at=epoch.created_at)
    record = record._replace(contract_identity=edge['source_identity'])
    repo.write(WORKFLOW_REL, workflow_to_disk(record))
    return record


def _current_workflow(repo):
    return init_or_resume_candidate_workflow(
        repository=repo, base=repo.root.parent, code=prov.code_version(),
        root_sha1='a' * 40, source_identity=SOURCE, task_id='task-1',
        configured_model='fake', created_at=1759000000)


def _minimal(tmp_path, *, name='out', edge=EDGE):
    from novel.work_units import initialize_workflow
    base = tmp_path / name
    initialize_workflow(base)
    repo = Str06Repository(base / 'run')
    _old_workflow(repo, edge=edge)
    repo.write_text('stage5/hook/hook.txt', '原楔子证据。')
    repo.write_text('stage5/body/ch01.txt', '保留章稿。')
    return repo


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def _preview(repo):
    return preview(repo, source_identity=SOURCE, code_version=prov.code_version())


def _historical_plan(repo, *, edge=EDGE):
    """显式合成冻结历史计划；不调用现役升级引擎制造一条兼容边。"""
    archive = tuple(UpgradeFile(
        rel=rel, sha256=hashlib.sha256(repo.path(rel).read_bytes()).hexdigest())
        for rel in ('stage5/body/ch01.txt', 'stage5/hook/hook.txt'))
    return ContractUpgradePlanV1(
        upgrade=edge['id'], source_contract_identity=edge['source_identity'],
        target_contract_identity=edge['target_identity'], source_identity=SOURCE,
        workflow_sha256=hashlib.sha256(repo.path(WORKFLOW_REL).read_bytes()).hexdigest(),
        code_version='frozen-historical-code', archive=archive, retain=(),
        rebuild_nodes=edge['rebuild_nodes'])


def _apply(repo, plan):
    return apply(repo, base=repo.root.parent, sid=repo.root.name,
                 source_identity=SOURCE, code_version=prov.code_version(),
                 subject_sha256=digest(plan.model_dump(mode='json')),
                 actor='测试', reason='核验历史边拒识')


def _historical_pending(repo, window):
    """冻结历史中断窗口；字节 setup 不通过当前 preview/apply 运行迁移。"""
    before = workflow_from_disk(repo.inspect(WORKFLOW_REL))
    plan = _historical_plan(repo)
    subject = digest(plan.model_dump(mode='json'))
    after = before._replace(
        epoch=roll_candidate_epoch(
            before.epoch, reason='contract_drift', code_version='frozen-historical-code',
            task_id=before.epoch.task_id, session_run_id='historical-session',
            created_at=before.epoch.created_at + 1, contract_version=EDGE['target_version']),
        contract_identity=EDGE['target_identity'])
    record = dict(schema_version=1, plan=plan.model_dump(mode='json'),
                  before=workflow_to_disk(before), after=workflow_to_disk(after),
                  actor='历史测试操作者', reason='冻结历史窗口')
    record['seal'] = digest(record)
    repo.write_text(f'runtime/upgrades/{subject}/workflow.json',
                    repo.path(WORKFLOW_REL).read_text(encoding='utf-8'))
    moved = 0 if window == 'pending' else (1 if window == 'archive' else len(plan.archive))
    for row in plan.archive[:moved]:
        target = repo.path(f'runtime/upgrades/{subject}/files/{row.rel}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(repo.path(row.rel).read_bytes())
        repo.path(row.rel).unlink()
    if window in ('workflow', 'receipt'):
        repo.write(WORKFLOW_REL, record['after'])
    if window == 'receipt':
        repo.write(f'runtime/upgrades/{subject}/receipt.json',
                   dict(schema_version=1, subject_sha256=subject,
                        record=record, status='applied'))
    repo.write(PENDING_REL, record)
    return plan



@pytest.mark.parametrize('edge', REGISTERED_UPGRADES, ids=lambda e: e['id'])
def test_historical_edges_refuse_preview_and_keep_original_bytes(tmp_path, edge):
    repo = _minimal(tmp_path, edge=edge)
    before = _snapshot(repo.root.parent)
    assert edge['target_identity'] != candidate_contract_identity()
    for _ in range(2):
        with pytest.raises(UpgradeRefused, match='未登记|无法证明兼容'):
            _preview(repo)
    assert _snapshot(repo.root.parent) == before


def test_current_root_refuses_upgrade_preview_as_up_to_date(tmp_path):
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _current_workflow(repo)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused, match='无需升级'):
        _preview(repo)
    assert _snapshot(repo.root.parent) == before


def test_current_abort_identity_guard_keeps_a_current_root_readonly(tmp_path):
    from novel.orchestrator.contract_upgrade import _current_abort_pending
    repo, _, _ = _make_session(tmp_path, _full_chain_wires())
    _current_workflow(repo)
    before = _snapshot(repo.root.parent)
    assert _current_abort_pending(repo) is None
    assert _snapshot(repo.root.parent) == before


def test_abort_identity_guard_rejects_historical_epoch_under_current_table(tmp_path):
    from novel.orchestrator.contract_upgrade import _current_abort_pending
    repo = _minimal(tmp_path)
    raw = repo.inspect(WORKFLOW_REL)
    raw['contract_identity'] = candidate_contract_identity()
    repo.write(WORKFLOW_REL, raw)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused, match='旧根'):
        _current_abort_pending(repo)
    assert _snapshot(repo.root.parent) == before


def test_repeated_historical_apply_does_not_archive_or_refund(tmp_path):
    repo = _minimal(tmp_path)
    plan = _historical_plan(repo)
    before = _snapshot(repo.root.parent)
    for _ in range(2):
        with pytest.raises(UpgradeRefused, match='未登记|无法证明兼容'):
            _apply(repo, plan)
        assert _snapshot(repo.root.parent) == before
    assert not repo.path(PENDING_REL).exists()
    assert not repo.path('runtime/upgrades').exists()


@pytest.mark.parametrize('defect', ['source', 'identity', 'unknown_code'])
def test_historical_preview_drift_is_readonly(tmp_path, defect):
    repo = _minimal(tmp_path)
    source, code = SOURCE, prov.code_version()
    if defect == 'source':
        source = 'b' * 64
    elif defect == 'identity':
        raw = repo.inspect(WORKFLOW_REL)
        raw['contract_identity'] = '0' * 64
        repo.write(WORKFLOW_REL, raw)
    else:
        code = 'unknown'
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused):
        preview(repo, source_identity=source, code_version=code)
    assert _snapshot(repo.root.parent) == before


def test_frozen_output_root_refuses_upgrade_without_any_write(tmp_path):
    from novel import paths
    from novel.work_units import WorkUnitRefused
    repo = _minimal(tmp_path)
    plan = _historical_plan(repo)
    paths.workflow_manifest_path(repo.root.parent).unlink()
    before = _snapshot(repo.root.parent)
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        _apply(repo, plan)
    assert _snapshot(repo.root.parent) == before


@pytest.mark.parametrize('window', ['pending', 'archive', 'workflow', 'receipt'])
def test_historical_interrupted_upgrade_is_not_current_recovery(tmp_path, window):
    repo = _minimal(tmp_path)
    plan = _historical_pending(repo, window)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused, match='未登记|无法证明兼容'):
        _preview(repo)
    with pytest.raises((UpgradeRefused, ProvenanceRefused)):
        _apply(repo, plan)
    with pytest.raises(UpgradeRefused, match='尚未完成'):
        _current_workflow(repo)
    assert _snapshot(repo.root.parent) == before
    assert repo.path(PENDING_REL).exists()


@pytest.mark.parametrize('window', ['pending', 'archive'])
def test_abort_historical_pending_is_refused_without_any_write(tmp_path, window):
    repo = _minimal(tmp_path)
    _historical_pending(repo, window)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused, match='旧根.*只读|旧根.*不一致'):
        abort_pending(repo, base=repo.root.parent, sid=repo.root.name,
                      source_identity=SOURCE, code_version=prov.code_version(),
                      actor='测试', reason='新版不得清理旧日志')
    assert _snapshot(repo.root.parent) == before


def test_current_root_cannot_clear_a_historical_target_pending(tmp_path):
    repo = _minimal(tmp_path)
    _historical_pending(repo, 'pending')
    # 当前 workflow 也不能使另一目标契约的历史日志变成可清理日志。
    current = open_candidate_workflow(epoch=candidate_epoch(), source_identity=SOURCE,
                                      created_at=1759000000)
    repo.write(WORKFLOW_REL, workflow_to_disk(current))
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused, match='目标不是当前契约'):
        abort_pending(repo, base=repo.root.parent, sid=repo.root.name,
                      source_identity=SOURCE, code_version=prov.code_version(),
                      actor='测试', reason='历史日志不因当前外壳而可清理')
    assert _snapshot(repo.root.parent) == before


def test_corrupt_historical_pending_seal_is_refused_without_rewriting_evidence(tmp_path):
    repo = _minimal(tmp_path)
    _historical_pending(repo, 'pending')
    record = repo.inspect(PENDING_REL)
    record['seal'] = '0' * 64
    repo.write(PENDING_REL, record)
    before = _snapshot(repo.root.parent)
    with pytest.raises(ProvenanceRefused, match='seal'):
        _preview(repo)
    assert _snapshot(repo.root.parent) == before


@pytest.mark.parametrize('window', ['none', 'first_output', 'before_seal', 'after_seal'])
def test_new_root_current_commit_recovery_keeps_old_root_readonly(tmp_path, monkeypatch, window):
    from collections import Counter
    from novel.errors import UnsealedArtifact
    old = _minimal(tmp_path, name='old')
    historical = _snapshot(old.root.parent)
    repo, session, transport = _make_session(tmp_path, _full_chain_wires(), name='current')
    _current_workflow(repo)
    original_atomic, original_seal = repo._atomic_write, repo._write_commit_bundle
    hit = False

    def atomic(path, data):
        nonlocal hit
        original_atomic(path, data)
        if window == 'first_output' and str(path).replace('\\', '/').endswith(
                'stage2/characters/characters.json') and not hit:
            hit = True
            raise OSError('模拟当前输出组写入中断')

    def seal(bundle):
        nonlocal hit
        target = bundle['node'] == 'str06.stage2.compile' and not hit
        if target and window == 'before_seal':
            hit = True
            raise OSError('模拟当前封存前中断')
        result = original_seal(bundle)
        if target and window == 'after_seal':
            hit = True
            raise OSError('模拟当前封存后中断')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(repo, '_atomic_write', atomic)
        patch.setattr(repo, '_write_commit_bundle', seal)
        if window == 'none':
            _run(repo, session)
        else:
            with pytest.raises(OSError, match='模拟当前'):
                _run(repo, session)
            assert hit
    if window in ('first_output', 'before_seal'):
        with pytest.raises(UnsealedArtifact):
            repo.read_model('stage2/characters/characters.json')
    purchased = Counter(transport.calls)
    _run(repo, session)
    for owner in ('book', 'ch01', 'ch02', 'names', 'associate'):
        assert transport.calls.count(owner) == purchased[owner], owner
    calls = len(transport.calls)
    _run(repo, session)
    assert len(transport.calls) == calls, '当前同请求重放零新购'
    assert repo.inspect('runtime/workflow_status.json')['status'] == 'delivered'
    assert repo.read_model('stage2/characters/characters.json').contract_version == 'str06-rc27'
    assert _snapshot(old.root.parent) == historical


def test_historical_rejected_design_evidence_is_not_adopted_in_new_root(tmp_path):
    from novel.llm.transport import ChatRequest
    from novel.stages.chain import NODE_ORDER
    from novel.stages.common import SYS
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, deepcopy(wires), name='old')
    end = NODE_ORDER.index('str06.stage4.preserve') + 1
    _run(repo, session, node_filter=NODE_ORDER[:end])
    old_design = deepcopy(wires['design'])
    old_design['protocol_version'] = 'stage4-design-v10'
    transport.wires['design'] = old_design
    answer = session.send('design', ChatRequest(
        model='fake', system=SYS, user='设计目标书：冻结历史未采用回答。',
        reasoning_effort='high', thinking=True, json_mode=True, max_tokens=1000))
    session.record_acceptance(answer, mode='json', parse='ok',
                              errors=('历史协议拒收',), accepted=False)
    _old_workflow(repo)
    before = _snapshot(repo.root.parent)
    calls = len(transport.calls)
    with pytest.raises(UpgradeRefused):
        _preview(repo)
    current, current_session, current_transport = _make_session(tmp_path, wires, name='current')
    _run(current, current_session)
    assert current.read_model('stage4/design/body_design.json').contract_version == 'str06-rc27'
    assert current_transport.calls.count('design') == 1
    assert len(transport.calls) == calls
    assert _snapshot(repo.root.parent) == before


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
    # 同一当前协议内的免费重编译才有逐章精确失效语义；跨契约旧根只读，新根重新生成。
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



def test_workflow_lock_contention_stops_before_any_product_write(tmp_path):
    import novel.orchestrator.contract_upgrade as cu
    repo = _minimal(tmp_path)
    plan = _historical_plan(repo)
    before = _snapshot(repo.root.parent)

    class Busy:
        def __enter__(self):
            raise TimeoutError('timed out locking shared append log')

        def __exit__(self, *args):
            return False

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cu, 'path_transaction_lock', lambda _p: Busy())
        with pytest.raises(UpgradeRefused, match='争用|超时|正在进行'):
            _apply(repo, plan)
    assert _snapshot(repo.root.parent) == before
    assert not repo.path(PENDING_REL).exists()


def test_historical_open_issue_remains_unchanged_when_new_root_rebuilds(tmp_path):
    from contracts.schema.workflow import OwnerIssueV1
    from novel.stages.owner_revision import (
        OwnerRevisionRequired, discover_owner_issues, read_owner_issue_file,
    )
    repo, session, _ = _make_session(tmp_path, _full_chain_wires(), name='old')
    _run(repo, session)
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(
            repo, raised_by='str06.stage4.compile', owner='hook_design',
            node='str06.stage4.hook_design',
            issues=[OwnerIssueV1(code='hook_combination', item='materials',
                                 message='冻结历史未决组合')])
    assert read_owner_issue_file(repo, 'hook_design').status == 'open'
    _old_workflow(repo)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused):
        _preview(repo)
    current, current_session, _ = _make_session(tmp_path, _full_chain_wires(), name='current')
    _run(current, current_session)
    assert read_owner_issue_file(current, 'hook_design') is None
    assert read_owner_issue_file(repo, 'hook_design').status == 'open'
    assert _snapshot(repo.root.parent) == before


def test_historical_unknown_and_accumulated_cost_survive_new_root_rebuild(tmp_path):
    from novel.llm.paid_requests import ExecutionError, ask_text_once
    from novel.llm.transport import ChatRequest
    from novel.work_units import WorkUnitBudget, read_book_budget
    repo, session, _ = _make_session(tmp_path, _full_chain_wires(), name='old')
    _run(repo, session)

    class Unconfirmed:
        def send(self, request):
            raise TimeoutError('已发送，结果未知')

    session.transport = Unconfirmed()
    request = ChatRequest(model='fake', system='未知调用', user='冻结历史未知文本请求',
                          reasoning_effort='high', thinking=True, json_mode=False,
                          max_tokens=1024)
    with pytest.raises((ExecutionError, TimeoutError)):
        ask_text_once(session, 'hook_writer', request, lambda _: [])
    unit = WorkUnitBudget(repo.root.parent, 'run', 'str06.stage5.hook', 'executor', 'book')
    ledger, budget = unit._read(), read_book_budget(repo.root.parent, 'run')
    assert ledger.turns[-1].state == 'unknown'
    _old_workflow(repo)
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused):
        _preview(repo)
    current, current_session, _ = _make_session(tmp_path, _full_chain_wires(), name='current')
    _run(current, current_session)
    assert unit._read() == ledger
    assert read_book_budget(repo.root.parent, 'run') == budget
    assert _snapshot(repo.root.parent) == before


def test_operator_and_agent_rebuild_new_root_and_replay_without_new_purchase(tmp_path, monkeypatch):
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
    old = _minimal(tmp_path, name='old')
    historical = _snapshot(old.root.parent)
    base = tmp_path / 'current'
    initialize_workflow(base)
    source = tmp_path / FILENAME
    source.write_text(SOURCE_TEXT, encoding='utf-8')
    assert cli.main(['run', 'stage0.remove', '--source', str(source), '--base', str(base)]) == 0
    assert cli.main(['run', 'stage1.partition', '9001', '--base', str(base)]) == 0
    monkeypatch.setattr('novel.stages.chain.load_config', lambda: Config(
        model='deepseek-v4-pro', thinking_mode='on'))
    transport = _install_fake_transport(monkeypatch, _full_chain_wires())
    profiles = _profiles()
    for node in NODE_ORDER:
        for step in build_steps(node, '9001', base=base,
                                executor_backend=profiles.executor_default if NODES[node].paid else None,
                                egress_authorized=True if NODES[node].paid else None):
            assert cli.main(step.argv[step.argv.index('run'):]) == 0
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    repo = Str06Repository(paths.resolve_novel_dir(base, '9001'))
    original = {p.relative_to(repo.root).as_posix(): p.read_bytes()
                for prefix in ('stage5/body', 'stage5/acceptance/body', 'stage7/final')
                for p in (repo.root / prefix).glob('*') if p.is_file()}
    calls = len(transport.calls)
    box = {'dispatched': [], 'processes': []}
    _install_inprocess_subprocess(monkeypatch, box)
    _install_step_argv(monkeypatch, box)
    run = agent.start(base, '9001', authorized=True, egress_authorized=True,
                      profiles=profiles, pause_before_node=None)
    assert agent.wait(run.run_id, 120)
    assert operator.next_action(base, '9001').state == 'pipeline_complete'
    assert not box['dispatched'] and len(transport.calls) == calls
    assert all(repo.path(rel).read_bytes() == data for rel, data in original.items())
    assert _snapshot(old.root.parent) == historical


@pytest.mark.real_code_version
def test_cli_historical_preview_and_apply_refuse_for_free_without_writes(tmp_path):
    import subprocess
    import sys
    from contracts.schema import encode_artifact
    from novel.stages.chain import load_stage1_chapters
    repo = _minimal(tmp_path)
    rel = 'stage1/main_text.json'
    source_path = repo.root / rel
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(encode_artifact(rel, [
        {'chapter_ordinal': 1, 'text': '第一章源事实。'},
        {'chapter_ordinal': 2, 'text': '第二章源事实。'}]), ensure_ascii=False),
                           encoding='utf-8', newline='\n')
    _old_workflow(repo, source=load_stage1_chapters(repo.root.parent, 'run').source_identity)
    before = _snapshot(repo.root.parent)
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'novel.cli',
               'contract-upgrade', 'run', '--base', str(repo.root.parent)]
    for argv in (command, [*command, '--apply', '--subject-sha256', 'f' * 64,
                           '--actor', '测试', '--reason', '旧根拒识验证']):
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=60)
        assert result.returncode == 2, (result.stdout, result.stderr)
        assert '未登记' in result.stderr or '无法证明兼容' in result.stderr
        assert _snapshot(repo.root.parent) == before


def test_historical_rounds_are_not_refunded_by_refused_upgrade(tmp_path):
    from novel.work_units import WorkUnitBudget
    repo = _minimal(tmp_path)
    budget = WorkUnitBudget(repo.root.parent, repo.root.name, EDGE['refund_nodes'][0])
    for index in range(4):
        reservation = budget.reserve('a' * 64, run_id=f'old-{index}')
        budget.finish(reservation.turn_id, outcome='failed', reason_code='content_rejected')
    assert budget.remaining == 0
    original = budget._read()
    before = _snapshot(repo.root.parent)
    with pytest.raises(UpgradeRefused):
        _apply(repo, _historical_plan(repo))
    assert budget._read() == original and budget.remaining == 0
    assert _snapshot(repo.root.parent) == before


def test_historical_converter_is_unregistered_and_cannot_create_wire(tmp_path):
    from novel.orchestrator.upgrade_converters import CONVERTERS, convert_archived_wire
    repo = _minimal(tmp_path)
    before = _snapshot(repo.root.parent)
    assert not CONVERTERS
    with pytest.raises(TerminalState, match='未登记无损换算器'):
        convert_archived_wire(repo, upgrade_id=EDGE['id'], node='str06.stage5.hook',
                              wire_dir=tmp_path / 'wire', actor='测试', reason='旧语义不可补造')
    assert not (tmp_path / 'wire').exists()
    assert _snapshot(repo.root.parent) == before


def test_current_purchased_replay_after_session_restart_keeps_adoptions_and_cost(tmp_path):
    from novel.llm.ledger import ledger_path
    from novel.llm.paid_requests import PaidRequestSession
    from novel.work_units import sealed_commit_bundles
    repo, session, _ = _make_session(tmp_path, _full_chain_wires())
    _current_workflow(repo)
    _run(repo, session)
    cost = ledger_path(repo.root.parent).read_bytes()
    adoptions = {str(p): bundle.adoptions for p, bundle in sealed_commit_bundles(repo.root)}
    original_prose = repo.path('stage5/body/ch01.txt').read_bytes()
    _, context = _current_workflow(repo)
    transport = _RoutingTransport(_full_chain_wires())
    session = PaidRequestSession(repo, context.ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake', sid='run')
    _run(repo, session)
    assert not transport.calls
    # cache行允许追加；原实付行保持为账本前缀，不重复购买。
    assert ledger_path(repo.root.parent).read_bytes().startswith(cost)
    assert repo.path('stage5/body/ch01.txt').read_bytes() == original_prose
    current = {str(p): bundle.adoptions for p, bundle in sealed_commit_bundles(repo.root)}
    assert all(current[path] == refs for path, refs in adoptions.items())
