import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

/** 原文、节点与进度来自同一范围快照；reading 只作阅读包缺席时的只读显示。 */
export function useReadScreen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [cross, setCross] = useState(false)
  const scope = cross ? 'cross_chapter' : 'chapter:' + ordinal
  const query = useQuery({
    queryKey: qk.reading(sid, ordinal),
    queryFn: () => api.getReading(sid, ordinal), retry: false,
  })
  const book = useQuery({
    queryKey: qk.novel(sid), queryFn: () => api.getNovel(sid),
    enabled: !!sid, retry: false,
  })
  const workpack = useQuery({
    queryKey: qk.blueprintReadingWorkpack(sid, scope),
    queryFn: () => api.getBlueprintReadingWorkpack(sid, scope),
    enabled: !!sid && !!query.data?.blueprint, retry: false,
  })
  const pack = workpack.isSuccess ? workpack.data : null
  const rows = pack ? pack.material.sources.map((source) => ({
    ...source, blueprint: pack.material.blueprints.find(
      (row) => row.chapter_ordinal === source.chapter_ordinal) ?? null,
  })) : query.data ? [{
    chapter_ordinal: ordinal, text: query.data.text, blueprint: query.data.blueprint,
  }] : []
  return {
    sid, ordinal, scope, cross, setCross, query, workpack, pack, rows,
    chapters: book.data?.chapters
      ? Array.from({ length: book.data.chapters }, (_, i) => i + 1) : [ordinal],
  }
}
