import { useState } from 'react'
import { Link } from 'react-router-dom'
import { errorBody } from '@/api/errors'
import { useEnums } from '@/hooks/useEnums'
import type { BlueprintRefinementRequest } from '@/types/plot.generated'
import { useBlueprintRefinement } from './useBlueprintRefinement'

const labels: Record<string, string> = {
  completeness: '剧情完整', attribution: '归因与人物立场', causal_continuity: '因果连续',
  epistemic_reveal: '知晓与披露', carry_state: '持续状态',
}

export function BlueprintRefinementPanel({ sid, ordinal }: { sid: string; ordinal: number }) {
  const [origin, setOrigin] = useState<'design' | 'structure'>('design')
  const [enabled, setEnabled] = useState(false)
  const [dimension, setDimension] = useState('')
  const [reason, setReason] = useState('')
  const enums = useEnums()
  const { query, submit } = useBlueprintRefinement(sid, origin, enabled)
  const dimensions = enums.data?.plot_review_dimensions
  const catalog = !query.isFetching && !query.isError ? query.data : null
  const ready = catalog?.origin === origin && catalog.subjects_by_chapter[ordinal]?.includes('chapter')
    && !!reason.trim() && !!dimensions?.includes(dimension) && !submit.isPending && !submit.isSuccess
  const error = submit.error ?? query.error ?? enums.error
  return <details className="mx-30 mb-18 rounded-block border border-line-2 p-14">
    <summary className="font-semibold">蓝图疑点核查 · 第 {ordinal} 章</summary>
    <p className="prose-cn text-body text-ink-3">
      描述疑似原文错漏、归因错误或必要揭示边界缺失。普通换场与分段表现继续在设计审阅中处理。
      提交只写入待核查问题；章级审阅确认后，才决定是否修订蓝图。
    </p>
    <fieldset disabled={submit.isPending || submit.isSuccess} className="flex flex-col gap-10 border-0 p-0">
      <label>发现疑点的审阅阶段
        <select aria-label="疑点审阅阶段" value={origin} onChange={(event) => setOrigin(event.target.value as typeof origin)}>
          <option value="design">A 设计审阅</option><option value="structure">B 结构审阅</option>
        </select>
      </label>
      <button type="button" onClick={() => { setEnabled(true); if (enabled) void query.refetch() }}>
        {query.isFetching ? '正在读取核查入口…' : '读取当前核查入口'}
      </button>
      {catalog ? <>
        <p className="text-tag text-ink-4">{origin === 'design'
          ? '设计审阅使用规划盲视图，疑点需要回到原文核实。' : '请说明原文与当前蓝图的具体差异。'}</p>
        <label>疑点维度<select aria-label="疑点维度" value={dimension} onChange={(event) => setDimension(event.target.value)}>
          <option value="">请选择</option>
          {dimensions?.map((value) => <option key={value} value={value}>{labels[value] ?? value}</option>)}
        </select></label>
        <label>具体疑点与原文线索<textarea aria-label="蓝图疑点" rows={4} className="prose-cn w-full"
          maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" disabled={!ready} onClick={() => {
          if (ready && catalog) submit.mutate({
            origin: catalog.origin, review_token: catalog.review_token, actor: 'human-ui',
            chapter_ordinal: ordinal, dimension: dimension as BlueprintRefinementRequest['dimension'],
            subject_refs: ['chapter'], reason: reason.trim(),
          })
        }}>提交疑点（不调用模型）</button>
      </> : null}
    </fieldset>
    {submit.isSuccess ? <p role="status">
      疑点已登记，请在<Link to={'/n/' + sid + '/read/' + ordinal}>本章蓝图审阅</Link>中对照原文核查。
    </p> : error ? <p role="alert">{errorBody(error)}</p> : null}
  </details>
}
