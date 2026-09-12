import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { ActionButton } from '@/components/ui/ActionButton'
import { ErrorHint } from '@/components/state/States'
import { QueryStates } from '@/components/state/QueryStates'
import { lookupAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { invalidateBookReadModels } from '@/hooks/bookReadModels'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type {
  PrepareDecisionRequest, PrepareDraft, PrepareEditPreview, PrepareEditRequest,
  PrepareState, PrepareWorkpack,
} from '@/types/prepare'
import type { SourceBlurbMaterials } from '@/types/stages'

const STATE_LABELS: Record<PrepareState['state'], string> = {
  missing: '尚未准备', invalid: '准备稿有问题', pending: '待接受',
  accepted: '准备已接受', stale: '准备已过期', unknown: '准备状态无法验证',
}
const SUPPORT_LABELS = { supported: '正文支持', hook_supported: '原钩子独立支持', unresolved: '未决事实' }

/** 准备接受先于候选生成，故必须独立于 Hook 查询的 missing/error 状态。 */
export function PreparePanel({ sid }: { sid: string }) {
  const q = useQuery({ queryKey: qk.stage6Prepare(sid), queryFn: () => api.getStage6Prepare(sid), retry: false })
  const workpack = useQuery({
    queryKey: qk.stage6PrepareWorkpack(sid), queryFn: () => api.getStage6PrepareWorkpack(sid), retry: false,
    enabled: Boolean(q.data?.status.prepare && !q.data.status.pending_commit),
  })
  const { doc } = useApprovals(sid)
  const enums = useEnums()
  const ask = useAskJob()
  const actions = doc?.gate.s5 ? doc.spawns.s5 ?? [] : []
  return <section aria-label="文案准备" className="mx-30 mt-24 rounded-card border border-line bg-card p-20">
    <h1 className="m-0 text-screen font-semibold">文案准备</h1>
    <p className="prose-cn text-body text-ink-3">
      核对原钩子的事实支持与连续开篇范围。准备接受后生成三候选，候选仍需另行审判和定稿。
    </p>
    <QueryStates q={q} loadingLabel="读取准备状态" missing={{ title: '准备状态暂不可读' }}>
      {(view) => <>
        <div role="status" className="rounded-block bg-sunken-2 px-14 py-10">
          <strong>{STATE_LABELS[view.status.state]}</strong>
          <p className="prose-cn mb-0">{view.status.reason}</p>
          {view.status.pending_commit ? <p>已有保存的修订等待恢复，请先完成恢复。</p> : null}
        </div>
        {view.status.review ? <p className="prose-cn text-tag text-ink-3">
          最近决定：{view.status.review.actor} · {view.status.review.reason}
        </p> : null}
        {view.status.prepare ? <PrepareFacts materials={view.status.prepare.source_blurb} /> : null}
        {workpack.isError ? <div role="alert" className="text-warn-ink">
          准备审阅材料读取失败：{errorBody(workpack.error)}<ErrorHint error={workpack.error} />
        </div> : null}
        {workpack.isLoading && view.status.prepare && !view.status.pending_commit ? <p>读取准备审阅材料…</p> : null}
        {workpack.data && view.status.prepare && !view.status.pending_commit ? <PrepareReview key={workpack.data.state.review_token}
          sid={sid} workpack={workpack.data} refreshing={workpack.isFetching} /> : null}
      </>}
    </QueryStates>
    <div className="mt-14 flex flex-wrap gap-10">
      {actions.map((action) => {
        const resolved = lookupAction(enums.data, action)
        return <ActionButton key={action.kind + (action.extra ?? []).join(' ')} action={resolved}
          disabled={!resolved} disabledReason="正在读取后端动作能力"
          onClick={() => ask({ ...action, sid, ordinal: null, required_inputs: resolved?.required_inputs })}>
          {action.label}
        </ActionButton>
      })}
    </div>
  </section>
}

function PrepareFacts({ materials }: { materials: SourceBlurbMaterials }) {
  const opening = materials.opening
  return <div className="mt-16 flex flex-col gap-10">
    <h2 className="m-0 text-section font-semibold">开篇范围</h2>
    <p className="prose-cn m-0">{opening.kind === 'proposed'
      ? `第 1 章至第 ${opening.through_chapter} 章（包含末章）。${opening.rationale}`
      : `范围未决：${opening.reason}`}</p>
    <h2 className="mb-0 text-section font-semibold">原钩子主张 · {materials.facts.length} 条</h2>
    {materials.facts.length === 0 ? <p>本次准备未登记独立事实主张；仍须核验是否遗漏。</p> : null}
    {materials.facts.map((fact) => <article key={fact.claim_id}
      className="rounded-block border border-line-row px-14 py-11">
      <div className="text-tag text-ink-4">{fact.claim_id} · {SUPPORT_LABELS[fact.kind]}</div>
      <p className="prose-cn mb-6 mt-5">{fact.claim}</p>
      <div className="prose-cn text-body text-ink-3">{fact.kind === 'unresolved' ? fact.reason
        : fact.kind === 'hook_supported'
          ? fact.hook_evidence.source?.text ?? fact.hook_evidence.unresolved_locator ?? '证据尚未定位'
          : fact.evidence.source?.text ?? '证据尚未定位'}</div>
    </article>)}
  </div>
}

function PrepareReview({ sid, workpack, refreshing }: {
  sid: string; workpack: PrepareWorkpack; refreshing: boolean
}) {
  const qc = useQueryClient()
  const [actor, setActor] = useState('human-ui')
  const [reason, setReason] = useState('')
  const [decision, setDecision] = useState<PrepareDecisionRequest['decision'] | ''>('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(JSON.stringify(workpack.edit_template.source_blurb, null, 2))
  const [previewed, setPreviewed] = useState<{ request: PrepareEditRequest; preview: PrepareEditPreview } | null>(null)
  const invalidate = async () => {
    await invalidateBookReadModels(qc, sid)
    await qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    await qc.invalidateQueries({ queryKey: qk.run(sid) })
  }
  const preview = useMutation({ mutationFn: (req: PrepareEditRequest) => api.previewStage6PrepareEdit(sid, req),
    onSuccess: (value, request) => setPreviewed({ request, preview: value }) })
  const apply = useMutation({ mutationFn: (req: PrepareEditRequest) => api.applyStage6PrepareEdit(sid, req), onSettled: invalidate })
  const review = useMutation({ mutationFn: (req: PrepareDecisionRequest) => api.reviewStage6Prepare(sid, req), onSettled: invalidate })
  let parsed: PrepareDraft | null = null
  let parseError = ''
  try {
    const value: unknown = JSON.parse(draft)
    if (!value || typeof value !== 'object' || !('facts' in value) || !Array.isArray(value.facts) || !('opening' in value)) {
      throw new Error('编辑内容需要事实列表与开篇范围')
    }
    parsed = value as PrepareDraft
  } catch (error) { parseError = error instanceof Error ? error.message : '编辑内容无法读取' }
  const token = workpack.state.review_token
  const request: PrepareEditRequest | null = parsed && token && actor.trim() && reason.trim()
    ? { review_token: token, actor: actor.trim(), reason: reason.trim(), source_blurb: parsed } : null
  const currentPreview = request && previewed && JSON.stringify(request) === JSON.stringify(previewed.request) ? previewed : null
  const busy = refreshing || preview.isPending || apply.isPending || review.isPending
  const error = preview.error ?? apply.error ?? review.error
  return <div className="mt-18 border-t border-line pt-16">
    <details>
      <summary className="cursor-pointer font-semibold">核验原文与原钩子 · {workpack.main_text.length} 章</summary>
      <p className="text-body text-ink-3">钩子独立事实不要求正文重复；正文有反证时须重新裁决。</p>
      <h3 className="text-body font-semibold">原钩子</h3>
      <div className="prose-cn whitespace-pre-wrap text-read">{workpack.hook_copy.text}</div>
      {workpack.main_text.map((chapter) => <details key={chapter.chapter_ordinal} className="mt-12">
        <summary className="cursor-pointer">源正文第 {chapter.chapter_ordinal} 章</summary>
        <div className="prose-cn max-w-[var(--measure-compare)] whitespace-pre-wrap text-read">{chapter.text}</div>
      </details>)}
    </details>
    <fieldset disabled={busy} className="mt-16 flex flex-col gap-10 border-0 p-0">
      <label className="flex flex-col gap-4">准备审阅人
        <input aria-label="准备审阅人" value={actor} onChange={(event) => setActor(event.target.value)} />
      </label>
      <label className="flex flex-col gap-4">准备审阅依据与理由
        <textarea aria-label="准备审阅理由" rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      <button type="button" className="self-start" onClick={() => setEditing(!editing)}>
        {editing ? '收起准备修订' : '修订事实支持或开篇范围'}
      </button>
      {editing ? <div className="flex flex-col gap-10 rounded-block border border-line-row p-14">
        <p className="prose-cn m-0 text-body text-ink-3">修订后重新核验准备材料。证据填写源章与连续段号，正式坐标由后端编译。</p>
        {parsed?.facts.map((fact, index) => <label key={fact.claim_id} className="flex flex-col gap-4">
          主张 {fact.claim_id}
          <textarea aria-label={'准备主张 ' + fact.claim_id} rows={2} value={fact.claim} onChange={(event) => {
            const next = structuredClone(parsed!)
            next.facts[index].claim = event.target.value
            setDraft(JSON.stringify(next, null, 2))
          }} />
        </label>)}
        <details><summary className="cursor-pointer">支持分类、证据与范围编辑</summary>
          <textarea aria-label="准备完整编辑数据" rows={14} className="mt-8 w-full font-mono text-sm2"
            value={draft} onChange={(event) => setDraft(event.target.value)} />
        </details>
        {parseError ? <p role="alert">{parseError}</p> : null}
        <button type="button" disabled={!request || busy} onClick={() => { if (request) preview.mutate(request) }}>
          免费预检准备修订
        </button>
        {currentPreview ? <div role="status">
          <p>{currentPreview.preview.would_apply ? '预检通过；写回后仍须语义接受。' : '预检未通过，请订正。'}</p>
          {currentPreview.preview.findings.map((finding, index) => <p key={index}>{finding.detail}</p>)}
        </div> : previewed ? <p>内容已改变，请重新预检。</p> : null}
        <button type="button" disabled={!currentPreview?.preview.would_apply || busy}
          onClick={() => { if (currentPreview) apply.mutate(currentPreview.request) }}>
          写回准备修订（不调用模型）
        </button>
      </div> : null}
      <label className="flex flex-col gap-4">准备决定
        <select aria-label="准备决定" value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>
          <option value="">请选择本次决定</option>
          <option value="accept">接受准备材料</option>
          <option value="rework">要求修订准备材料</option>
          <option value="uncertain">保留未决</option>
        </select>
      </label>
      <button type="button" disabled={!token || !actor.trim() || !reason.trim() || !decision || busy || editing}
        onClick={() => { if (token && decision) review.mutate({ review_token: token,
          actor: actor.trim(), reason: reason.trim(), decision, edit: null }) }}>
        提交准备决定（不调用模型）
      </button>
    </fieldset>
    {error ? <div role="alert" className="mt-10 text-warn-ink">{errorBody(error)}<ErrorHint error={error} /></div> : null}
    {apply.data || review.data ? <p role="status">作业状态：{(apply.data ?? review.data)?.state}；准备状态以刷新后的材料为准。</p> : null}
  </div>
}
