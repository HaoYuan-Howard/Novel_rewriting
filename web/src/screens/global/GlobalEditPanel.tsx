import { useState } from 'react'
import { Panel } from '@/components/ui/Panel'
import {
  useApplyGlobalEdit,
  useGlobalEditTemplate,
  usePreviewGlobalEdit,
} from '@/hooks/useGlobalEdit'
import type { GlobalEdit } from '@/types/globalEdit'

/**
 * Stage2 全局事实免费订正：载入模板 → 修改 JSON → 独立预检 → 应用。
 * 预检与 apply 共用同一后端判据，预览不是预授权；语义未审随交付披露。
 */
export function GlobalEditPanel({ sid }: { sid: string }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const q = useGlobalEditTemplate(sid, open)
  const preview = usePreviewGlobalEdit(sid)
  const apply = useApplyGlobalEdit(sid)

  function parsed(): GlobalEdit | null {
    try {
      const value = JSON.parse(draft) as GlobalEdit
      setParseError(null)
      return value
    } catch {
      setParseError('全局订正内容不是有效 JSON，请检查后再提交。')
      return null
    }
  }

  return (
    <Panel className="flex flex-col gap-12 px-18 py-14">
      <button type="button" aria-expanded={open} className="text-left text-body-lg font-semibold"
        onClick={() => setOpen(!open)}>全局事实免费订正（独立预检 + 应用）</button>
      {open ? (
        <div className="flex flex-col gap-12">
          <p className="m-0 text-body text-ink-4">
            只改背景、世界规则、长期关系和跨章秘密四类长期约束；人物登记走人物面板的独立入口。
            预检只读、不落盘；应用通过落盘闸门后，下游按实际依赖与机械准入判定，语义未审随交付披露。
          </p>
          {q.isLoading ? <p role="status">正在读取订正模板…</p> : null}
          {q.error ? <p role="alert">{q.error.message}</p> : null}
          {q.data ? (
            <button type="button" onClick={() => setDraft(JSON.stringify(q.data, null, 2))}
              className="self-start rounded-btn border border-line-btn px-12 py-6 text-body">载入当前订正模板</button>
          ) : null}
          <label className="text-body">全局订正 JSON
            <textarea aria-label="全局订正 JSON" value={draft} rows={14}
              onChange={(event) => setDraft(event.target.value)}
              className="mt-6 w-full rounded-btn border border-line-btn bg-card p-10 font-mono text-body" />
          </label>
          <p className="m-0 text-mini text-ink-4">
            稀疏 patch 以字段是否出现为准；证据可复用现有引用或填章号与原文短语，来源 SHA 保持模板原值。
          </p>
          {parseError ? <p role="alert">{parseError}</p> : null}
          <div className="flex flex-wrap gap-10">
            <button type="button" disabled={!draft.trim() || preview.isPending}
              onClick={() => { const value = parsed(); if (value) preview.mutate(value) }}
              className="rounded-btn border border-line-btn px-12 py-6 text-body">预检（0 模型、零写入）</button>
            <button type="button" disabled={!draft.trim() || apply.isPending}
              onClick={() => { const value = parsed(); if (value) apply.mutate(value) }}
              className="rounded-btn border border-line-btn px-12 py-6 text-body">应用订正（0 模型）</button>
          </div>
          {preview.error ? <p role="alert">{preview.error.message}</p> : null}
          {preview.data ? (
            <div role="status" className="flex flex-col gap-4 rounded-btn border border-line-soft px-14 py-10">
              <span className="text-tag text-ink-4">预检结果（不是预授权）</span>
              <span className="text-mini text-ink-4">候选摘要：{preview.data.result_subject_sha256}</span>
              <span className="text-mini text-ink-4">改动：{preview.data.changed.length ? preview.data.changed.join('、') : '无'}</span>
              <span className={preview.data.hard_hits.length ? 'text-body text-warn-ink' : 'text-mini text-ink-4'}>
                {preview.data.hard_hits.length
                  ? `落盘闸门将拦截：${preview.data.hard_hits.join('、')}`
                  : '机械检查无阻断；应用时仍以现场重判为准'}
              </span>
            </div>
          ) : null}
          {apply.error ? <p role="alert">{apply.error.message}</p> : null}
          {apply.data ? <p role="status">订正作业状态：{apply.data.state}。实际结果以后端重读为准。</p> : null}
        </div>
      ) : null}
    </Panel>
  )
}
