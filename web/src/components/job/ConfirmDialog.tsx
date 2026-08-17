import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody, errorTitle } from '@/api/errors'
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
  const [preparing, setPreparing] = useState(false)
  const [requiredValues, setRequiredValues] = useState<Record<string, string>>({})
  /**
   * 这一次确认为什么没发起。
   *
   * ⚠⚠ **必须有一个本地落点，不能指望「mutation state 自己会呈现」**：
   * `before` 往往是别的组件持有的 mutation（挑深度那屏的 `setDeep`、闸门那条的
   * `approve`），它的 error 在**那个组件**里，本框看不到；而 `start` 的 error
   * 本框虽然看得到，却一行都没画。结果是后端已经 409 了，
   * 框还开着、按钮还能点、**屏幕上一个字都没有** —— 用户只会反复点一个
   * 花钱的按钮。发起失败必须被看见，这与 `U-00`（空白不等于没问题）是同一条。
   */
  const [failure, setFailure] = useState<unknown>(null)
  const start = useStartJob(confirm?.req.sid)
  const jobs = useJobs(confirm?.req.sid)

  const preview = useQuery({
    queryKey: qk.jobPreview(confirm?.req ?? null),
    queryFn: () => api.previewJob(confirm!.req),
    enabled: !!confirm,
  })

  useEffect(() => {
    // A credential belongs to exactly one confirmation. Reopening even the
    // same action must never inherit the previous actor/reason silently.
    setRequiredValues({})
    setFailure(null)
  }, [confirm])

  if (!confirm) return null

  const presentTokens = [...(confirm.req.extra ?? []), ...(confirm.req.args ?? [])]
  const requiredInputs = (confirm.req.required_inputs ?? []).filter(
    (input) => !presentTokens.includes(input.flag),
  )
  const requiredReady = requiredInputs.every(
    (input) => (requiredValues[input.flag] ?? '').trim().length > 0,
  )
  const executionArgs = [
    ...(confirm.req.args ?? []),
    ...requiredInputs.flatMap((input) => [input.flag, requiredValues[input.flag]?.trim() ?? '']),
  ]
  const executionReq = executionArgs.length
    ? { ...confirm.req, args: executionArgs }
    : confirm.req

  // A background refetch is deliberately treated as unknown. Never leave a stale command or
  // price visible while a request-shaping input (including mode) is being revalidated.
  const planReady = preview.isSuccess && !preview.isFetching
  const paid = planReady ? preview.data.paid : null
  const steps = planReady ? (preview.data?.steps ?? []) : []
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
              paid === true ? 'bg-spend text-card' : 'bg-line-soft text-ink-2',
            )}
          >
            {!planReady
              ? '贵贱待服务端确认'
              : paid
                ? '调 LLM · 花真钱'
                : '免费'}
          </span>
          <span className="text-h3 font-semibold">{confirm.req.label}</span>
        </div>

        <div className="flex flex-col gap-12 px-22 py-18">
          <div className="text-sm2 text-ink-4">
            执行计划（服务端 <span className="font-mono">POST /api/jobs/preview</span> 下发）
          </div>
          {preview.isFetching ? (
            <div className="rounded-btn-lg border border-line bg-inset px-14 py-12 text-body text-ink-4">
              正在取得逐步命令、贵贱与写盘范围…
            </div>
          ) : preview.isError ? (
            <div className="rounded-btn-lg border border-warn-line bg-warn-bg px-14 py-12 text-body text-warn-ink">
              取不到执行计划。贵贱保持未知，也不会允许发起；界面不替服务端猜命令。
            </div>
          ) : (
            <ol className="m-0 flex max-h-[420px] list-none flex-col gap-9 overflow-y-auto p-0">
              {steps.map((step, index) => (
                <li key={index} className="overflow-hidden rounded-btn-lg border border-line bg-inset">
                  <div className="flex flex-wrap items-center gap-8 border-b border-line-soft px-14 py-9">
                    <span className="font-mono text-mini text-ink-5">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="text-body font-semibold">{step.label}</span>
                    <span
                      className={cx(
                        'rounded-tag px-7 py-2 text-mini font-semibold',
                        step.paid ? 'bg-spend text-card' : 'bg-card text-ink-3',
                      )}
                    >
                      {step.paid ? '调 LLM · 花钱' : '不调 LLM'}
                    </span>
                    <span className="text-mini text-ink-4">
                      {step.writes ? '写盘：' + step.writes : '不写盘'}
                    </span>
                  </div>
                  <pre className="m-0 px-14 py-11 font-mono text-mini leading-[1.8] break-words whitespace-pre-wrap text-ink select-all">
                    {step.argv}
                  </pre>
                </li>
              ))}
            </ol>
          )}
          {requiredInputs.length ? (
            <fieldset className="m-0 flex flex-col gap-9 rounded-btn-lg border border-line bg-sunken-2 px-14 py-12">
              <legend className="px-4 text-body font-semibold">执行前必填的人工审计</legend>
              <div className="prose-cn text-sm2 text-ink-3">
                上方命令里的 <span className="font-mono">待填·…</span> 只是预览模板；
                真正发起时只提交你在这里填写的实值，服务端仍会拒绝空值或占位符。
              </div>
              {requiredInputs.map((input) => (
                <label key={input.flag} className="flex flex-col gap-4 text-sm2 text-ink-2">
                  <span>
                    <span className="font-mono font-semibold">{input.flag}</span>
                    <span className="ml-6 text-ink-4">{input.description}</span>
                  </span>
                  <input
                    aria-label={input.flag}
                    autoComplete="off"
                    value={requiredValues[input.flag] ?? ''}
                    onChange={(event) =>
                      setRequiredValues((current) => ({
                        ...current,
                        [input.flag]: event.target.value,
                      }))
                    }
                    className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body text-ink"
                  />
                </label>
              ))}
            </fieldset>
          ) : null}
          <div className="prose-cn text-body text-ink-3">
            {paid === null
              ? '服务端还没有确认这次动作的贵贱；执行计划取回前不会发起。'
              : paid
              ? '本计划含 LLM 调用。发起前请逐步确认参数——尤其是章号。'
              : '本计划不调 LLM；请逐步确认写盘范围。'}
          </div>
          {busy ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              这本书已经有一个作业在跑（{running?.label}）。一本书同时只跑一个。
            </div>
          ) : null}
          {failure ? (
            <div
              role="alert"
              className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink"
            >
              <div className="font-semibold">没有发起 —— {errorTitle(failure)}</div>
              <div className="prose-cn mt-3">{errorBody(failure)}</div>
              <div className="mt-3 text-tag">
                这一步没有执行，也没有产生费用。先按上面的原因处理，再重新发起。
              </div>
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
            disabled={!planReady || !requiredReady || busy || preparing || start.isPending}
            onClick={async () => {
              setPreparing(true)
              setFailure(null)
              try {
                await confirm.before?.()
                if (confirm.run) {
                  // 专用写回路由：执行不走 POST /jobs，但确认的是同一份服务端计划。
                  // 成败都进作业抽屉——失败的作业要被看见，不是被吞掉。
                  await confirm.run()
                  openDrawer()
                  confirm.after?.()
                  closeConfirm()
                } else {
                  start.mutate(executionReq, {
                    onSuccess: () => {
                      openDrawer()
                      confirm.after?.()
                      closeConfirm()
                    },
                    // 没起来就把原因摆在框里，并且**不关框**：
                    // 关掉等于把「没发起」演成「发起了」。
                    onError: (error) => setFailure(error),
                  })
                }
              } catch (error) {
                // 一个被拒绝的前置**绝不能**继续掉进 POST /jobs；
                // 同时它失败的原因要落在本框里，`before` 的 mutation 在别的组件上。
                setFailure(error)
              } finally {
                setPreparing(false)
              }
            }}
            className={cx(
              'rounded-btn px-18 py-8 text-body-lg font-semibold text-card',
              paid === true ? 'bg-spend' : 'bg-ink',
              !planReady || !requiredReady || busy || preparing || start.isPending
                ? 'cursor-not-allowed opacity-40'
                : 'cursor-pointer',
            )}
          >
            {!planReady
              ? '等待执行计划'
              : !requiredReady
                ? '请先填写必填项'
                : paid === true
                  ? '确认发起（花钱）'
                  : '发起'}
          </button>
        </div>
      </div>
    </div>
  )
}
