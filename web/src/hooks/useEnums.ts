import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'

/**
 * 非核心枚举一律运行时从服务端取，不硬编码进前端。
 * 后端改了枚举，前端不该跟着改代码。
 */
export function useEnums() {
  return useQuery({
    queryKey: qk.enums(),
    queryFn: () => api.getEnums(),
    staleTime: Infinity,
  })
}
