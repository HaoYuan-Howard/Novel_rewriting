import { useState } from 'react'
import { Panel } from '@/components/ui/Panel'
import { ErrorHint } from '@/components/state/States'
import { errorBody } from '@/api/errors'
import { useAnalyzeDraft } from '@/hooks/useAnalyzeDraft'
import type { AnalyzeDraftOwner } from '@/types/analyzeDraft'

function DraftOwnerView({ sid, owner }: { sid: string; owner: AnalyzeDraftOwner }) {
  const source = useAnalyzeDraft(sid, owner)

  return <div className="flex flex-col gap-12">
    {source.isPending ? <p role="status">正在读取已购候选…</p> : null}
    {source.error ? <div role="alert">{errorBody(source.error)}<ErrorHint error={source.error} /></div> : null}
    {source.data ? <>
      <p className="m-0 text-body">草稿状态：{source.data.source_state}。本屏只读；人工订正写面已退役。</p>
      <details><summary>共享首抽来源</summary>
        <p className="break-all font-mono text-mini">回执 SHA：{source.data.template.source_receipt_sha256}</p>
        <p className="break-all font-mono text-mini">候选 SHA：{source.data.template.source_draft_sha256}</p>
        <p className="break-all font-mono text-mini">映射 SHA：{source.data.template.source_mapping_sha256}</p>
      </details>
      <ul className="m-0 pl-20 text-body">{source.data.diagnostics.map((row, index) => <li key={index}>{row}</li>)}</ul>
      <details><summary>原文证据候选（{source.data.evidence_candidates.length} 段）</summary>
        <div className="max-h-320 overflow-auto">{source.data.evidence_candidates.map((row) =>
          <p key={row.evidence_candidate_id} className="prose-cn text-body">
            <span className="font-mono">{row.evidence_candidate_id}</span> · 第 {row.chapter_ordinal} 章：{row.excerpt}
          </p>)}</div>
      </details>
    </> : null}
  </div>
}

/** 已购联合草稿只读面板。 */
export function AnalyzeDraftPanel({ sid }: { sid: string }) {
  const [open, setOpen] = useState(false)
  const [owner, setOwner] = useState<AnalyzeDraftOwner>('stage2.characters')
  return <Panel className="flex flex-col gap-12 px-18 py-14">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      className="text-left text-body-lg font-semibold">已购联合草稿（只读）</button>
    {open ? <>
      <p className="m-0 text-body text-ink-4">联合回答已保存时在此查看草稿与证据候选；修复由自动/恢复路径处理。</p>
      <label className="text-body">草稿归属 <select aria-label="草稿归属" value={owner}
        onChange={(event) => setOwner(event.target.value as AnalyzeDraftOwner)} className="border border-line-btn px-10 py-6">
        <option value="stage2.characters">人物</option><option value="stage2.global">全局知识</option>
      </select></label>
      <DraftOwnerView key={owner} sid={sid} owner={owner} />
    </> : null}
  </Panel>
}
