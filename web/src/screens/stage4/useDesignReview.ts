import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { DesignReviewRequest, DesignReviewTemplateQuery } from '@/types/approvals'
import { useSid } from '@/hooks/useRouteParams'

/** Stage4A 正式审阅的独立数据缝；模板与审批总览均由服务端裁决。 */
export function useDesignReview(query: DesignReviewTemplateQuery | null) {
  const sid = useSid()
  const qc = useQueryClient()
  const template = useQuery({
    queryKey: qk.designReviewTemplate(sid, query),
    queryFn: () => api.getDesignReviewTemplate(sid, query!),
    enabled: !!sid && query !== null,
    retry: false,
  })
  const submit = useMutation({
    mutationFn: (request: DesignReviewRequest) => api.approveDesignReview(sid, request),
    onSuccess: (doc) => {
      qc.setQueryData(qk.approvals(sid), doc)
      void qc.invalidateQueries({ queryKey: ['approvals', sid, 'design', 'template'] })
      void qc.invalidateQueries({ queryKey: qk.approvalWorkpack(sid, 'design') })
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
    },
  })

  return { sid, template, submit }
}
