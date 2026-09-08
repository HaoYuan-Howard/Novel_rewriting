import { useEffect, useState } from 'react'
import { useApprovals } from '@/hooks/useApprovals'
import { useEnums } from '@/hooks/useEnums'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { ErrorHint } from '@/components/state/States'
import { errorBody } from '@/api/errors'

const labels: Record<string, string> = {
  accept: '接受', rework: '定向返工', uncertain: '不确定', stop: '停止',
}

/** 节点接受与已读分开；只提交服务端工作包 token，不计算审核结论。 */
export function Stage2NodeReviewPanel({ sid, node, token }: {
  sid: string
  node: string
  token: string | null
}) {
  const approvals = useApprovals(sid)
  const enums = useEnums()
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [decision, setDecision] = useState('')
  const [owner, setOwner] = useState('')
  const [chapters, setChapters] = useState('')
  useEffect(() => { setDecision(''); setReason('') }, [token])
  const mutation = approvals.approveStage2Review
  const options = enums.data?.stage2_node_review_decisions
  const owners = enums.data?.stage2_node_review_owners
  const current = approvals.doc?.stage2_reviews?.[node]
  const isRework = decision === 'rework'
  const ordinals = chapters.trim() ? chapters.trim().split(/[,，\s]+/).map(Number) : []
  const validChapters = ordinals.every((n) => Number.isInteger(n) && n > 0)
  const ready = !!token && !!actor.trim() && !!reason.trim() && !!options?.includes(decision)
    && (!isRework || (!!owners?.includes(owner) && validChapters
      && (owner !== 'stage2.blueprint' || ordinals.length > 0)))
  return (
    <Panel className="px-18 py-14">
      <PaneHeader title={node + ' · 节点审阅决定'} meta={current?.state ?? '节点审阅状态未知'} />
      <p className="text-body text-ink-3">接受只绑定当前完整工作包；确认已读后仍需单独提交审阅决定。</p>
      {approvals.isError ? <div role="alert">{errorBody(approvals.error)}<ErrorHint error={approvals.error} /></div> : null}
      {enums.isError ? <div role="alert">{errorBody(enums.error)}<ErrorHint error={enums.error} /></div> : null}
      {current?.current_decision ? <p className="text-body">{current.current_decision.actor}：{current.current_decision.reason}</p> : null}
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
          <label>返工章号 <input aria-label="返工章号" value={chapters} onChange={(e) => setChapters(e.target.value)} placeholder="按顺序填写，用逗号分隔" /></label>
        </> : null}
      </div>
      <ActorReasonFields actor={actor} onActor={setActor} reason={reason} onReason={setReason}
        note={token ? '决定绑定当前已展示的完整工作包。' : '当前没有可提交的工作包 token。'}
        actions={<button type="button" className="rounded-btn border border-line-btn px-18 py-9" disabled={!ready || mutation.isPending || approvals.isError}
          onClick={() => token && mutation.mutate({
            node, review_token: token, actor: actor.trim(), reason: reason.trim(), decision,
            owner: isRework ? owner : null, chapter_ordinals: isRework ? ordinals : [],
          })}>{mutation.isPending ? '提交中…' : '提交节点审阅决定'}</button>} />
      {mutation.isError ? <div role="alert">{errorBody(mutation.error)}<ErrorHint error={mutation.error} /></div> : null}
    </Panel>
  )
}
