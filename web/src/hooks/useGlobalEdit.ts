import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { GlobalEdit } from '@/types/globalEdit'
import { invalidateBookReadModels } from './bookReadModels'

/** Stage2 全局事实 typed 编辑；示例模式无当前全局底座，模板查询按需启用。 */
export function useGlobalEditTemplate(sid: string, enabled: boolean) {
  return useQuery({
    queryKey: ['stage2', sid, 'global-edit-template'],
    queryFn: () => api.getGlobalEditTemplate(sid),
    enabled,
    retry: false,
  })
}

/** 只读预检：与 apply 共用同一判据，但预览不是预授权。 */
export function usePreviewGlobalEdit(sid: string) {
  return useMutation({
    mutationFn: (request: GlobalEdit) => api.previewGlobalEdit(sid, request),
  })
}

export function useApplyGlobalEdit(sid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (request: GlobalEdit) => api.applyGlobalEdit(sid, request),
    // 失败作业也可能已落 INVALID；实际结果只从后端重读，不能按 HTTP 200 猜通过。
    onSettled: async () => {
      await invalidateBookReadModels(qc, sid)
      await qc.invalidateQueries({ queryKey: qk.run(sid) })
      await qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })
}
