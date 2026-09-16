import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type {
  ApprovalDoc,
  GateKey,
  Stage5ClosureRequest,
  WritingApprovalRequest,
} from '@/types/approvals'
import { useSid } from './useRouteParams'
import { invalidateBookReadModels } from './bookReadModels'

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

  const cache = async (doc: ApprovalDoc) => {
    // 请求在途时路由可能已换书；完整回包只更新它自己的书，不能使用新页面的 sid。
    const bookSid = doc.sid
    await qc.cancelQueries({ queryKey: qk.approvals(bookSid), exact: true })
    qc.setQueryData(qk.approvals(bookSid), doc)
    await invalidateBookReadModels(qc, bookSid, { currentKeys: [qk.approvals(bookSid)] })
  }

  const put = async (doc: ApprovalDoc) => {
    await cache(doc)
    // 确认会发起下游作业 —— 让抽屉立刻看得见
    void qc.invalidateQueries({ queryKey: qk.jobs(doc.sid) })
  }

  const approve = useMutation({
    mutationFn: (key: GateKey) => api.approveGate(sid, key),
    onSuccess: put,
  })
  const backfill = useMutation({
    mutationFn: () => api.backfillApprovals(sid),
    onSuccess: (result) => put(result.approvals),
  })
  const approveStage5Closure = useMutation({
    mutationFn: (request: Stage5ClosureRequest) => api.approveStage5Closure(sid, request),
    onSuccess: (doc) => {
      // 回执只写审批账；成功后 Stage5/下游与 inbox 必须重读服务端当前结论。
      return cache(doc)
    },
  })
  const startWriting = useMutation({
    mutationFn: (request: WritingApprovalRequest) => api.startWriting(sid, request),
    onSuccess: put,
  })

  return {
    sid,
    doc: q.isError ? null : q.data ?? null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
    approve,
    approveStage5Closure,
    backfill,
    startWriting,
  }
}
