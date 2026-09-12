import { useEffect, useState } from 'react'
import { useApprovals } from '@/hooks/useApprovals'
import { useEnums } from '@/hooks/useEnums'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { ErrorHint } from '@/components/state/States'
import { errorBody } from '@/api/errors'
import type { Stage2ReviewCoverage, Stage2ReviewException } from '@/types/stage2Review'

const labels: Record<string, string> = {
  accept: '接受', rework: '定向返工', uncertain: '不确定', stop: '停止',
}

/** 节点接受与已读分开；只提交服务端工作包 token，不计算审核结论。 */
export function Stage2NodeReviewPanel({ sid, node, token, coverage }: {
  sid: string
  node: 'stage2.global'
  token: string | null
  coverage?: Stage2ReviewCoverage | null
}) {
  const approvals = useApprovals(sid)
  const enums = useEnums()
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [decision, setDecision] = useState('')
  const [owner, setOwner] = useState('')
  const [reviewDefault, setReviewDefault] = useState('uncertain')
  const [reviewDraft, setReviewDraft] = useState<{ scope: string; text: string } | null>(null)
  useEffect(() => { setDecision(''); setReason(''); setReviewDraft(null); setReviewDefault('uncertain') }, [token])
  const mutation = approvals.approveStage2Review
  const options = enums.data?.stage2_node_review_decisions
  const owners = enums.data?.stage2_node_review_owners?.filter((value) => value !== 'stage2.blueprint')
  const current = approvals.doc?.stage2_reviews?.[node]
  const isRework = decision === 'rework'
  const coverageSha = coverage?.scope.scope_sha256
  const exceptionText = reviewDraft && reviewDraft.scope === coverageSha ? reviewDraft.text : JSON.stringify(
    isRework ? coverage?.pending_obligation_ids.map((obligation_id) => ({
      obligation_id, decision: 'rework', reason: reason.trim(),
    })) ?? [] : [], null, 2)
  let exceptions: Stage2ReviewException[] = []
  let reviewError = ''
  if (coverage && isRework) {
    try {
      const value: unknown = JSON.parse(exceptionText)
      if (!Array.isArray(value)) throw new Error('审阅例外必须是数组')
      exceptions = value as Stage2ReviewException[]
    } catch (error) { reviewError = error instanceof Error ? error.message : '审阅例外 JSON 无法读取' }
  }
  const ready = !!token && !!actor.trim() && !!reason.trim() && !!options?.includes(decision)
    && !reviewError
    && (!isRework || !!owners?.includes(owner))
  return (
    <Panel className="px-18 py-14">
      <PaneHeader title={node + ' · 节点审阅决定'} meta={current?.state ?? '节点审阅状态未知'} />
      <p className="text-body text-ink-3">接受只绑定当前完整工作包；确认已读后仍需单独提交审阅决定。</p>
      {approvals.isError ? <div role="alert">{errorBody(approvals.error)}<ErrorHint error={approvals.error} /></div> : null}
      {enums.isError ? <div role="alert">{errorBody(enums.error)}<ErrorHint error={enums.error} /></div> : null}
      {current?.current_decision ? <p className="text-body">{current.current_decision.actor}：{current.current_decision.reason}</p> : null}
      {current?.current_decision?.core_edits ? <details className="mb-12 rounded-block border border-line-2 p-12">
        <summary>已保存免费 Core 修订批次</summary>
        <p>人物与全局知识分别由所属节点写回。此记录保留原修订请求，新产生的待审义务仍需复核。</p>
        <pre className="max-h-280 overflow-auto whitespace-pre-wrap break-all text-mini">{JSON.stringify(current.current_decision.core_edits, null, 2)}</pre>
        <span className="break-all font-mono text-micro">预检身份：{current.current_decision.core_edit_prepared_sha256 ?? '不可验证'}</span>
      </details> : null}
      {current?.unavailable_reason ? <p role="alert">审阅来源未知：{current.unavailable_reason}</p> : null}
      {coverage ? <details className="mb-12 rounded-block border border-line-2 p-12">
        <summary>审阅义务：待审 {coverage.pending_obligation_ids.length} 项，继承 {coverage.inherited.length} 项</summary>
        <p>每项继承均由后端验证原决定和当前证据。删除问题仍需复审，隔离的 UNVER 保留原结论。</p>
        {coverage.scope.obligations.filter((row) => coverage.pending_obligation_ids.includes(row.obligation_id)).map((row) => (
          <p key={row.obligation_id}>{row.obligation_id} · {row.subject_path}
            {row.kind === 'removed_problem' ? ' · 原问题主体已删除，须确认修复' : ''}</p>
        ))}
        {coverage.prior_problems.map((row) => <p key={row.obligation.obligation_id}>
          原问题 {row.obligation.obligation_id}：{row.actor} · {row.reason}
        </p>)}
      </details> : null}
      <div className="mb-12 flex flex-wrap gap-10">
        <label>决定 <select aria-label={node + ' 审阅决定'} value={decision} onChange={(e) => setDecision(e.target.value)}>
          <option value="">选择决定</option>
          {options?.map((value) => <option key={value} value={value}>{labels[value] ?? value}</option>)}
        </select></label>
        {isRework ? <>
          <label>返工属主 <select aria-label="返工属主" value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">选择生产节点</option>
            {owners?.map((value) => <option key={value}>{value}</option>)}
          </select></label>
        </> : null}
      </div>
      {coverage && isRework ? <div className="mb-12">
        <label>其余待审项 <select aria-label="其余待审项决定" value={reviewDefault} onChange={(event) => setReviewDefault(event.target.value)}>
          {options?.filter((value) => value === 'accept' || value === 'uncertain').map((value) => (
            <option key={value} value={value}>{labels[value]}</option>
          ))}
        </select></label>
        <label>审阅例外（JSON）<textarea aria-label="审阅例外（JSON）" rows={8}
          className="block w-full rounded-btn border border-line-2 p-8 font-mono text-sm2"
          value={exceptionText} onChange={(event) => setReviewDraft({ scope: coverage.scope.scope_sha256, text: event.target.value })} /></label>
        <p>逐项填写问题与原因；只有亲自核实其余待审项后，才将“其余待审项”设为接受。</p>
        {reviewError ? <div role="alert">{reviewError}</div> : null}
      </div> : null}
      <ActorReasonFields actor={actor} onActor={setActor} reason={reason} onReason={setReason}
        note={token ? '决定绑定当前已展示的完整工作包。' : '当前没有可提交的工作包 token。'}
        actions={<button type="button" className="rounded-btn border border-line-btn px-18 py-9" disabled={!ready || mutation.isPending || approvals.isError}
          onClick={() => token && mutation.mutate({
            node, review_token: token, actor: actor.trim(), reason: reason.trim(), decision,
            owner: isRework ? owner : null, chapter_ordinals: [],
            knowledge_links: null,
            review: coverage ? { scope_sha256: coverage.scope.scope_sha256,
              default_decision: isRework ? reviewDefault : decision, exceptions } : null,
          })}>{mutation.isPending ? '提交中…' : '提交节点审阅决定'}</button>} />
      {mutation.isError ? <div role="alert">{errorBody(mutation.error)}<ErrorHint error={mutation.error} /></div> : null}
    </Panel>
  )
}
