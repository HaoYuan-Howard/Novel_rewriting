import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { AgentRunRecord, AgentRunRequest } from '@/types/agent'
import { useSid } from './useRouteParams'

/** agent 代跑记录（新的在前）。有在跑的 2.5s 一轮，都停着不轮询 —— 与 useJobs 同款纪律。 */
export function useAgentRuns(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid

  return useQuery({
    queryKey: qk.agentRuns(sid),
    queryFn: () => api.getAgentRuns(sid),
    enabled: !!sid,
    refetchInterval: (q) =>
      q.state.data?.runs.some((run) => run.state === 'running') ? 2500 : false,
  })
}

/** 一次代跑的日志。照 useJobLog：每轮从头取，running 时 800ms 一轮，停了不再取。 */
export function useAgentRunLog(run: AgentRunRecord | null) {
  return useQuery({
    queryKey: qk.agentRunLog(run?.run_id ?? ''),
    queryFn: () => api.getAgentRunLog(run?.run_id ?? '', 0),
    enabled: !!run,
    refetchInterval: () => (run?.state === 'running' ? 800 : false),
  })
}

export function useStartAgentRun(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: AgentRunRequest) => api.startAgentRun(req),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
    },
  })
}

/** 熄火：杀的是 agent 进程。熄火后的结局归服务端记账（unknown 不改判 ok）。 */
export function useStopAgentRun(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => api.stopAgentRun(runId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
    },
  })
}
