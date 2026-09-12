import type { Provenance } from '@/types/common'
import type { CoreEvidenceAnchors, CoreEvidenceSnippets } from '@/types/stages'

/** 证据片段只来自服务端，浏览器不按 Python 字符偏移切正文。 */
export function CoreEvidence({ refs, anchors, snippets }: {
  refs: string[]
  anchors: CoreEvidenceAnchors
  snippets: CoreEvidenceSnippets
}) {
  return <div className="flex flex-col gap-3">
    {refs.map((ref) => {
      const anchor = anchors[ref]
      const snippet = snippets[ref]
      return <span key={ref} className="prose-cn text-mini text-ink-5"
        title={anchor ? `${ref} · 原文码点 ${anchor.start}–${anchor.end}` : ref}>
        {anchor && snippet !== null && snippet !== undefined
          ? `证据 ch${anchor.chapter_ordinal} · “${snippet}”`
          : <span role="status" className="text-warn-ink">证据不可验证{anchor ? ` · 第 ${anchor.chapter_ordinal} 章` : ''} · {ref}</span>}
      </span>
    })}
  </div>
}

export function SharedAnalysisSource({ provenance }: { provenance: Provenance }) {
  const source = provenance.shared_analysis_source
  if (!source) return null
  return <div className="rounded-btn border border-line-soft bg-inset px-12 py-8 text-mini text-ink-3">
    <span className="font-semibold">共享首抽</span> · 人物与全局知识来自同一次模型响应，分别编译校验，统一审阅。
    <details className="mt-4"><summary className="cursor-pointer">查看来源</summary>
      <div className="mt-4 break-all font-mono text-micro">{source.receipt_rel}<br />{source.run_id} · {source.attempt_id}</div>
    </details>
  </div>
}
