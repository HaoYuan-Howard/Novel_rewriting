import { useQuery } from '@tanstack/react-query'
import { Outlet, useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { isNoSample } from '@/api/errors'
import { useSid } from '@/hooks/useRouteParams'
import { useAgentRuns } from '@/hooks/useAgentRuns'
import { useRunProgress } from '@/hooks/useRunProgress'
import { Mono } from '@/components/ui/Mono'

/**
 * 书级外壳：顶部一条书名 + sid。
 * `key={sid}` 加在路由那一层 —— 换书就整棵重挂，屏内的选中/开关不会跟过去。
 *
 * ⚠ STR-06 切换（F8c）：人工闸整族退役后这里不再挂审批投影，也没有
 * 「最终缺口」提示条 —— 交付结论归 agent 代跑记录与 `/str06/:candidate` 的
 * delivery pane，本外壳只呈现书与章数这两个盘面事实。
 */
export function BookLayout() {
  const sid = useSid()
  const nav = useNavigate()
  useRunProgress(sid)
  useAgentRuns(sid)
  const q = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    enabled: !!sid,
    retry: false,
  })

  const title = q.data?.title ?? null
  const chapters = q.data?.chapters ?? null

  return (
    <div className="flex min-w-0 flex-col">
      <header className="flex items-center gap-12 border-b border-line bg-sunken-3 px-30 py-12">
        <button
          type="button"
          onClick={() => nav('/')}
          className="cursor-pointer rounded-btn border border-line-btn bg-card px-10 py-4 text-tag text-ink-3"
        >
          ← 语料库
        </button>
        <span className="text-body-lg font-semibold">
          {title ?? (q.isPending ? '取书名中 …' : isNoSample(q.error) ? '（这本书的详情没有在样本里）' : '（取不到书名）')}
        </span>
        <Mono className="text-mini text-ink-4">
          {sid} · {chapters === null ? '章数待分章' : chapters + ' 章'}
        </Mono>
      </header>
      <Outlet />
    </div>
  )
}
