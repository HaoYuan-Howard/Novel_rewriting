import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody, isApiError } from '@/api/errors'
import { useStartAgentRun } from '@/hooks/useAgentRuns'
import { SCRIM } from '@/constants/vocabulary'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { cx } from '@/lib/cx'

/**
 * 发起 agent 代跑前拦一道。
 *
 * 与 ConfirmDialog 同一条纪律：argv / prompt 由服务端 preview **原样下发**，
 * 前端一个字符都不拼。prompt 里带着付费与正文外发两项授权的原文 ——
 * 所以任一勾选变化都重新 preview，框里摆的必须是将要发出去的那一份。
 * 互斥不归这个框管：服务端 409（JobConflict）是裁决，框只把那句话摆出来。
 */
export function AgentRunDialog({
  sid,
  onClose,
  onStarted,
}: {
  sid: string
  onClose: () => void
  onStarted: (sid: string) => void
}) {
  const [authorized, setAuthorized] = useState(false)
  // 产品默认允许正文外发，但仍作为独立复选框显式提交，用户可随时取消。
  const [egressAuthorized, setEgressAuthorized] = useState(true)
  const [commandOpen, setCommandOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  const start = useStartAgentRun(sid)

  const preview = useQuery({
    queryKey: qk.agentRunPreview(sid, authorized, egressAuthorized),
    queryFn: () => api.previewAgentRun({
      sid,
      authorized,
      egress_authorized: egressAuthorized,
    }),
  })

  const conflict = isApiError(start.error) && start.error.backendKind === 'JobConflict'
  const disabled = preview.isPending || preview.isError || start.isPending

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center p-16"
      style={{ background: SCRIM }}
      onClick={onClose}
    >
      <div
        data-testid="agent-run-dialog"
        className="flex max-h-[calc(100vh-32px)] w-[min(660px,calc(100vw-32px))] flex-col overflow-hidden rounded-modal border border-line-btn bg-card shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-10 border-b border-line-soft px-22 py-16">
          <span
            className={cx(
              'rounded-chip px-9 py-3 text-tag font-semibold',
              authorized ? 'bg-spend text-card' : 'bg-line-soft text-ink-2',
            )}
          >
            {authorized ? '已授权付费节点' : '仅诊断与恢复方案'}
          </span>
          <span
            className={cx(
              'rounded-chip px-9 py-3 text-tag font-semibold',
              egressAuthorized
                ? 'bg-gate-ok-bg text-gate-ok-ink'
                : 'bg-warn-bg text-warn-ink',
            )}
          >
            {egressAuthorized ? '允许正文外发' : '禁止正文外发'}
          </span>
          <span className="text-h3 font-semibold">让 agent 跑这本书</span>
          <Mono className="ml-auto text-mini text-ink-4">{sid}</Mono>
        </div>

        <div
          data-testid="agent-run-dialog-body"
          className="flex min-h-0 flex-col gap-12 overflow-y-auto px-22 py-18"
        >
          <div className="flex items-center gap-10">
            <span className="text-sm2 text-ink-4">
              将要执行的命令（服务端{' '}
              <span className="font-mono">POST /api/agent/runs/preview</span> 下发）
            </span>
            <LinkButton onClick={() => setCommandOpen((v) => !v)} disabled={!preview.data}>
              {commandOpen ? '收起完整命令' : '展开完整命令'}
            </LinkButton>
          </div>
          <div
            data-testid="agent-command-preview"
            className={cx(
              'overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-12 font-mono text-body break-all text-ink select-all',
              commandOpen ? 'max-h-[260px]' : 'max-h-[92px]',
            )}
          >
            {preview.isPending
              ? '…'
              : preview.isError
                ? '取不到这条命令。没拿到服务端下发的 argv，就不该发起——界面不替你拼一条。'
                : preview.data?.argv.join(' ')}
          </div>

          <div className="flex items-center gap-10">
            <span className="text-sm2 text-ink-4">给 agent 的 prompt（授权原文在里面）</span>
            <LinkButton onClick={() => setPromptOpen((v) => !v)} disabled={!preview.data}>
              {promptOpen
                ? '收起 prompt'
                : '展开 prompt 全文' +
                  (preview.data ? '（' + preview.data.prompt.length + ' 字）' : '')}
            </LinkButton>
          </div>
          {promptOpen && preview.data ? (
            <pre className="m-0 max-h-[220px] overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-12 font-mono text-mini leading-[1.9] whitespace-pre-wrap text-ink-2 select-all">
              {preview.data.prompt}
            </pre>
          ) : null}

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={authorized}
              onChange={(e) => setAuthorized(e.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许本会话产生外部模型调用费用（skill 接口契约的付费授权）；默认不勾，
              不勾则 agent 在第一条付费命令前停下
            </span>
          </label>

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={egressAuthorized}
              onChange={(e) => setEgressAuthorized(e.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许将小说正文及派生上下文发送给外部模型（正文外发授权，默认勾选）；取消后即使已有
              付费授权，agent 也会在首个需要正文外发的步骤前停下
            </span>
          </label>

          {start.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              {conflict ? (
                '这本书已经有一个代跑在跑（服务端 409：同一本书互斥；其他书仍可并行）。等它跑完，或去进度屏把它熄火。'
              ) : (
                <>
                  {errorBody(start.error)}
                  <ErrorHint error={start.error} />
                </>
              )}
            </div>
          ) : null}
        </div>

        <div
          data-testid="agent-run-dialog-footer"
          className="flex shrink-0 items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13"
        >
          <span className="text-tag text-ink-4">
            {authorized
              ? '发起后跳到进度屏：跑到哪一步、退出码都在那。'
              : '只收集状态、下一步命令与恢复方案，不执行付费节点。'}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg"
          >
            返回
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() =>
              start.mutate(
                { sid, authorized, egress_authorized: egressAuthorized },
                { onSuccess: () => onStarted(sid) },
              )
            }
            className={cx(
              'rounded-btn px-18 py-8 text-body-lg font-semibold text-card',
              authorized ? 'bg-spend' : 'bg-ink',
              disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
            )}
          >
            {authorized ? '授权并开始跑书' : '生成诊断与恢复方案'}
          </button>
        </div>
      </div>
    </div>
  )
}
