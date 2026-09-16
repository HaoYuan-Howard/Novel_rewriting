import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { ActionButton } from '@/components/ui/ActionButton'
import { QueryStates } from '@/components/state/QueryStates'
import { lookupAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type { PrepareState } from '@/types/prepare'
import type { SourceBlurbMaterials } from '@/types/stages'

const STATE_LABELS: Record<PrepareState['state'], string> = {
  missing: '尚未准备', invalid: '准备稿有问题', pending: '待自动接受',
  accepted: '准备已接受', stale: '准备已过期', unknown: '准备状态无法验证',
}
const SUPPORT_LABELS = { supported: '正文支持', hook_supported: '原钩子独立支持', unresolved: '未决事实' }

/** 准备接受先于候选生成；批次 B4 起只读展示，机械通过即自动接受。 */
export function PreparePanel({ sid }: { sid: string }) {
  const q = useQuery({ queryKey: qk.stage6Prepare(sid), queryFn: () => api.getStage6Prepare(sid), retry: false })
  const { doc } = useApprovals(sid)
  const enums = useEnums()
  const ask = useAskJob()
  const actions = doc?.gate.s5 ? doc.spawns.s5 ?? [] : []
  return <section aria-label="文案准备" className="mx-30 mt-24 rounded-card border border-line bg-card p-20">
    <h1 className="m-0 text-screen font-semibold">文案准备</h1>
    <p className="prose-cn text-body text-ink-3">
      核对原钩子的事实支持与连续开篇范围。机械校验通过后由系统自动接受；
      候选仍需另行审判和定稿（改选走 stage6.approve --pick）。
    </p>
    <QueryStates q={q} loadingLabel="读取准备状态" missing={{ title: '准备状态暂不可读' }}>
      {(view) => <>
        <div role="status" className="rounded-block bg-sunken-2 px-14 py-10">
          <strong>{STATE_LABELS[view.status.state]}</strong>
          <p className="prose-cn mb-0">{view.status.reason}</p>
          {view.status.pending_commit ? <p>已有保存的修订等待恢复，请先完成免费恢复。</p> : null}
        </div>
        {view.status.review ? <p className="prose-cn text-tag text-ink-3">
          最近决定：{view.status.review.actor} · {view.status.review.reason}
        </p> : null}
        {view.status.prepare ? <PrepareFacts materials={view.status.prepare.source_blurb} /> : null}
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
