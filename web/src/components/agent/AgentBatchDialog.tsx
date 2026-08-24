import { useState } from 'react'
import { errorBody } from '@/api/errors'
import { useAgentQueue, useStartAgentBatch } from '@/hooks/useAgentRuns'
import { SCRIM } from '@/constants/vocabulary'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'
import { cx } from '@/lib/cx'
import type { AgentQueueView } from '@/types/agent'

/**
 * 批量发起 agent 代跑前拦一道。
 *
 * 与单本的 `AgentRunDialog` 的分界线：那一个摆的是**将要执行的那条命令**
 * （服务端 preview 原样下发，`R-11.8` 同构）；N 本摆不下 N 条命令，
 * 所以这一个摆的是**这一批的边界**——哪几本、几个同时跑、两项授权覆盖到哪。
 * ⚠ 要逐字核对某一本的 argv，仍然走那本书自己的「agent 跑书」按钮。
 *
 * ⚠ 授权对**整批**生效，不逐本勾：逐本勾会让「这一批到底授权了什么」
 * 没有一个能写进终审包的答案。要给不同书不同授权，就分两批。
 */
export function AgentBatchDialog({
  books,
  onClose,
}: {
  books: { sid: string; title: string }[]
  onClose: () => void
}) {
  const [authorized, setAuthorized] = useState(false)
  // 与单本入口同款产品默认：默认允许正文外发，但仍显式提交，随时可取消。
  const [egressAuthorized, setEgressAuthorized] = useState(true)
  const [result, setResult] = useState<AgentQueueView | null>(null)
  const batch = useStartAgentBatch()
  const queue = useAgentQueue()

  // 闸由服务端下发。⚠ 前端不写死这个数——同「贵贱只有一处算」那条纪律。
  const gate = result?.max_concurrent ?? queue.data?.max_concurrent ?? null
  const alreadyRunning = queue.data?.running.length ?? 0

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center"
      style={{ background: SCRIM }}
      onClick={onClose}
    >
      <div
        className="w-[660px] overflow-hidden rounded-modal border border-line-btn bg-card shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-10 border-b border-line-soft px-22 py-16">
          <span
            className={cx(
              'rounded-chip px-9 py-3 text-tag font-semibold',
              authorized ? 'bg-spend text-card' : 'bg-line-soft text-ink-2',
            )}
          >
            {authorized ? '已授权流水线付费节点' : '未授权流水线付费节点'}
          </span>
          <span
            className={cx(
              'rounded-chip px-9 py-3 text-tag font-semibold',
              egressAuthorized ? 'bg-gate-ok-bg text-gate-ok-ink' : 'bg-warn-bg text-warn-ink',
            )}
          >
            {egressAuthorized ? '允许正文外发' : '禁止正文外发'}
          </span>
          <span className="text-h3 font-semibold">
            {result ? '这一批已受理' : '让 agent 跑这 ' + books.length + ' 本'}
          </span>
        </div>

        {result ? (
          <div className="flex flex-col gap-12 px-22 py-18">
            <BatchOutcome result={result} books={books} />
          </div>
        ) : (
          <div className="flex flex-col gap-12 px-22 py-18">
            <div className="max-h-[190px] overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-10">
              {books.map((b) => (
                <div key={b.sid} className="flex items-baseline gap-10 py-3">
                  <span className="text-body text-ink">{b.title}</span>
                  <Mono className="ml-auto text-mini text-ink-4">{b.sid}</Mono>
                </div>
              ))}
            </div>

            <div className="rounded-block border border-line-btn bg-sunken-2 px-14 py-10 text-body text-ink-2">
              最多 <span className="font-semibold">{gate ?? '…'}</span> 本同时跑，其余排队；
              一本结束自动带起下一本。
              {alreadyRunning > 0 ? '现在已经有 ' + alreadyRunning + ' 本在跑，占着名额。' : ''}
              <div className="mt-6 text-ink-4">
                ⚠ 每本书起一个 agent 进程，它自己花一笔、它起的流水线再花一笔。
                排队只在服务进程内存里，重启不保留。
              </div>
            </div>

            <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
              <input
                type="checkbox"
                checked={authorized}
                onChange={(e) => setAuthorized(e.target.checked)}
                className="mt-3"
              />
              <span className="text-body text-ink-2">
                允许这一批产生外部模型调用费用（skill 接口契约的付费授权）；默认不勾，
                不勾则每个 agent 都在第一条付费命令前停下
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
                允许把这几本的正文及派生上下文发送给外部模型（正文外发授权，默认勾选）
              </span>
            </label>

            {batch.isError ? (
              <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                {errorBody(batch.error)}
                <ErrorHint error={batch.error} />
              </div>
            ) : null}
          </div>
        )}

        <div className="flex items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13">
          <span className="text-tag text-ink-4">
            {result
              ? '每本书的进度在它自己的「进度」屏上。'
              : '受理后立刻返回，不等任何一本跑完。'}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg"
          >
            {result ? '关闭' : '返回'}
          </button>
          {result ? null : (
            <button
              type="button"
              disabled={batch.isPending || books.length === 0}
              onClick={() =>
                batch.mutate(
                  {
                    sids: books.map((b) => b.sid),
                    authorized,
                    egress_authorized: egressAuthorized,
                  },
                  { onSuccess: (view) => setResult(view) },
                )
              }
              className={cx(
                'rounded-btn px-18 py-8 text-body-lg font-semibold text-card',
                authorized ? 'bg-spend' : 'bg-ink',
                batch.isPending || books.length === 0
                  ? 'cursor-not-allowed opacity-40'
                  : 'cursor-pointer',
              )}
            >
              {authorized
                ? '授权并开始跑这 ' + books.length + ' 本'
                : '批量启动诊断（仍产生 Claude 费用）'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * 受理结果：三堆逐本摆开。
 * ⚠ 「部分失败不回滚」在界面上的样子——被拒的那几本连同原因单独列出，
 * 而不是把整批说成失败（那会把已经在跑的那几本藏掉）。
 */
function BatchOutcome({
  result,
  books,
}: {
  result: AgentQueueView
  books: { sid: string; title: string }[]
}) {
  const title = (sid: string) => books.find((b) => b.sid === sid)?.title ?? sid

  return (
    <>
      <Group label={'已经在跑（' + result.running.length + '）'}>
        {result.running.map((r) => (
          <Row key={r.run_id} name={title(r.start.sid)} sid={r.start.sid} note="running" />
        ))}
      </Group>
      <Group label={'排队中（' + result.pending.length + '）'}>
        {result.pending.map((p) => (
          <Row key={p.sid} name={title(p.sid)} sid={p.sid} note={'第 ' + p.position + ' 位'} />
        ))}
      </Group>
      {result.rejected.length ? (
        <Group label={'没有受理（' + result.rejected.length + '）'} warn>
          {result.rejected.map((r) => (
            <Row key={r.sid} name={title(r.sid)} sid={r.sid} note={r.reason} warn />
          ))}
        </Group>
      ) : null}
    </>
  )
}

function Group({
  label,
  warn,
  children,
}: {
  label: string
  warn?: boolean
  children: React.ReactNode
}) {
  const empty = Array.isArray(children) && children.length === 0
  return (
    <div>
      <div className={cx('mb-5 text-sm2', warn ? 'text-warn-ink' : 'text-ink-4')}>{label}</div>
      <div
        className={cx(
          'rounded-btn-lg border px-14 py-8',
          warn ? 'border-warn-line bg-warn-bg' : 'border-line bg-inset',
        )}
      >
        {empty ? <span className="text-body text-ink-5">这一堆是空的</span> : children}
      </div>
    </div>
  )
}

function Row({
  name,
  sid,
  note,
  warn,
}: {
  name: string
  sid: string
  note: string
  warn?: boolean
}) {
  return (
    <div className="flex items-baseline gap-10 py-3">
      <span className="text-body text-ink">{name}</span>
      <Mono className="text-mini text-ink-5">{sid}</Mono>
      <span className={cx('ml-auto text-tag', warn ? 'text-warn-ink' : 'text-ink-4')}>{note}</span>
    </div>
  )
}
