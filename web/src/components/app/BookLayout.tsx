import { useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody, isNoSample } from '@/api/errors'
import { useSid } from '@/hooks/useRouteParams'
import { useApprovals } from '@/hooks/useApprovals'
import { useAgentRuns } from '@/hooks/useAgentRuns'
import { useRunProgress } from '@/hooks/useRunProgress'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'

const REQUIRED_GATE_BY_STAGE = {
  stage1: 's0',
  stage2: 's1',
  stage4: 's2',
  stage5: 's4',
  stage6: 's5',
} as const

const FINAL_APPROVAL_PAGES = [
  { stage: 'stage6', gate: 's6', label: 'Stage6 文案审阅' },
  { stage: 'stage7', gate: 's7', label: 'Stage7 人物审阅' },
] as const

/**
 * 书级外壳：顶部一条书名 + sid。
 * `key={sid}` 加在路由那一层 —— 换书就整棵重挂，屏内的选中/开关不会跟过去。
 */
export function BookLayout() {
  const sid = useSid()
  const nav = useNavigate()
  // 任一 Stage 长驻时都观察自动进展；与运行页共享查询和去重的书级刷新。
  useRunProgress(sid)
  useAgentRuns(sid)
  const { doc, backfill } = useApprovals()
  // 成功条的「知道了」只挡当前这本书的当前这次结果：换书、或又补录了一次，都要重新出现
  const [dismissed, setDismissed] = useState<{ sid: string; data: unknown } | null>(null)
  const q = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    enabled: !!sid,
    retry: false,
  })

  // 详情取不到时不猜书名：列表里那个名字属于列表，不属于这一屏
  const title = q.data?.title ?? null
  const chapters = q.data?.chapters ?? null
  const checklistGaps = Object.entries(q.data?.stage_done ?? {})
    .filter(([stage, done]) => {
      const required = REQUIRED_GATE_BY_STAGE[stage as keyof typeof REQUIRED_GATE_BY_STAGE]
      return done === true && required && doc && !doc.gate[required]
    })
    .map(([stage]) => stage)
  // gate 的有效性只认后端；这里仅给已有产物对应的正式审阅页导航。
  // s6/s7 也可能因前置链暂停，不能把 false 推断成必须重签。
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
      {checklistGaps.length ? (
        <div className="flex items-center gap-10 border-b border-warn-line bg-warn-bg px-30 py-10 text-body text-warn-ink">
          <span aria-hidden>!</span>
          <span className="min-w-0 flex-1">
            盘上已有 {checklistGaps.join('、')} 产物，但对应的历史审批检查单尚未闭合。
            可按盘面核对并补录仍能对证的历史记录；需要正式审阅的决定仍须在对应页面处理。
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
      {finalApprovalGaps.length ? (
        <div className="flex items-center gap-10 border-b border-warn-line bg-warn-bg px-30 py-10 text-body text-warn-ink">
          <span aria-hidden>!</span>
          <span className="min-w-0 flex-1">
            {finalApprovalGaps.map(({ label }) => label).join('、')}的最终定稿当前未生效。
            请到正式审阅页查看前置条件和当前决定；检查单补录不能代替定稿审阅。
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
      {backfill.data?.backfilled.length &&
      !(dismissed && dismissed.sid === sid && dismissed.data === backfill.data) ? (
        <div className="flex items-center gap-10 border-b border-line-soft bg-gate-ok-bg px-30 py-9 text-body text-ink-2">
          <span className="min-w-0 flex-1">
            已按正式产物补录 {backfill.data.backfilled.join('、')}；这些闸门来自盘面核对，不是你逐屏点击的记录。
          </span>
          <button
            type="button"
            onClick={() => setDismissed({ sid, data: backfill.data })}
            className="cursor-pointer rounded-btn border border-line-btn bg-card px-10 py-4 text-tag text-ink-3"
          >
            知道了
          </button>
        </div>
      ) : null}
      {backfill.isError ? (
        <div className="border-b border-warn-line bg-warn-bg px-30 py-9 text-body text-warn-ink">
          补录失败：{errorBody(backfill.error)}
          <ErrorHint error={backfill.error} />
        </div>
      ) : null}
      <Outlet />
    </div>
  )
}
