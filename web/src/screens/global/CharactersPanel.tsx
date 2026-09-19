import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { Panel } from '@/components/ui/Panel'
import { CoreEvidence, SharedAnalysisSource } from './CoreEvidence'

/** 独立人物登记只读面板；人工订正写面已退役。Characters v3：无 role / first_chapter / aliases。 */
export function CharactersPanel({ sid }: { sid: string }) {
  const q = useQuery({
    queryKey: qk.characters(sid),
    queryFn: () => api.getCharacters(sid),
    retry: false,
  })

  return (
    <Panel className="flex flex-col gap-12 px-18 py-14">
      <h2 className="m-0 text-body-lg font-semibold">独立人物登记</h2>
      <p className="m-0 text-body text-ink-4">
        登记实体身份、全书称名及首次出现章。身份描述改由 Blueprint 公开 State 承担；
        本屏只读监控；语义未审随交付披露。
      </p>
      {q.isLoading ? <p role="status">正在读取人物登记…</p> : null}
      {q.error ? <p role="alert">{q.error.message}</p> : null}
      {q.data ? (
        <>
          <p className="m-0 text-body">
            当前来源：{q.data.artifact.invalid ? '问题稿' : '正式稿'} ·{' '}
            {q.data.artifact.invalid ? q.data.artifact.invalid_freshness : q.data.artifact.freshness}
          </p>
          <SharedAnalysisSource provenance={q.data.registry._provenance} />
          <div className="grid gap-12 lg:grid-cols-2">
            {Object.entries(q.data.registry.characters).map(([id, row]) => (
              <div key={id} className="flex flex-col gap-7 rounded-btn border border-line-soft px-14 py-12">
                <div className="flex flex-wrap items-center gap-8">
                  <span className="text-h3 font-semibold">{row.canonical_surface}</span>
                  <span className="font-mono text-mini text-ink-5">{id}</span>
                  <span className="ml-auto text-mini text-ink-4">
                    {row.status} · 辨识度 {row.name_distinctiveness}
                  </span>
                </div>
                <span className="text-mini text-ink-4">
                  首次出现：第 {row.first_mention_chapter} 章
                  {row.name_usable_from != null
                    ? ` · 姓名可用：第 ${row.name_usable_from} 章`
                    : ''}
                </span>
                <div className="flex flex-col gap-6 border-t border-line-soft pt-6">
                  {row.surfaces.map((surface) => (
                    <div key={`${surface.kind}:${surface.surface}`} className="flex flex-col gap-3">
                      <span className="text-body">
                        {surface.surface}
                        {surface.surface === row.canonical_surface ? '（规范称名）' : ''}
                        {' · '}
                        {surface.kind}
                        {surface.identity_revealed_from != null
                          ? ` · 第 ${surface.identity_revealed_from} 章起可绑定此实体`
                          : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {Object.keys(q.data.registry.mentions).length > 0 ? (
            <details className="rounded-btn border border-line-soft px-14 py-12">
              <summary className="cursor-pointer text-body font-semibold">
                出现记录（{Object.keys(q.data.registry.mentions).length}）
              </summary>
              <div className="mt-8 flex flex-col gap-6">
                {Object.values(q.data.registry.mentions).map((mention) => (
                  <div key={mention.mention_id} className="flex flex-col gap-3">
                    <span className="text-body">
                      第 {mention.chapter_ordinal} 章 · {mention.surface} · {mention.kind} ·{' '}
                      {mention.disposition}
                      {mention.char_id ? ` → ${mention.char_id}` : ''}
                    </span>
                    <CoreEvidence
                      refs={mention.evidence_refs}
                      anchors={q.data.registry.evidence_anchors}
                      snippets={q.data.evidence_snippets}
                    />
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </>
      ) : null}
    </Panel>
  )
}
