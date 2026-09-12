import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { invalidateBookReadModels } from '@/hooks/bookReadModels'
import type { PlotEdit, PlotEditPreview } from '@/types/plot.generated'

/** UI 只编辑请求；结构、身份和新鲜度由与 CLI 共用的后端编译器判定。 */
export function BlueprintEditPanel({ sid, ordinal }: { sid: string; ordinal: number }) {
  const qc = useQueryClient()
  const [template, setTemplate] = useState<PlotEdit | null>(null)
  const [actor, setActor] = useState('human-ui')
  const [reason, setReason] = useState('')
  const [draft, setDraft] = useState('')
  const [approved, setApproved] = useState<{ request: PlotEdit; result: PlotEditPreview } | null>(null)
  const load = useMutation({
    mutationFn: () => api.getBlueprintEditTemplate(sid, ordinal),
    onSuccess: (value) => {
      setTemplate(value); setDraft(JSON.stringify(value.chapters, null, 2)); setApproved(null)
    },
  })
  let chapters: PlotEdit['chapters'] | null = null
  let parseError = ''
  if (template) {
    try {
      const value: unknown = JSON.parse(draft)
      if (!Array.isArray(value) || value.length === 0 || value.some((row) =>
        !row?.chapter || !Array.isArray(row.chapter.beats) || row.chapter.beats.some(
          (beat: { beat_key?: unknown; content?: unknown }) =>
            typeof beat.beat_key !== 'string' || typeof beat.content !== 'string'))) {
        throw new Error('章节编辑需包含章节及完整剧情节点')
      }
      chapters = value as PlotEdit['chapters']
    } catch (error) { parseError = error instanceof Error ? error.message : '编辑内容无法读取' }
  }
  const request: PlotEdit | null = template && chapters && actor.trim() && reason.trim()
    ? { ...template, actor: actor.trim(), reason: reason.trim(), chapters } : null
  const preview = useMutation({
    mutationFn: (value: PlotEdit) => api.previewBlueprintEdit(sid, value),
    onSuccess: (result, value) => setApproved({ request: value, result }),
  })
  const apply = useMutation({
    mutationFn: (value: PlotEdit) => api.applyBlueprintEdit(sid, value),
    onSettled: () => {
      void invalidateBookReadModels(qc, sid)
      void qc.invalidateQueries({ queryKey: qk.run(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })
  const busy = load.isPending || preview.isPending || apply.isPending
  const currentPreview = approved && request && JSON.stringify(approved.request) === JSON.stringify(request)
    ? approved : null
  const canApply = !!currentPreview?.result.would_apply && !busy && !apply.isSuccess
  function updateBeat(chapterIndex: number, beatIndex: number, content: string) {
    if (!chapters) return
    const next = structuredClone(chapters)
    next[chapterIndex].chapter.beats[beatIndex].content = content
    setDraft(JSON.stringify(next, null, 2))
  }
  const error = load.error ?? preview.error ?? apply.error
  return <details className="rounded-block border border-line-2 p-14">
    <summary className="font-semibold">修订本章剧情蓝图</summary>
    <p className="text-body text-ink-3">
      用原文订正真实错漏。普通分场在设计页安排；此处修改后需要重新审阅受影响范围。
    </p>
    <button type="button" disabled={busy} onClick={() => { apply.reset(); preview.reset(); load.mutate() }}>
      {load.isPending ? '读取编辑基线…' : template ? '重新读取当前编辑基线' : '读取本章编辑基线'}
    </button>
    {error ? <p role="alert">{errorBody(error)}</p> : null}
    {template ? <fieldset disabled={busy || apply.isSuccess} className="mt-12 flex flex-col gap-12 border-0 p-0">
      <label>编辑人<input aria-label="蓝图编辑人" value={actor} onChange={(e) => setActor(e.target.value)} /></label>
      <label>原文依据与修改理由<input aria-label="蓝图编辑理由" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <p className="text-tag text-ink-4">编辑人是自报身份，用于审计记录。</p>
      {chapters?.map((row, c) => <div key={c} className="flex flex-col gap-10">
        {row.chapter.beats.map((beat, b) => <label key={beat.beat_key} className="flex flex-col gap-4">
          第 {row.chapter.chapter_ordinal} 章 · 剧情节点 {b + 1}
          <textarea aria-label={'编辑剧情节点 ' + (b + 1)} rows={3} className="prose-cn w-full"
            value={beat.content} onChange={(e) => updateBeat(c, b, e.target.value)} />
        </label>)}
      </div>)}
      <details><summary>高级编辑：证据、状态、约束与节点拆合</summary>
        <p className="text-tag">普通订正保留身份；拆合须在 identities 中声明替代关系，正式 ID 由代码分配。</p>
        <textarea aria-label="完整章节编辑数据" rows={16} className="w-full font-mono text-sm2"
          value={draft} onChange={(e) => setDraft(e.target.value)} />
      </details>
      {parseError ? <p role="alert">{parseError}</p> : null}
      <button type="button" disabled={!request || busy}
        onClick={() => { if (request) preview.mutate(request) }}>预检本次修订</button>
      {currentPreview ? <div role="status">
        <p>{currentPreview.result.would_apply ? '结构预检通过，等待写回。' : '结构预检未通过。'}</p>
        <p>影响章节：{currentPreview.result.changed_chapters.join('、')}。结构预检不代表语义接受。</p>
        {currentPreview.result.hard_hits.map((hit) => <p key={hit}>{hit}</p>)}
        {currentPreview.result.tombstones.map((row) => <p key={row.node_id}>
          {row.node_id} → {row.superseded_by.join('、') || '已删除（保留历史）'}
        </p>)}
      </div> : approved ? <p>内容已修改，请重新预检。</p> : null}
      <button type="button" disabled={!canApply}
        onClick={() => { if (currentPreview) apply.mutate(currentPreview.request) }}>写回本次修订（不调用模型）</button>
    </fieldset> : null}
    {apply.data ? <p role="status">
      {apply.data.state === 'ok' ? '修订已写回，请读取当前审阅材料。' : '写回作业状态：' + apply.data.state + '，请查看作业日志。'}
    </p> : null}
  </details>
}
