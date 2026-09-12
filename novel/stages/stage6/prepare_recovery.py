"""原已购 Prepare stdout 的免费恢复；沿共享响应修复与 custody，不另购内容。"""
from __future__ import annotations
import json
from contracts.schema.stage6_prepare import PrepareResponse, PrepareProvenance
from novel import prov
from novel.run import RunContext
from novel.errors import ProvenanceRefused, ResponseRepairRequired, TerminalState
from novel.agents.session_client import SessionAgentClient
from novel.llm import agent_runs, stage6_prepare_runs as runs
from novel.stages.stage6.prepare import SYSTEM, NODE, MAX_TOKENS


def recover_raw_checkpoint(ctx: RunContext, prepared, *, source_response_sha256=None):
    """原 run 的已购 stdout → 同 schema → checkpoint；无 raw 绝不续买。"""
    from novel.llm import response_repair
    from novel.llm.client import judge_payload
    from novel.llm.prompts import required_top_level
    from novel.llm.transport import ChatResponse
    if ctx.paid or ctx.executor_profile is None:
        raise ProvenanceRefused('原 raw 编译只接受免费且配置已验证的原 run')
    if agent_runs.own_request_changed(ctx, runs.run_spec(), **prepared.request()) is not False:
        raise ProvenanceRefused('原 raw 编译请求与原 run 不一致')
    client = SessionAgentClient(ctx, ctx.executor_profile, None, max_semantic_turns=4)
    try:
        saved = client.recover_saved_response(SYSTEM, prepared.user, model_cls=PrepareResponse,
            purpose=NODE, max_tokens=MAX_TOKENS, recovery_policy=runs.RECOVERY_POLICY)
        if saved is None:
            raise ProvenanceRefused('原 Prepare 尚无完整已保存响应；保留原 owner，禁止自动重新购买')
        turn, attempt = saved
        acceptance = client.accepted_checkpoint(NODE)
        if source_response_sha256 is not None and source_response_sha256 != acceptance['ledger_response_sha256']:
            raise ProvenanceRefused('Prepare 原 stdout SHA 已变化，不能按旧恢复请求采用')
        response, why, gate, details = judge_payload(turn.content,
            ChatResponse(content=turn.content, model=acceptance['model']),
            require=required_top_level(PrepareResponse), model_cls=PrepareResponse, validator=None)
        binding = None
        record = None
        if response is None or why is not None:
            identity = agent_runs.response_repair_identity(ctx, runs.run_spec(), acceptance)
            chain = agent_runs.canonical_sha256(identity)
            try:
                draft = json.loads(acceptance['normalized_text'])
            except (ValueError, TypeError):
                draft = {}
            _rel, error = response_repair.keep_rejection(ctx.novel_dir, chain_id=chain,
                attempt_id=attempt, original=turn.content, candidate=draft, payload={
                    'sid': ctx.sid, 'node': NODE, 'purpose': NODE, 'run_id': ctx.run_id, 'code': ctx.code,
                    'model': acceptance['model'], 'model_index': 0, 'sample': acceptance['accepted_turn_index'],
                    'request_fingerprint': acceptance['request_fingerprint'], 'gate': gate,
                    'why': why, 'reject_detail': details, 'repair_eligible': True,
                    'eligibility_reason': '已购 Prepare 原响应的免费形状修复；同一原 schema 与 HARD 仍须重验',
                    'response_model': 'PrepareResponse', 'required_top_level': required_top_level(PrepareResponse),
                    'agent_response_repair_identity': identity,
                    'resume_node': NODE, 'resume_flags': ['--recover-prepared', ctx.run_id,
                        '--subject-sha256', acceptance['ledger_response_sha256']]})
            if error is not None:
                raise ProvenanceRefused('原 Prepare 响应隔离未完整落盘；禁止继续请求')
            records = response_repair.records_for_chain(ctx.novel_dir, chain)
            if len(records) != 1:
                raise ProvenanceRefused('原 Prepare 响应隔离链存在多个候选，拒绝挑选')
            record = records[0]
            if (record.rejection.get('agent_response_repair_identity') != identity
                    or agent_runs.canonical_sha256((record.root / response_repair.ORIGINAL_FILE).read_bytes())
                    != acceptance['ledger_response_sha256']):
                raise ProvenanceRefused('原 Prepare 隔离来源被修改')
            hint = (f'编辑 {record.root / response_repair.CANDIDATE_FILE} 后用 response-repair {ctx.sid} '
                    f'--base "{ctx.novel_dir.parent}" --attempt {attempt} --actor <id> --reason <why> 封存，'
                    f'再免费运行 stage6.prepare --recover-prepared {ctx.run_id} '
                    f'--subject-sha256 {acceptance["ledger_response_sha256"]}；不加 --yes、不改原配置')
            if record.resolution is None:
                raise ResponseRepairRequired('原 Prepare stdout 未通过原 schema，已建立免费修复工作项',
                    attempt_id=attempt, candidate_path=str(record.root / response_repair.CANDIDATE_FILE),
                    original_path=str(record.root / response_repair.ORIGINAL_FILE), hint=hint)
            if record.resolution.get('action') == 'decline':
                if record.outcome is None:
                    response_repair.write_outcome(record, decision='declined', gate=None, detail=None,
                        why='操作者放弃原 Prepare 响应的唯一免费修复')
                raise TerminalState('原 Prepare 响应修复已放弃，保留原 owner 与已购证据；禁止自动重新购买')
            binding = agent_runs.response_repair_binding(ctx, runs.run_spec(), acceptance, record)
            text = json.dumps(record.resolution['candidate'], ensure_ascii=False)
            response, why, gate, details = judge_payload(text,
                ChatResponse(content=text, model=acceptance['model']),
                require=required_top_level(PrepareResponse), model_cls=PrepareResponse, validator=None)
            if response is None or why is not None:
                if record.outcome is None:
                    response_repair.write_outcome(record, decision='rejected', gate=gate,
                        detail=details, why=why or '修复候选未通过原 schema')
                raise TerminalState('封存的 Prepare 修复候选未通过原 schema；保留原响应，禁止自动重新购买')
        # 此处的免费 context 已核原 run/request/profile 与完整 custody/ledger。
        # 原费用由后继来源回执认领；不能把 ctx 改为 paid 或冒充本轮新模型调用。
        usage = ctx.ledger.usage_for_complete_purposes([NODE], sid=ctx.sid, node=NODE,
            required_identities=[(NODE, row) for row in acceptance['attempt_links']])
        source = PrepareProvenance(code=ctx.code, root_clean_sha1=ctx.root_clean_sha1,
            inputs=ctx.edges, prompts={'PREPARE_SYS': prov.fingerprint(SYSTEM)},
            model=ctx.executor_profile.model, adopted_models=client.accepted_models(NODE), usage=usage)
        state = {'input_sha256': prepared.input_sha256, 'provenance': source.model_dump(mode='json')}
        agent_runs.write_checkpoint(ctx, runs.run_spec(), **prepared.request(),
            response_payload=response.model_dump(mode='json', by_alias=True), acceptance=acceptance,
            revision_key=f'candidate:{acceptance["accepted_turn_index"]}', state_payload=state,
            response_repair=binding)
        checkpoint = agent_runs.restore_checkpoint(ctx, runs.run_spec(), **prepared.request())
        if checkpoint is None:
            raise ProvenanceRefused('原 Prepare raw 编译后 checkpoint 未闭合')
        if record is not None and record.outcome is None:
            response_repair.write_outcome(record, decision='accepted', gate=None, detail=None,
                why='封存修复通过原 PrepareResponse；证据与范围 HARD 在 Prepare owner 提交时处理')
        return checkpoint
    finally:
        client.close()
