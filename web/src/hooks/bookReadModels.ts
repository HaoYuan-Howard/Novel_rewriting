import { hashKey, type QueryClient, type QueryKey } from '@tanstack/react-query'
import { qk } from '@/api/queryKeys'

// 这里只登记已有书级读模型，不包含运行轮询、日志或动作预览。不能按 key 内任意
// 字符串碰巧等于 sid 匹配，更不能让一次刷新反过来触发自己的观察查询。
// ⚠ STR-06 切换（F8c）：旧链读屏根（reading/characters/global/stage2/stage4/
// stage5/review/inbox/stage7）与审批根（approvals）随屏和端点一起退役；
// 层级复检只剩 stage0/stage1 两个 kind，'check' 根保留。
const BOOK_READ_ROOTS = new Set([
  'novel', 'stage0', 'stage1', 'check',
])

function belongsToBook(queryKey: QueryKey, sid: string): boolean {
  const [root, book] = queryKey
  return (typeof root === 'string' && BOOK_READ_ROOTS.has(root) && book === sid)
    || hashKey(queryKey) === hashKey(qk.novels())
}

interface RefreshOptions {
  /** 写接口已经返回的完整读模型是最新事实，其关联模板仍要失效。 */
  currentKeys?: readonly QueryKey[]
}

/** 运行、作业与审批共用的唯一书级读面失效口；只重取后端，绝不推算 badge/gate。 */
export async function invalidateBookReadModels(
  qc: QueryClient, sid: string, options: RefreshOptions = {},
): Promise<void> {
  if (!sid) return
  const current = new Set((options.currentKeys ?? []).map((key) => hashKey(key)))
  const filters = {
    predicate: (query: { queryKey: QueryKey }) => {
      const key = hashKey(query.queryKey)
      return belongsToBook(query.queryKey, sid) && !current.has(key)
    },
  }
  // 旧请求可能仍在路上；取消其缓存提交，再取当前读数，防止晚回包盖回旧批准。
  await qc.cancelQueries(filters)
  await qc.invalidateQueries(filters)
}
