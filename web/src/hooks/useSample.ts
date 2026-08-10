import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'

/**
 * 样本覆盖范围。真实模式返回 null（每一章都有数据）。
 * 界面用它决定章号条上哪些章要灰掉、以及跳转时的默认章号 ——
 * 「第 3 章才有」这条知识不出现在任何组件里。
 */
export function useSampleCoverage() {
  const q = useQuery({
    queryKey: ['sampleCoverage'],
    queryFn: () => api.getSampleCoverage(),
    staleTime: Infinity,
  })
  const cov = q.data ?? null
  return {
    /** undefined = 还没问出来；null = 真实后端（每一章都有） */
    coverage: q.isPending ? undefined : cov,
    /** 这一章有没有数据可看。没有覆盖信息时一律当作有。 */
    hasSample: (sid: string, o: number) =>
      !cov ? true : cov.sid === sid && cov.ordinals.includes(o),
    /** 跳到章号屏时的默认章。没有覆盖信息时是第 1 章。 */
    defaultOrdinal: (sid: string) =>
      cov && cov.sid === sid && cov.ordinals.length ? cov.ordinals[0] : 1,
  }
}
