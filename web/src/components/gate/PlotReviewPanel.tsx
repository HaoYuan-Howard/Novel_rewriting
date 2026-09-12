import { useState } from 'react'
import { useApprovals } from '@/hooks/useApprovals'
import { useEnums } from '@/hooks/useEnums'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { ErrorHint } from '@/components/state/States'
import { errorBody } from '@/api/errors'
import type { BlueprintReviewWorkpack } from '@/types/stages'
import type { Dimension } from '@/types/plot'
import type { DimensionVerdict, PlotReviewResponse, KnowledgeLinkDecision } from '@/types/plot.generated'

const dimensionLabels: Record<string, string> = {
  completeness: '剧情完整', attribution: '归因与人物立场', causal_continuity: '因果连续',
  epistemic_reveal: '知晓与披露', carry_state: '持续状态',
}
const labels: Record<string, string> = {
  pass: '通过', issue: '存在问题', unver: '无法判定', not_applicable: '不适用',
  accept: '接受本范围', rework: '定向返修', uncertain: '无法判定', stop: '停止',
}
type VerdictDraft = { state: DimensionVerdict['state'] | ''; reason: string; refs: string }
function arrayJson<T>(value: string): T[] {
  const parsed: unknown = JSON.parse(value)
  if (!Array.isArray(parsed)) throw new Error('请填写 JSON 数组')
  return parsed as T[]
}

/** 范围裁决只包含五个维度与实际问题；重新取得材料后由 token key 清空旧草稿。 */
export function PlotReviewPanel({ sid, pack }: { sid: string; pack: BlueprintReviewWorkpack }) {
  const approvals = useApprovals(sid)
  const enums = useEnums()
  const [actor, setActor] = useState('human-ui')
  const [reason, setReason] = useState('')
  const [decision, setDecision] = useState('')
  const [owner, setOwner] = useState('')
  const [chapters, setChapters] = useState(pack.scope.startsWith('chapter:') ? pack.scope.split(':')[1] : '')
  const [verdicts, setVerdicts] = useState<Partial<Record<Dimension, VerdictDraft>>>({})
  const [newIssues, setNewIssues] = useState('[]')
  const [dispositions, setDispositions] = useState(JSON.stringify(pack.material.issues
    .filter((row) => row.state === 'open').map((row) => ({ issue_id: row.issue_id, state: 'open', reason: '' })), null, 2))
  const pending = new Set(pack.knowledge_links.pending_obligation_ids)
  const obligations = pack.knowledge_links.scope.obligations.filter((row) => pending.has(row.obligation_id))
  const [links, setLinks] = useState(JSON.stringify(obligations.map((row) => ({
    obligation_id: row.obligation_id, expected_subject_sha256: row.subject_sha256,
    resolution: { kind: 'unresolved', reason: '' },
  })), null, 2))
  const dimensions = enums.data?.plot_review_dimensions as Dimension[] | undefined
  const states = enums.data?.plot_dimension_states as DimensionVerdict['state'][] | undefined
  const choices = enums.data?.stage2_node_review_decisions
  const owners = enums.data?.stage2_node_review_owners
  const mutation = approvals.approveStage2Review
  const isRework = decision === 'rework'
  const ordinals = chapters.trim() ? chapters.trim().split(/[,，\s]+/).map(Number) : []
  let jsonError = ''
  let parsedIssues: PlotReviewResponse['new_issues'] = []
  let parsedDispositions: PlotReviewResponse['dispositions'] = []
  let parsedLinks: KnowledgeLinkDecision[] = []
  try {
    parsedIssues = arrayJson(newIssues)
    parsedDispositions = arrayJson(dispositions)
    parsedLinks = arrayJson(links)
  } catch (error) { jsonError = error instanceof Error ? error.message : 'JSON 无法读取' }
  const completed = !!dimensions?.length && dimensions.every((dimension) => {
    const row = verdicts[dimension]
    return !!row?.state && states?.includes(row.state)
      && (!['unver', 'not_applicable'].includes(row.state) || !!row.reason.trim())
      && (row.state !== 'issue' || !!row.refs.trim())
  })
  const ready = completed && !!actor.trim() && !!reason.trim() && !!choices?.includes(decision)
    && !jsonError && !mutation.isPending && !approvals.isError && !enums.isError
    && (!isRework || (!!owners?.includes(owner)
      && ordinals.every((o) => Number.isInteger(o) && o > 0)
      && (owner !== 'stage2.blueprint' || ordinals.length > 0)))
  const change = (dimension: Dimension, update: Partial<VerdictDraft>) => setVerdicts((previous) => ({
    ...previous, [dimension]: { state: '', reason: '', refs: '', ...previous[dimension], ...update },
  }))
  const submit = () => {
    if (!ready || !dimensions) return
    const coverage = Object.fromEntries(dimensions.map((dimension) => {
      const row = verdicts[dimension]!
      return [dimension, { state: row.state, reason: row.reason.trim() || null,
        issue_refs: row.state === 'issue' ? row.refs.trim().split(/[,，\s]+/) : [] }]
    })) as PlotReviewResponse['coverage']
    mutation.mutate({
      node: 'stage2.blueprint', scope: pack.scope, review_token: pack.review_token,
      actor: actor.trim(), reason: reason.trim(), decision,
      owner: isRework ? owner : null, chapter_ordinals: isRework ? ordinals : [],
      review: null,
      plot_review: {
        decision: decision === 'accept' ? 'accept' : isRework ? 'revise' : 'unver',
        reason: reason.trim(), coverage, new_issues: parsedIssues, dispositions: parsedDispositions,
      },
      knowledge_links: { scope_sha256: pack.knowledge_links.scope.scope_sha256,
        actor: actor.trim(), reason: reason.trim(), decisions: parsedLinks },
    })
  }
  return <Panel className="px-18 py-14">
    <PaneHeader title={pack.scope === 'cross_chapter' ? '跨章复核决定' : '章节审阅决定'} />
    <p className="text-body text-ink-3">逐维度填写结论。返修须关联实际问题；历史问题须明确保留、解决或撤销。</p>
    {enums.isError ? <div role="alert">{errorBody(enums.error)}<ErrorHint error={enums.error} /></div> : null}
    {!dimensions || !states ? <p role="status">审阅选项尚不可用，无法提交。</p> : dimensions.map((dimension) => {
      const row = verdicts[dimension]
      return <div key={dimension} className="my-10 flex flex-wrap items-center gap-10">
        <label>{dimensionLabels[dimension] ?? dimension} <select aria-label={dimensionLabels[dimension] ?? dimension}
          value={row?.state ?? ''} onChange={(e) => change(dimension, { state: e.target.value as VerdictDraft['state'] })}>
          <option value="">选择结论</option>
          {states.map((state) => <option key={state} value={state}>{labels[state] ?? state}</option>)}
        </select></label>
        <input aria-label={(dimensionLabels[dimension] ?? dimension) + '说明'} className="grow rounded-btn border border-line-2 p-8"
          placeholder="说明（无法判定或不适用时必填）" value={row?.reason ?? ''}
          onChange={(e) => change(dimension, { reason: e.target.value })} />
        {row?.state === 'issue' ? <input aria-label={(dimensionLabels[dimension] ?? dimension) + '问题引用'}
          placeholder="问题 ID，用逗号分隔" value={row.refs} onChange={(e) => change(dimension, { refs: e.target.value })} /> : null}
      </div>
    })}
    <details className="my-12 rounded-block border border-line-2 p-12">
      <summary>实际问题与历史处置</summary>
      <p>新增问题使用 new:1 等本轮键，填写 dimension、problem、subjects 与 evidence_refs。漏抽可引用章边界和 p:起段:止段。</p>
      <label>新增问题（JSON）<textarea aria-label="新增问题（JSON）" rows={8}
        className="block w-full rounded-btn border border-line-2 p-8 font-mono text-sm2"
        value={newIssues} onChange={(e) => setNewIssues(e.target.value)} /></label>
      {pack.material.issues.map((row) => <p key={row.issue_id}>{row.issue_id} · {row.state} · {row.problem}</p>)}
      <label>历史问题处置（JSON）<textarea aria-label="历史问题处置（JSON）" rows={6}
        className="block w-full rounded-btn border border-line-2 p-8 font-mono text-sm2"
        value={dispositions} onChange={(e) => setDispositions(e.target.value)} /></label>
    </details>
    <details className="my-12 rounded-block border border-line-2 p-12" open={obligations.length > 0}>
      <summary>必要知识时点 · 待处理 {obligations.length} 项</summary>
      <p>只处理已登记知识事件。at_beats 选择支持主体 subject_refs 和触发节点 trigger_beat_ref；at_entry 只使用开放的章首主体。证据不足保持 unresolved。</p>
      {obligations.map((row) => <div key={row.obligation_id} className="my-8 border-b border-line-2 pb-8">
        <p>{row.kind} · {row.subject_ref.kind} / {row.subject_ref.ref} · 第 {row.chapter_ordinal} 章</p>
        <pre className="overflow-auto whitespace-pre-wrap text-sm2">{JSON.stringify(row, null, 2)}</pre>
      </div>)}
      <label>知识时点决定（JSON）<textarea aria-label="知识时点决定（JSON）" rows={8}
        className="block w-full rounded-btn border border-line-2 p-8 font-mono text-sm2"
        value={links} onChange={(e) => setLinks(e.target.value)} /></label>
    </details>
    {jsonError ? <p role="alert">{jsonError}</p> : null}
    <div className="my-12 flex flex-wrap gap-12">
      <label>范围决定 <select aria-label="范围决定" value={decision} onChange={(e) => setDecision(e.target.value)}>
        <option value="">选择决定</option>{choices?.map((v) => <option key={v} value={v}>{labels[v] ?? v}</option>)}
      </select></label>
      {isRework ? <>
        <label>返工属主 <select aria-label="返工属主" value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">选择属主</option>{owners?.map((v) => <option key={v}>{v}</option>)}
        </select></label>
        <label>返工章号 <input aria-label="返工章号" value={chapters} onChange={(e) => setChapters(e.target.value)} /></label>
      </> : null}
    </div>
    <ActorReasonFields actor={actor} onActor={setActor} reason={reason} onReason={setReason}
      note="提交绑定本次范围的原文、蓝图、依赖与问题材料。"
      actions={<button type="button" className="rounded-btn border border-line-btn px-18 py-9"
        disabled={!ready} onClick={submit}>{mutation.isPending ? '提交中…' : '提交范围审阅决定'}</button>} />
    {approvals.isError ? <div role="alert">{errorBody(approvals.error)}<ErrorHint error={approvals.error} /></div> : null}
    {mutation.isError ? <div role="alert">{errorBody(mutation.error)}<ErrorHint error={mutation.error} /></div> : null}
  </Panel>
}
