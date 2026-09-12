import { useState } from 'react'
import { Panel } from '@/components/ui/Panel'
import { ActionButton } from '@/components/ui/ActionButton'
import { ErrorHint } from '@/components/state/States'
import { errorBody } from '@/api/errors'
import { useAnalyzeDraft, useEditAnalyzeDraft } from '@/hooks/useAnalyzeDraft'
import { useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type { AnalyzeDraftOwner, AnalyzeOwnerDraftEdit } from '@/types/analyzeDraft'

function DraftOwnerEditor({ sid, owner }: { sid: string; owner: AnalyzeDraftOwner }) {
  const source = useAnalyzeDraft(sid, owner)
  const edit = useEditAnalyzeDraft(sid)
  const enums = useEnums()
  const { askConfirm, openDrawer } = useChrome()
  const [draft, setDraft] = useState('')
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const prepared = source.data?.source_state === 'prepared'
  const recovery = enums.data?.actions.find((action) => action.kind === owner
    && action.extra?.length === 1 && action.extra[0] === '--recover-analyze-draft')

  function submit() {
    try {
      const value = JSON.parse(draft) as AnalyzeOwnerDraftEdit
      if (value.owner !== owner) throw new Error('订正 owner 必须与当前草稿一致。')
      setParseError(null)
      edit.mutate({ actor: actor.trim(), reason: reason.trim(), edit: value },
        { onSettled: () => openDrawer() })
    } catch (error) {
      setParseError(error instanceof SyntaxError ? '草稿订正不是有效 JSON，请检查后再提交。'
        : error instanceof Error ? error.message : '草稿订正无法读取。')
    }
  }

  return <div className="flex flex-col gap-12">
    {source.isPending ? <p role="status">正在读取已购候选…</p> : null}
    {source.error ? <div role="alert">{errorBody(source.error)}<ErrorHint error={source.error} /></div> : null}
    {source.data ? <>
      <p className="m-0 text-body">草稿状态：{source.data.source_state}。候选尚未获统一审阅接受。</p>
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
      {prepared ? <>
        <p>已保存冻结的修订，只能补完提交；修订内容保留在原记录中。</p>
        <details><summary>已保存的草稿修订</summary><pre className="max-h-320 overflow-auto whitespace-pre-wrap break-all text-mini">{JSON.stringify(source.data.template, null, 2)}</pre></details>
        <ActionButton action={recovery ?? null} disabled={!recovery}
          disabledReason="当前动作登记尚未读取，无法启动恢复。"
          onClick={() => recovery && askConfirm({ req: { ...recovery, sid } })}>免费补完草稿提交</ActionButton>
      </> : <>
        <button type="button" className="self-start rounded-btn border border-line-btn px-12 py-6 text-body"
          onClick={() => setDraft(JSON.stringify(source.data!.template, null, 2))}>载入原草稿订正模板</button>
        <label className="text-body">草稿订正 JSON<textarea aria-label="草稿订正 JSON" rows={16} value={draft}
          onChange={(event) => setDraft(event.target.value)} className="mt-6 w-full rounded-btn border border-line-btn bg-card p-10 font-mono text-body" /></label>
        <p className="m-0 text-mini text-ink-4">只替换当前所属草稿，保留三项来源 SHA。证据引用使用上方候选 ID；人物持久 ID 由程序分配。</p>
        <label className="text-body">订正人 <input aria-label="草稿订正人" value={actor} onChange={(event) => setActor(event.target.value)} className="border border-line-btn px-10 py-6" /></label>
        <label className="text-body">订正依据 <input aria-label="草稿订正依据" value={reason} onChange={(event) => setReason(event.target.value)} className="w-full border border-line-btn px-10 py-6" /></label>
        <button type="button" className="self-start rounded-btn border border-line-btn px-12 py-6 text-body"
          disabled={!draft.trim() || !actor.trim() || !reason.trim() || edit.isPending}
          onClick={submit}>提交草稿订正（0 模型）</button>
      </>}
    </> : null}
    {parseError ? <p role="alert">{parseError}</p> : null}
    {edit.error ? <div role="alert">{errorBody(edit.error)}<ErrorHint error={edit.error} /></div> : null}
    {edit.data ? <p role="status">草稿订正作业：{edit.data.state}。请查看当前产物与待审结果。</p> : null}
  </div>
}

export function AnalyzeDraftPanel({ sid }: { sid: string }) {
  const [open, setOpen] = useState(false)
  const [owner, setOwner] = useState<AnalyzeDraftOwner>('stage2.characters')
  return <Panel className="flex flex-col gap-12 px-18 py-14">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      className="text-left text-body-lg font-semibold">已购联合草稿与免费修复</button>
    {open ? <>
      <p className="m-0 text-body text-ink-4">联合回答已保存、但尚不能编译时，在此订正问题草稿。已有正式稿或问题稿请使用该产物的独立订正。</p>
      <label className="text-body">草稿归属 <select aria-label="草稿归属" value={owner}
        onChange={(event) => setOwner(event.target.value as AnalyzeDraftOwner)} className="border border-line-btn px-10 py-6">
        <option value="stage2.characters">人物</option><option value="stage2.global">全局知识</option>
      </select></label>
      <DraftOwnerEditor key={owner} sid={sid} owner={owner} />
    </> : null}
  </Panel>
}
