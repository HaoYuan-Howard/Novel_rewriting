import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useChrome } from '@/hooks/useChrome'
import { useJobs, useStartJob } from '@/hooks/useJobs'
import { SCRIM } from '@/constants/vocabulary'
import { cx } from '@/lib/cx'

/**
 * 花钱的操作要拦一道。
 *
 * 框里**原样摆出将要执行的那条完整命令**——它由服务端的 /jobs/preview 下发，
 * 前端不拼命令行（文案改一个字，前端拼的那条就和真跑的不是一回事了）。
 * 用户靠它确认参数没点错，尤其是 `--chapters 3,7` 这种。
 *
 * 不显示预估花费 / 预估调用次数：后端不提供，猜一个数写在界面上比不显示更坏。
 * 也没有取消：调用在途时钱已经花了，kill 掉省不回来。
 */
export function ConfirmDialog() {
  const { confirm, closeConfirm, openDrawer } = useChrome()
  const start = useStartJob(confirm?.req.sid)
  const jobs = useJobs(confirm?.req.sid)

  const preview = useQuery({
    queryKey: qk.jobPreview(confirm?.req ?? null),
    queryFn: () => api.previewJob(confirm!.req),
    enabled: !!confirm,
  })

  if (!confirm) return null

  const paid = preview.data?.paid ?? false
  const running = jobs.data?.running ?? null
  const busy = !!running && running.sid === confirm.req.sid

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center"
      style={{ background: SCRIM }}
      onClick={closeConfirm}
    >
      <div
        className="w-[660px] overflow-hidden rounded-modal border border-line-btn bg-card shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-10 border-b border-line-soft px-22 py-16">
          <span
            className={cx(
              'rounded-chip px-9 py-3 text-tag font-semibold',
              paid ? 'bg-spend text-card' : 'bg-line-soft text-ink-2',
            )}
          >
            {preview.isPending ? '取命令中' : paid ? '调 LLM · 花真钱' : '免费'}
          </span>
          <span className="text-h3 font-semibold">{confirm.req.label}</span>
        </div>

        <div className="flex flex-col gap-12 px-22 py-18">
          <div className="text-sm2 text-ink-4">
            将要执行的命令（服务端 <span className="font-mono">POST /api/jobs/preview</span>{' '}
            下发，原样摆出）
          </div>
          <div className="rounded-btn-lg border border-line bg-inset px-14 py-12 font-mono text-body break-all text-ink select-all">
            {preview.isPending
              ? '…'
              : preview.isError
                ? '取不到这条命令。没拿到服务端下发的 argv，就不该发起——界面不替你拼一条。'
                : preview.data?.argv}
          </div>
          <div className="prose-cn text-body text-ink-3">
            {paid
              ? '这一步会调 LLM。发起前请确认参数——尤其是章号。'
              : '这一步不调 LLM，但会写盘：它会刷新指标、提升产物或归档旧轮。'}
          </div>
          {busy ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              这本书已经有一个作业在跑（{running?.label}）。一本书同时只跑一个。
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13">
          <span className="text-tag text-ink-4">发起后没有取消——调用在途时钱已经花了。</span>
          <button
            type="button"
            onClick={closeConfirm}
            className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg"
          >
            返回
          </button>
          <button
            type="button"
            disabled={preview.isPending || preview.isError || busy || start.isPending}
            onClick={() => {
              start.mutate(confirm.req, {
                onSuccess: () => {
                  openDrawer()
                  confirm.after?.()
                  closeConfirm()
                },
              })
            }}
            className={cx(
              'rounded-btn px-18 py-8 text-body-lg font-semibold text-card',
              paid ? 'bg-spend' : 'bg-ink',
              preview.isPending || preview.isError || busy || start.isPending
                ? 'cursor-not-allowed opacity-40'
                : 'cursor-pointer',
            )}
          >
            {paid ? '确认发起（花钱）' : '发起'}
          </button>
        </div>
      </div>
    </div>
  )
}
