import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type {
  ApprovalDoc,
  GateKey,
  SeenKey,
  Stage5ClosureRequest,
  StructureApprovalRequest,
} from '@/types/approvals'
import type { BlueprintSeenRequest } from '@/types/stages'
import { useSid } from './useRouteParams'

/**
 * 人工验收记录。走 Query 而不是 Context+useReducer：
 * 它在真产品里是服务端的记录，接后端时只改 mock.ts 一个文件，
 * 屏与组件改动 0 行。
 */
export function useApprovals(sidOverride?: string) {
  const routeSid = useSid()
  const sid = sidOverride ?? routeSid
  const qc = useQueryClient()

  const q = useQuery({
    queryKey: qk.approvals(sid),
    queryFn: () => api.getApprovals(sid),
    enabled: !!sid,
    staleTime: Infinity,
  })

  const cache = (doc: ApprovalDoc) => {
    qc.setQueryData(qk.approvals(sid), doc)
  }

  const put = (doc: ApprovalDoc) => {
    cache(doc)
    // 确认会发起下游作业 —— 让抽屉立刻看得见
    void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
  }

  const approve = useMutation({
    mutationFn: (key: GateKey) => api.approveGate(sid, key),
    onSuccess: put,
  })
  const backfill = useMutation({
    mutationFn: () => api.backfillApprovals(sid),
    onSuccess: (result) => put(result.approvals),
  })
  const approveStructure = useMutation({
    mutationFn: (request: StructureApprovalRequest) => api.approveStructure(sid, request),
    onSuccess: (doc) => {
      // 专用结构裁决只写 `_service/approvals`，明确不触发作业。
      cache(doc)
      void qc.invalidateQueries({ queryKey: qk.approvalWorkpack(sid, 'structure') })
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
    },
  })
  const approveStage5Closure = useMutation({
    mutationFn: (request: Stage5ClosureRequest) => api.approveStage5Closure(sid, request),
    onSuccess: (doc) => {
      // 回执只写审批账；成功后 Stage5/下游与 inbox 必须重读服务端当前结论。
      cache(doc)
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
      void qc.invalidateQueries({ queryKey: qk.stage6(sid) })
      void qc.invalidateQueries({ queryKey: qk.stage7(sid) })
    },
  })
  const markSeen = useMutation({
    mutationFn: (key: SeenKey) => api.markSeen(sid, key),
    onSuccess: put,
  })
  const markBlueprintSeen = useMutation({
    mutationFn: (request: BlueprintSeenRequest) => api.markSeen(sid, 'read', request),
    onSuccess: put,
  })
  const startWriting = useMutation({
    mutationFn: () => api.startWriting(sid),
    onSuccess: put,
  })

  return {
    sid,
    doc: q.data ?? null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
    approve,
    approveStructure,
    approveStage5Closure,
    backfill,
    markSeen,
    markBlueprintSeen,
    startWriting,
  }
}
