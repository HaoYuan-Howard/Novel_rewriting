import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type {
  AgentBatchRequest,
  AgentProfileSettingsUpdate,
  AgentRunRecord,
  AgentRunRequest,
  NetworkSettingsUpdate,
  NetworkSettingsView,
} from '@/types/agent'
import { useSid } from './useRouteParams'
import { agentReadIdentity, useBookReadModelsRefresh } from './useBookReadModelsRefresh'

/**
 * capability 含这台机器此刻的 CLI/登录探针结果。HTTP 是 Cache-Control: no-store，
 * React Query 这一层也不把旧结果当新结果复用：每次挂载都重取，卸载即回收。
 */
export function useAgentCapabilities() {
  return useQuery({
    queryKey: qk.agentCapabilities(),
    queryFn: () => api.getAgentCapabilities(),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
  })
}

/** 用户常用配置是服务端事实；不放 localStorage，也不与产品默认混成一格。 */
export function useAgentProfileSettings(enabled = true) {
  return useQuery({
    enabled,
    queryKey: qk.agentProfileSettings(),
    queryFn: () => api.getAgentProfileSettings(),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
  })
}

export function useNetworkSettings(enabled = true) {
  return useQuery<NetworkSettingsView>({
    enabled,
    queryKey: qk.networkSettings(),
    queryFn: () => api.getNetworkSettings(),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
  })
}

export function useSaveNetworkSettings() {
  const qc = useQueryClient()
  return useMutation<NetworkSettingsView, unknown, NetworkSettingsUpdate>({
    mutationFn: (req) => api.saveNetworkSettings(req),
    onSuccess: (next) => {
      qc.setQueryData(qk.networkSettings(), next)
    },
  })
}

export function useAgentRunContinuation(runId?: string) {
  return useQuery({
    queryKey: qk.agentRunContinuation(runId ?? ''),
    queryFn: () => api.getAgentRunContinuation(runId!),
    enabled: !!runId,
    staleTime: 0,
    refetchOnMount: 'always',
  })
}

export function useSaveAgentProfileSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: AgentProfileSettingsUpdate) => api.saveAgentProfileSettings(req),
    onSuccess: (next) => {
      qc.setQueryData(qk.agentProfileSettings(), next)
    },
  })
}

/**
 * agent 代跑记录（新的在前）。有在跑的 2.5s 一轮，稳定停点不轮询。
 *
 * `observeControlTransition` 用于 202 异步控制的快速交接窗：resume 响应可能仍是
 * paused，不能让那一次竞态读把轮询永久关掉。即使页面在交接中刷新而丢失本地
 * 标记，paused 也保留低频复核，最终仍会收敛到后端真实状态。
 */
export function useAgentRuns(
  sidOverride?: string,
  observeControlTransition = false,
) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid

  const query = useQuery({
    queryKey: qk.agentRuns(sid),
    queryFn: () => api.getAgentRuns(sid),
    enabled: !!sid,
    refetchOnMount: 'always',
    refetchInterval: (q) => {
      const runs = q.state.data?.runs ?? []
      const active = runs.some(
        (run) => run.state === 'pending' || run.state === 'running',
      )
      if (active || observeControlTransition) return 2500
      return runs.some((run) => run.state === 'paused') ? 5000 : false
    },
  })
  useBookReadModelsRefresh(
    sid, 'agentRuns', query.isSuccess ? agentReadIdentity(query.data) : null,
    query.isFetchedAfterMount, query.isFetching,
  )
  return query
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
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
      void qc.invalidateQueries({ queryKey: qk.agentQueue() })
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
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
      void qc.invalidateQueries({ queryKey: qk.agentQueue() })
    },
  })
}

/** 协作式暂停：不取消已取得 claim 的付费节点。 */
export function usePauseAgentRun(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => api.pauseAgentRun(runId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
      void qc.invalidateQueries({ queryKey: qk.agentQueue() })
    },
  })
}

/** 恢复同一 parent run；同 epoch 由后端精确续 provider session。 */
export function useResumeAgentRun(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => api.resumeAgentRun(runId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
      void qc.invalidateQueries({ queryKey: qk.agentQueue() })
    },
  })
}

/**
 * 并发全景：谁在跑、谁在排、闸开多大。
 *
 * ⚠ 有东西在跑或在排就 2.5s 一轮，都空着就不轮询 —— 与 useJobs / useAgentRuns 同款纪律。
 * ⚠ 后端 `queue_view` 与 drain 共用线性化锁：GET 不会在 pending 已 pop、
 *   running 尚未登记的中间缝返回假空。前端只按服务端快照决定轮询。
 */
export function useAgentQueue() {
  return useQuery({
    queryKey: qk.agentQueue(),
    queryFn: () => api.getAgentQueue(),
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d) return false
      return d.running.length > 0 || d.pending.length > 0 ? 2500 : false
    },
  })
}

/** 批量开跑。受理即返回全景（202），不等任何一本跑完。 */
export function useStartAgentBatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: AgentBatchRequest) => api.startAgentBatch(req),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.agentQueue() })
    },
  })
}
