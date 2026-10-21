import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/api/endpoints'
import { qkStr06 } from '@/api/queryKeys'

/** 只读三层监控：运行修订变化时取消旧材料并重新读取，换书隔离缓存和选章。 */
export function useStr06Monitor(candidate: string) {
  const qc = useQueryClient()
  const seen = useRef<{ candidate: string; revision: string } | null>(null)
  const [selection, setSelection] = useState<{ candidate: string; ordinal: number } | null>(null)
  const [downloadError, setDownloadError] = useState<unknown>(null)
  const [downloading, setDownloading] = useState(false)
  const candidates = useQuery({ queryKey: qkStr06.candidates(), queryFn: () => api.getStr06Candidates(), retry: false })
  const run = useQuery({ queryKey: qkStr06.run(candidate), queryFn: () => api.getStr06Run(candidate),
    enabled: !!candidate, retry: false, refetchInterval: 2000 })
  const sourceStory = useQuery({ queryKey: qkStr06.sourceStory(candidate),
    queryFn: () => api.getStr06SourceStory(candidate), enabled: !!candidate, retry: false })
  const targetStory = useQuery({ queryKey: qkStr06.targetStory(candidate),
    queryFn: () => api.getStr06TargetStory(candidate), enabled: !!candidate, retry: false })
  const hook = useQuery({ queryKey: qkStr06.hook(candidate), queryFn: () => api.getStr06Hook(candidate),
    enabled: !!candidate, retry: false })
  const delivery = useQuery({ queryKey: qkStr06.delivery(candidate), queryFn: () => api.getStr06Delivery(candidate),
    enabled: !!candidate, retry: false })

  useEffect(() => {
    if (!run.data || run.isFetching) return
    const revision = run.data.revision
    if (seen.current?.candidate === candidate && seen.current.revision === revision) return
    seen.current = { candidate, revision }
    const filters = { predicate: (query: { queryKey: readonly unknown[] }) =>
      query.queryKey.length >= 3 && query.queryKey[0] === 'str06'
        && query.queryKey[1] === candidate && query.queryKey[2] !== 'run' }
    void (async () => {
      await qc.cancelQueries(filters)
      await qc.invalidateQueries(filters)
    })()
  }, [candidate, qc, run.data, run.isFetching])

  const ordinals = targetStory.data?.status === 'ready'
    ? targetStory.data.data?.chapters.map((chapter) => chapter.chapter_ordinal) ?? [] : []
  const selected = selection?.candidate === candidate && ordinals.includes(selection.ordinal)
    ? selection.ordinal : ordinals[0] ?? null
  const chapter = useQuery({ queryKey: qkStr06.chapter(candidate, selected ?? 0),
    queryFn: () => api.getStr06Chapter(candidate, selected!),
    enabled: !!candidate && selected !== null, retry: false })

  const openBook = async () => {
    setDownloadError(null)
    setDownloading(true)
    try {
      const file = await api.getStr06Book(candidate)
      const url = URL.createObjectURL(new Blob([file.text], { type: 'text/plain;charset=utf-8' }))
      try {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = file.filename
        anchor.click()
      } finally {
        URL.revokeObjectURL(url)
      }
    } catch (error) {
      setDownloadError(error)
    } finally {
      setDownloading(false)
    }
  }

  return { candidates, run, sourceStory, targetStory, chapter, hook, delivery,
    selected, ordinals, select: (ordinal: number) => setSelection({ candidate, ordinal }),
    openBook, downloading, downloadError }
}
