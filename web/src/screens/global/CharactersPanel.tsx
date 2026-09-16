import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { Panel } from '@/components/ui/Panel'
import { CoreEvidence, SharedAnalysisSource } from './CoreEvidence'

/** 独立人物登记只读面板；人工订正写面已退役。 */
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
        登记实体身份、唯一称谓及读者获知其归属的章。称谓本身可以更早出现；登记名不代表写作许可。
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
                  <span className="text-h3 font-semibold">{row.canonical}</span>
                  <span className="font-mono text-mini text-ink-5">{id}</span>
                  <span className="ml-auto text-mini text-ink-4">辨识度 {row.name_distinctiveness}</span>
                </div>
                <span className="prose-cn text-body">{row.role}</span>
                <CoreEvidence
                  refs={row.role_evidence_refs}
                  anchors={q.data.registry.evidence_anchors}
                  snippets={q.data.evidence_snippets}
                />
                <span className="text-mini text-ink-4">实体首次出场：第 {row.first_chapter} 章</span>
                <CoreEvidence
                  refs={row.first_appearance_evidence_refs}
                  anchors={q.data.registry.evidence_anchors}
                  snippets={q.data.evidence_snippets}
                />
                <div className="flex flex-col gap-6 border-t border-line-soft pt-6">
                  {row.aliases.map((alias) => (
                    <div key={alias.surface} className="flex flex-col gap-3">
                      <span className="text-body">
                        {alias.surface}{alias.surface === row.canonical ? '（规范称名）' : ''}
                        {' · '}第 {alias.identity_revealed_from} 章起可绑定此实体
                      </span>
                      <CoreEvidence
                        refs={alias.evidence_refs}
                        anchors={q.data.registry.evidence_anchors}
                        snippets={q.data.evidence_snippets}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </Panel>
  )
}
