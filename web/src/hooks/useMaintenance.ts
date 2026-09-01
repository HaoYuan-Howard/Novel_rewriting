import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import type { RetentionPlan, RetentionPolicy, TrashRestorePlan } from '@/types/maintenance'

/** 维护屏只经这一组 hooks 进入唯一 API 缝，不接触路径或删除判定。 */
export function useMaintenance() {
  const retentionPreview = useMutation({
    mutationFn: (policy: RetentionPolicy) => api.previewRetention(policy),
  })
  const retentionApply = useMutation({
    mutationFn: (plan: RetentionPlan) => api.applyRetention(plan, true),
  })
  const restorePreview = useMutation({
    mutationFn: (request: { trashId: string; expectedTitle: string }) =>
      api.previewTrashRestore(request.trashId, request.expectedTitle),
  })
  const restoreApply = useMutation({
    mutationFn: (plan: TrashRestorePlan) => api.applyTrashRestore(plan, true),
  })
  return { retentionPreview, retentionApply, restorePreview, restoreApply }
}
