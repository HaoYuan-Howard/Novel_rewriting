import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { RunMetrics } from '@/types/runMetrics'

/** 统计独立轮询，不触发小说产物失效，也不沿用另一 scope 的旧数据。 */
export function useRunMetrics(sid: string, runId?: string) {
  return useQuery({
    queryKey: qk.runMetrics(sid, runId),
    queryFn: () => api.getRunMetrics(sid, runId),
    enabled: !!sid,
    refetchInterval: 10000,
    retry: false,
  })
}

/** 仅在用户展开时读取可证明所属运行的现有日志，不靠时间猜测归属。 */
export function useRunMetricLog(sid: string, run?: RunMetrics['runs'][number]) {
  return useQuery({
    queryKey: qk.runMetricLog(sid, run?.kind ?? '', run?.run_id ?? ''),
    enabled: !!run && (run.kind === 'agent' || run.kind === 'job'),
    queryFn: async () => {
      if (run?.kind === 'agent') return (await api.getAgentRunLog(run.run_id, 0)).text
      if (run?.kind === 'job') return (await api.getJobLog(run.run_id, 0)).lines.map((line) => line.text).join('\n')
      throw new Error('此记录缺少可验证的运行日志归属')
    },
    refetchInterval: run?.state === 'running' ? 2000 : false,
    retry: false,
  })
}
