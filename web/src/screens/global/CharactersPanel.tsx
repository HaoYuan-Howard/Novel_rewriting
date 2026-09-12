import { useState } from 'react'
import { Panel } from '@/components/ui/Panel'
import { useCharacters, useEditCharacters } from '@/hooks/useCharacters'
import type { CharactersEdit } from '@/types/characters'
import { CoreEvidence, SharedAnalysisSource } from './CoreEvidence'

export function CharactersPanel({ sid }: { sid: string }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const q = useCharacters(sid, open)
  const edit = useEditCharacters(sid)

  function submit() {
    try {
      const payload = JSON.parse(draft) as CharactersEdit
      setParseError(null)
      edit.mutate({ actor: actor.trim(), reason: reason.trim(), edit: payload })
    } catch {
      setParseError('人物订正内容不是有效 JSON，请检查后再提交。')
    }
  }

  return (
    <Panel className="flex flex-col gap-12 px-18 py-14">
      <button type="button" aria-expanded={open} className="text-left text-body-lg font-semibold"
        onClick={() => setOpen(!open)}>独立人物登记与免费订正</button>
      {open ? (
        <div className="flex flex-col gap-12">
          <p className="m-0 text-body text-ink-4">登记实体身份、唯一称谓及读者获知其归属的章。称谓本身可以更早出现；登记名不代表写作许可。提交订正后仍需统一复核。</p>
          {q.isLoading ? <p role="status">正在读取人物登记…</p> : null}
          {q.error ? <p role="alert">{q.error.message}</p> : null}
          {q.data ? (
            <>
              <p className="m-0 text-body">当前来源：{q.data.artifact.invalid ? '问题稿' : '正式稿'} · {q.data.artifact.invalid ? q.data.artifact.invalid_freshness : q.data.artifact.freshness}</p>
              <SharedAnalysisSource provenance={q.data.registry._provenance} />
              <div className="grid gap-12 lg:grid-cols-2">
                {Object.entries(q.data.registry.characters).map(([id, row]) => (
                  <div key={id} className="flex flex-col gap-7 rounded-btn border border-line-soft px-14 py-12">
                    <div className="flex flex-wrap items-center gap-8">
                      <span className="text-h3 font-semibold">{row.canonical}</span>
                      <span className="font-mono text-mini text-ink-5">{id}</span>
                      <span className="ml-auto text-mini text-ink-4">辨识度 {row.name_distinctiveness}</span>
                    </div>
                    <span className="prose-cn text-body">{row.role}</span>
                    <CoreEvidence refs={row.role_evidence_refs} anchors={q.data!.registry.evidence_anchors} snippets={q.data!.evidence_snippets} />
                    <span className="text-mini text-ink-4">实体首次出场：第 {row.first_chapter} 章</span>
                    <CoreEvidence refs={row.first_appearance_evidence_refs} anchors={q.data!.registry.evidence_anchors} snippets={q.data!.evidence_snippets} />
                    <div className="flex flex-col gap-6 border-t border-line-soft pt-6">
                      {row.aliases.map((alias) => <div key={alias.surface} className="flex flex-col gap-3">
                        <span className="text-body">{alias.surface}{alias.surface === row.canonical ? '（规范称名）' : ''}
                          {' · '}第 {alias.identity_revealed_from} 章起可绑定此实体</span>
                        <CoreEvidence refs={alias.evidence_refs} anchors={q.data!.registry.evidence_anchors} snippets={q.data!.evidence_snippets} />
                      </div>)}
                    </div>
                  </div>
                ))}
              </div>
              {q.data.template_unavailable_reason ? <p role="status">{q.data.template_unavailable_reason}</p> : null}
              <button type="button" disabled={!q.data.edit_template || edit.isPending}
                onClick={() => setDraft(JSON.stringify(q.data!.edit_template, null, 2))}
                className="self-start rounded-btn border border-line-btn px-12 py-6 text-body">载入当前订正模板</button>
            </>
          ) : null}
          <label className="text-body">人物订正 JSON
            <textarea aria-label="人物订正 JSON" value={draft} rows={14}
              onChange={(event) => setDraft(event.target.value)}
              className="mt-6 w-full rounded-btn border border-line-btn bg-card p-10 font-mono text-body" />
          </label>
          <p className="m-0 text-mini text-ink-4">只保留要修改的操作；证据可复用现有引用，或填写章号与原文短语。来源 SHA 保持模板原值，提交时由后端核验。</p>
          <label className="text-body">订正人 <input aria-label="人物订正人" value={actor} onChange={(event) => setActor(event.target.value)} className="border border-line-btn px-10 py-6" /></label>
          <label className="text-body">订正依据 <input aria-label="人物订正依据" value={reason} onChange={(event) => setReason(event.target.value)} className="w-full border border-line-btn px-10 py-6" /></label>
          {parseError ? <p role="alert">{parseError}</p> : null}
          {edit.error ? <p role="alert">{edit.error.message}</p> : null}
          {edit.data ? <p role="status">订正作业状态：{edit.data.state}。请结合当前产物与复核结果查看。</p> : null}
          <button type="button" onClick={submit} disabled={!q.data || !draft.trim() || !actor.trim() || !reason.trim() || edit.isPending}
            className="self-start rounded-btn border border-line-btn px-12 py-6 text-body">提交人物订正（0 模型）</button>
        </div>
      ) : null}
    </Panel>
  )
}
