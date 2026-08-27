import { useEffect, useMemo, useState } from 'react'
import { errorBody } from '@/api/errors'
import {
  useAgentCapabilities,
  useAgentQueue,
  useStartAgentBatch,
} from '@/hooks/useAgentRuns'
import { SCRIM } from '@/constants/vocabulary'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'
import { AgentProfilesSnapshot } from './AgentProfilesSnapshot'
import { PipelineAgentProfilesForm } from './AgentProfilesForm'
import {
  clonePipelineAgentProfiles,
  emptyPipelineAgentProfilesDraft,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
} from '@/lib/agentProfiles'
import { cx } from '@/lib/cx'
import type {
  AgentBatchRequest,
  AgentQueueView,
  PipelineAgentProfiles,
} from '@/types/agent'

interface AcceptedBatch {
  view: AgentQueueView
  /** 受理瞬间的整批请求快照；排队中的书也冻结这一份。 */
  request: AgentBatchRequest
}

export function AgentBatchDialog({
  books,
  onClose,
}: {
  books: { sid: string; title: string }[]
  onClose: () => void
}) {
  const [authorized, setAuthorized] = useState(false)
  const [egressAuthorized, setEgressAuthorized] = useState(true)
  const [accepted, setAccepted] = useState<AcceptedBatch | null>(null)
  const [profilesInitialized, setProfilesInitialized] = useState(false)
  const [profileDraft, setProfileDraft] = useState(emptyPipelineAgentProfilesDraft)
  const capabilities = useAgentCapabilities()
  const batch = useStartAgentBatch()
  const queue = useAgentQueue()

  useEffect(() => {
    if (!capabilities.data || profilesInitialized) return
    setProfileDraft(pipelineDraftOf(capabilities.data.default_profiles))
    setProfilesInitialized(true)
  }, [capabilities.data, profilesInitialized])

  const materialized = useMemo(
    () => capabilities.data
      ? materializePipelineAgentProfiles(capabilities.data, profileDraft)
      : { profiles: null, errors: [] },
    [capabilities.data, profileDraft],
  )
  const gate = accepted?.view.max_concurrent ?? queue.data?.max_concurrent ?? null
  const alreadyRunning = queue.data?.running.length ?? 0
  const disabled = batch.isPending
    || books.length === 0
    || materialized.profiles === null
    || !authorized
    || !egressAuthorized

  const submit = (profiles: PipelineAgentProfiles) => {
    const request: AgentBatchRequest = {
      sids: books.map((book) => book.sid),
      authorized,
      egress_authorized: egressAuthorized,
      profiles: clonePipelineAgentProfiles(profiles),
    }
    batch.mutate(request, {
      onSuccess: (view) => setAccepted({ view, request }),
    })
  }

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center p-16"
      style={{ background: SCRIM }}
      onClick={onClose}
    >
      <div
        data-testid="agent-batch-dialog"
        className="flex max-h-[calc(100vh-32px)] w-[min(760px,calc(100vw-32px))] flex-col overflow-hidden rounded-modal border border-line-btn bg-card shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-10 border-b border-line-soft px-22 py-16">
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
            {accepted ? '这一批已受理' : '让 agent 跑这 ' + books.length + ' 本'}
          </span>
        </div>

        <div className="flex min-h-0 flex-col gap-12 overflow-y-auto px-22 py-18">
          {accepted ? (
            <>
              <AgentProfilesSnapshot profiles={accepted.request.profiles} />
              <div className="text-tag text-ink-4">
                上面是本批受理时的冻结配置；后续修改页面或服务端默认都不会改写排队项。
              </div>
              <BatchOutcome result={accepted.view} books={books} />
            </>
          ) : (
            <>
              <div className="max-h-[190px] overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-10">
                {books.map((book) => (
                  <div key={book.sid} className="flex items-baseline gap-10 py-3">
                    <span className="text-body text-ink">{book.title}</span>
                    <Mono className="ml-auto text-mini text-ink-4">{book.sid}</Mono>
                  </div>
                ))}
              </div>

              <div className="rounded-block border border-line-btn bg-sunken-2 px-14 py-10 text-body text-ink-2">
                最多 <span className="font-semibold">{gate ?? '…'}</span> 本同时跑，其余排队；
                一本结束自动带起下一本。
                {alreadyRunning > 0 ? '现在已经有 ' + alreadyRunning + ' 本在跑，占着名额。' : ''}
                <div className="mt-6 text-ink-4">
                  ⚠ 每本书起一个指挥 Agent；它派生的执行者按下方同一快照运行。
                  排队只在服务进程内存里，重启不保留。
                </div>
              </div>

              {capabilities.isPending ? (
                <div className="rounded-block border border-line bg-inset px-14 py-11 text-body text-ink-4">
                  正在探测本机 Agent 能力；拿到 capability 前不会受理这一批…
                </div>
              ) : capabilities.isError ? (
                <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
                  取不到本机 Agent capability，因此不会启动。
                  <ErrorHint error={capabilities.error} />
                </div>
              ) : capabilities.data ? (
                <>
                  {capabilities.data.default_profiles === null ? (
                    <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                      服务端尚未发布默认 Agent 配置；请显式选完本批的指挥与执行者。界面不会猜第一项。
                    </div>
                  ) : null}
                  <PipelineAgentProfilesForm
                    capabilities={capabilities.data}
                    value={profileDraft}
                    onChange={setProfileDraft}
                    errors={materialized.errors}
                    disabled={batch.isPending}
                  />
                </>
              ) : null}

              <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
                <input
                  type="checkbox"
                  checked={authorized}
                  disabled={batch.isPending}
                  onChange={(event) => setAuthorized(event.target.checked)}
                  className="mt-3"
                />
                <span className="text-body text-ink-2">
                  允许这一批产生外部模型调用费用。它不自动开启正文外发授权。
                </span>
              </label>

              <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
                <input
                  type="checkbox"
                  checked={egressAuthorized}
                  disabled={batch.isPending}
                  onChange={(event) => setEgressAuthorized(event.target.checked)}
                  className="mt-3"
                />
                <span className="text-body text-ink-2">
                  允许把这几本的正文及派生上下文发送给外部模型。取消它不会撤销付费授权。
                </span>
              </label>

              {batch.isError ? (
                <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                  {errorBody(batch.error)}
                  <ErrorHint error={batch.error} />
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13">
          <span className="text-tag text-ink-4">
            {accepted
              ? '每本书的进度与冻结配置都在它自己的「进度」屏上。'
              : !authorized
                ? '未授予模型调用费用：当前仅可预览配置，不会启动任何一本。'
                : !egressAuthorized
                  ? '未授予正文外发：当前仅可预览配置，不会启动任何一本。'
                  : '受理后立刻返回，不等任何一本跑完。'}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg"
          >
            {accepted ? '关闭' : '返回'}
          </button>
          {accepted ? null : (
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                if (materialized.profiles) submit(materialized.profiles)
              }}
              className={cx(
                'rounded-btn px-18 py-8 text-body-lg font-semibold text-card',
                authorized ? 'bg-spend' : 'bg-ink',
                disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
              )}
            >
              {!authorized
                ? '需先授权模型调用费用'
                : !egressAuthorized
                  ? '需先授权正文外发'
                  : '授权并开始跑这 ' + books.length + ' 本'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function BatchOutcome({
  result,
  books,
}: {
  result: AgentQueueView
  books: { sid: string; title: string }[]
}) {
  const title = (sid: string) => books.find((book) => book.sid === sid)?.title ?? sid

  return (
    <>
      <Group label={'已经在跑（' + result.running.length + '）'}>
        {result.running.map((run) => (
          <Row key={run.run_id} name={title(run.start.sid)} sid={run.start.sid} note="running" />
        ))}
      </Group>
      <Group label={'排队中（' + result.pending.length + '）'}>
        {result.pending.map((pending) => (
          <Row
            key={pending.sid}
            name={title(pending.sid)}
            sid={pending.sid}
            note={'第 ' + pending.position + ' 位'}
          />
        ))}
      </Group>
      {result.rejected.length ? (
        <Group label={'没有受理（' + result.rejected.length + '）'} warn>
          {result.rejected.map((rejected) => (
            <Row
              key={rejected.sid}
              name={title(rejected.sid)}
              sid={rejected.sid}
              note={rejected.reason}
              warn
            />
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
