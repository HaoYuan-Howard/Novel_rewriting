import { Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody, isNoSample } from '@/api/errors'
import { useSid } from '@/hooks/useRouteParams'
import { useApprovals } from '@/hooks/useApprovals'
import { Mono } from '@/components/ui/Mono'

const REQUIRED_GATE_BY_STAGE = {
  stage1: 's0',
  stage2: 's1',
  stage3: 's1',
  stage4: 's2',
  stage5: 's3',
  stage6: 's5',
  stage7: 's6',
} as const

/**
 * 书级外壳：顶部一条书名 + sid。
 * `key={sid}` 加在路由那一层 —— 换书就整棵重挂，屏内的选中/开关不会跟过去。
 */
export function BookLayout() {
  const sid = useSid()
  const nav = useNavigate()
  const { doc, backfill } = useApprovals()
  const q = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    enabled: !!sid,
    retry: false,
  })

  // 详情取不到时不猜书名：列表里那个名字属于列表，不属于这一屏
  const title = q.data?.title ?? null
  const chapters = q.data?.chapters ?? null
  const approvalGaps = Object.entries(q.data?.stage_done ?? {})
    .filter(([stage, done]) => {
      const required = REQUIRED_GATE_BY_STAGE[stage as keyof typeof REQUIRED_GATE_BY_STAGE]
      return done === true && required && doc && !doc.gate[required]
    })
    .map(([stage]) => stage)

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
      {approvalGaps.length ? (
        <div className="flex items-center gap-10 border-b border-warn-line bg-warn-bg px-30 py-10 text-body text-warn-ink">
          <span aria-hidden>!</span>
          <span className="min-w-0 flex-1">
            盘上已有 {approvalGaps.join('、')} 产物，但这本书缺少对应的前端人工验收记录。
            可以只读审阅，也可以显式按盘面补录验收链；补录会标明来源，不伪装成逐屏点击。
          </span>
          <button
            type="button"
            disabled={backfill.isPending}
            onClick={() => backfill.mutate()}
            className="cursor-pointer rounded-btn border border-warn-line bg-card px-12 py-6 text-body font-semibold disabled:cursor-not-allowed disabled:opacity-40"
          >
            {backfill.isPending ? '核对盘面中…' : '按盘面补录验收链'}
          </button>
        </div>
      ) : null}
      {backfill.data?.backfilled.length ? (
        <div className="border-b border-line-soft bg-gate-ok-bg px-30 py-9 text-body text-ink-2">
          已按正式产物补录 {backfill.data.backfilled.join('、')}；这些闸门来自盘面核对，不是你逐屏点击的记录。
        </div>
      ) : null}
      {backfill.isError ? (
        <div className="border-b border-warn-line bg-warn-bg px-30 py-9 text-body text-warn-ink">
          补录失败：{errorBody(backfill.error)}
        </div>
      ) : null}
      <Outlet />
    </div>
  )
}
