import { useQuery } from '@tanstack/react-query'
import { Outlet, useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { isNoSample } from '@/api/errors'
import { useSid } from '@/hooks/useRouteParams'
import { useApprovals } from '@/hooks/useApprovals'
import { useAgentRuns } from '@/hooks/useAgentRuns'
import { useRunProgress } from '@/hooks/useRunProgress'
import { Mono } from '@/components/ui/Mono'

const FINAL_APPROVAL_PAGES = [
  { stage: 'stage6', gate: 's6', label: 'Stage6 文案改选' },
  { stage: 'stage7', gate: 's7', label: 'Stage7 人物对照' },
] as const

/**
 * 书级外壳：顶部一条书名 + sid。
 * `key={sid}` 加在路由那一层 —— 换书就整棵重挂，屏内的选中/开关不会跟过去。
 */
export function BookLayout() {
  const sid = useSid()
  const nav = useNavigate()
  useRunProgress(sid)
  useAgentRuns(sid)
  const { doc } = useApprovals()
  const q = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    enabled: !!sid,
    retry: false,
  })

  const title = q.data?.title ?? null
  const chapters = q.data?.chapters ?? null
  // gate 的有效性只认后端；这里仅给已有产物对应的只读页导航。
  const finalApprovalGaps = FINAL_APPROVAL_PAGES.filter(({ stage, gate }) =>
    doc?.gate[gate] === false && (
      q.data?.stage_done[stage] === true
      || (stage === 'stage6' && q.data?.stage_done.stage7 === true)
    ),
  )

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
      {finalApprovalGaps.length ? (
        <div className="flex items-center gap-10 border-b border-warn-line bg-warn-bg px-30 py-10 text-body text-warn-ink">
          <span aria-hidden>!</span>
          <span className="min-w-0 flex-1">
            {finalApprovalGaps.map(({ label }) => label).join('、')}尚未对齐当前选择。
            请到对应页面查看状态；Stage6 可免费改选候选。
          </span>
          {finalApprovalGaps.map(({ stage, label }) => (
            <button
              key={stage}
              type="button"
              onClick={() => nav('/n/' + sid + '/' + stage)}
              className="cursor-pointer rounded-btn border border-warn-line bg-card px-12 py-6 text-body font-semibold"
            >
              查看{label}
            </button>
          ))}
        </div>
      ) : null}
      <Outlet />
    </div>
  )
}
