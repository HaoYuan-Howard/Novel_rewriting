import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import { qkStr06 } from '@/api/queryKeys'
import type { Str06ChapterView } from '@/types/str06'

/**
 * STR-06 候选链监控屏的全部只读查询（F7d）。
 *
 * 一个候选根一套 key（`qkStr06`）：换候选根换缓存，不存在 A 根的数据漏到 B 根。
 * 章查询带 `enabled`：没有 IR（还没跑到 stage4）时不发章请求——
 * 「取不到」与「没跑」在前端也分成两条路。
 */
export function useStr06Monitor(candidate: string) {
  const candidates = useQuery({
    queryKey: qkStr06.candidates(),
    queryFn: () => api.getStr06Candidates(),
    retry: false,
  })
  const run = useQuery({
    queryKey: qkStr06.run(candidate),
    queryFn: () => api.getStr06Run(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const characters = useQuery({
    queryKey: qkStr06.characters(candidate),
    queryFn: () => api.getStr06Characters(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const blueprint = useQuery({
    queryKey: qkStr06.blueprint(candidate),
    queryFn: () => api.getStr06Blueprint(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const design = useQuery({
    queryKey: qkStr06.design(candidate),
    queryFn: () => api.getStr06Design(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const ir = useQuery({
    queryKey: qkStr06.ir(candidate),
    queryFn: () => api.getStr06Ir(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const hook = useQuery({
    queryKey: qkStr06.hook(candidate),
    queryFn: () => api.getStr06Hook(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const naming = useQuery({
    queryKey: qkStr06.naming(candidate),
    queryFn: () => api.getStr06Naming(candidate),
    enabled: !!candidate,
    retry: false,
  })
  const delivery = useQuery({
    queryKey: qkStr06.delivery(candidate),
    queryFn: () => api.getStr06Delivery(candidate),
    enabled: !!candidate,
    retry: false,
  })

  // 选中章：IR 到齐后默认第 1 章；IR 变化（重跑）时不把旧选择带到新书状态。
  const [selected, setSelected] = useState<number | null>(null)
  const ordinals = ir.data?.chapters.map((c) => c.chapter_ordinal) ?? []
  useEffect(() => {
    if (ordinals.length && (selected === null || !ordinals.includes(selected))) {
      setSelected(ordinals[0])
    }
  }, [ordinals.join(','), selected])

  const chapter = useQuery({
    queryKey: qkStr06.chapter(candidate, selected ?? 0),
    queryFn: () => api.getStr06Chapter(candidate, selected!),
    enabled: !!candidate && selected !== null,
    retry: false,
  })

  const openBook = () => {
    if (!candidate) return
    void api.getStr06Book(candidate).then((text) => {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'str06_candidate_book.txt'
      anchor.click()
      URL.revokeObjectURL(url)
    })
  }

  return {
    candidates, run, characters, blueprint, design, ir, hook, naming, delivery,
    chapter: chapter as typeof chapter & { data?: Str06ChapterView },
    selected, select: setSelected, openBook,
  }
}
