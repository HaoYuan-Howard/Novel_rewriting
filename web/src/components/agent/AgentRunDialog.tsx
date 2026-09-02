import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody, isApiError } from '@/api/errors'
import {
  useAgentCapabilities,
  useAgentProfileSettings,
  useStartAgentRun,
} from '@/hooks/useAgentRuns'
import { SCRIM } from '@/constants/vocabulary'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { AgentProfilesSnapshot } from './AgentProfilesSnapshot'
import { PipelineAgentProfilesForm } from './AgentProfilesForm'
import {
  clonePipelineAgentProfiles,
  emptyPipelineAgentProfilesDraft,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
  pipelineProfilesEqual,
} from '@/lib/agentProfiles'
import { cx } from '@/lib/cx'
import type { AgentRunRequest, PipelineAgentProfiles } from '@/types/agent'

function requestSnapshot(
  sid: string,
  authorized: boolean,
  egressAuthorized: boolean,
  profiles: PipelineAgentProfiles,
): AgentRunRequest {
  return {
    sid,
    authorized,
    egress_authorized: egressAuthorized,
    profiles: clonePipelineAgentProfiles(profiles),
  }
}

/** 启动只拿成功 preview 随身保存的 request，不从当前表单再拼一次。 */
interface PreviewEnvelope {
  request: AgentRunRequest
  response: Awaited<ReturnType<typeof api.previewAgentRun>>
}

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
  const [egressAuthorized, setEgressAuthorized] = useState(true)
  const [commandOpen, setCommandOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  const [profilesInitialized, setProfilesInitialized] = useState(false)
  const [profileDraft, setProfileDraft] = useState(emptyPipelineAgentProfilesDraft)
  const navigate = useNavigate()
  const capabilities = useAgentCapabilities()
  const settings = useAgentProfileSettings()
  const start = useStartAgentRun(sid)

  useEffect(() => {
    if (!capabilities.data || !settings.data || profilesInitialized) return
    const preferred = settings.data.state === 'configured'
      ? settings.data.profiles
      : settings.data.state === 'unset'
        ? capabilities.data.default_profiles
        : null
    setProfileDraft(pipelineDraftOf(preferred))
    setProfilesInitialized(true)
  }, [capabilities.data, profilesInitialized, settings.data])

  const materialized = useMemo(
    () => capabilities.data
      ? materializePipelineAgentProfiles(capabilities.data, profileDraft)
      : { profiles: null, errors: [] },
    [capabilities.data, profileDraft],
  )
  const request = useMemo(
    () => profilesInitialized && materialized.profiles
      ? requestSnapshot(sid, authorized, egressAuthorized, materialized.profiles)
      : null,
    [authorized, egressAuthorized, materialized.profiles, profilesInitialized, sid],
  )

  const preview = useQuery<PreviewEnvelope>({
    queryKey: qk.agentRunPreview(request),
    queryFn: async () => {
      if (!request) throw new Error('profile request is not materialized')
      return { request, response: await api.previewAgentRun(request) }
    },
    enabled: request !== null,
  })

  const echoed = preview.data
    ? preview.data.response.authorized === preview.data.request.authorized
      && preview.data.response.egress_authorized === preview.data.request.egress_authorized
      && pipelineProfilesEqual(
        preview.data.response.profiles,
        preview.data.request.profiles,
      )
    : false
  const conflict = isApiError(start.error) && start.error.backendKind === 'JobConflict'
  const disabled = !preview.data || !echoed || !authorized || !egressAuthorized || start.isPending
  const previewResponse = preview.data?.response

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center p-16"
      style={{ background: SCRIM }}
      onClick={onClose}
    >
      <div
        data-testid="agent-run-dialog"
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
          {capabilities.isPending || settings.isPending ? (
            <div className="rounded-block border border-line bg-inset px-14 py-11 text-body text-ink-4">
              正在读取常用配置并探测本机 Agent 能力；两份都拿到前不会预览或启动…
            </div>
          ) : capabilities.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
              取不到本机 Agent capability，因此不会预览或启动。
              <ErrorHint error={capabilities.error} />
            </div>
          ) : settings.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
              取不到设置页保存的常用配置，因此不会用产品默认替它猜一份。
              <ErrorHint error={settings.error} />
            </div>
          ) : capabilities.data && settings.data ? (
            <>
              {settings.data.state === 'configured' ? (
                <div className="flex items-center gap-10 rounded-block border border-gate-ok-line bg-gate-ok-bg px-14 py-10 text-body text-gate-ok-ink">
                  <span>已载入设置页保存的常用配置；下方改动只影响本次运行。</span>
                  <LinkButton
                    className="ml-auto"
                    onClick={() => { onClose(); navigate('/settings') }}
                  >
                    管理常用配置
                  </LinkButton>
                </div>
              ) : settings.data.state === 'unreadable' ? (
                <div className="flex items-center gap-10 rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                  <span>{settings.data.issue}</span>
                  <LinkButton
                    className="ml-auto"
                    onClick={() => { onClose(); navigate('/settings') }}
                  >
                    去修复设置
                  </LinkButton>
                </div>
              ) : capabilities.data.default_profiles === null ? (
                <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                  尚未保存常用 Agent 配置，服务端也没有发布产品默认；请在这里临时选择，
                  或到设置页保存后复用。界面不会猜第一项。
                  <LinkButton
                    className="ml-10"
                    onClick={() => { onClose(); navigate('/settings') }}
                  >
                    打开设置
                  </LinkButton>
                </div>
              ) : null}
              <PipelineAgentProfilesForm
                capabilities={capabilities.data}
                value={profileDraft}
                onChange={setProfileDraft}
                errors={materialized.errors}
                disabled={start.isPending}
              />
            </>
          ) : null}

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={authorized}
              disabled={start.isPending}
              onChange={(event) => setAuthorized(event.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许本会话产生外部模型调用费用。它只控制付费授权，不会顺带开启正文外发；
              指挥与执行者的真实费用均由所选 Agent/模型产生。
            </span>
          </label>

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={egressAuthorized}
              disabled={start.isPending}
              onChange={(event) => setEgressAuthorized(event.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许将小说正文及派生上下文发送给外部模型。取消它不会撤销付费授权，
              但需要正文外发的执行者会在调用前被拒绝。
            </span>
          </label>

          <div className="flex items-center gap-10">
            <span className="text-sm2 text-ink-4">
              将要执行的命令（服务端{' '}
              <span className="font-mono">POST /api/agent/runs/preview</span> 下发）
            </span>
            <LinkButton onClick={() => setCommandOpen((value) => !value)} disabled={!previewResponse}>
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
            {request === null
              ? '配置完成后才会请求预览。'
              : preview.isPending
                ? '…'
                : preview.isError && !preview.data
                  ? '取不到这条命令。没拿到服务端 preview，就不会发起。'
                  : previewResponse?.argv.join(' ')}
          </div>

          {previewResponse ? (
            <AgentProfilesSnapshot profiles={previewResponse.profiles} />
          ) : null}

          <div className="flex items-center gap-10">
            <span className="text-sm2 text-ink-4">给指挥 Agent 的 prompt（授权原文在里面）</span>
            <LinkButton onClick={() => setPromptOpen((value) => !value)} disabled={!previewResponse}>
              {promptOpen
                ? '收起 prompt'
                : '展开 prompt 全文' +
                  (previewResponse ? '（' + previewResponse.prompt.length + ' 字）' : '')}
            </LinkButton>
          </div>
          {promptOpen && previewResponse ? (
            <pre className="m-0 max-h-[220px] overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-12 font-mono text-mini leading-[1.9] whitespace-pre-wrap text-ink-2 select-all">
              {previewResponse.prompt}
            </pre>
          ) : null}

          {preview.data && !echoed ? (
            <div role="alert" className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              服务端 preview 回显的 Agent 配置或授权与请求不一致；为防配置漂移，本次启动已关闭。
            </div>
          ) : null}

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
          <span data-testid="agent-run-cost-note" className="text-tag text-ink-4">
            {!authorized
              ? '未授予模型调用费用：当前只可预览，不会启动指挥 Agent。'
              : !egressAuthorized
                ? '未授予正文外发：当前只可预览，不会启动指挥 Agent。'
                : '发起后跳到进度屏；指挥与付费执行节点都可能产生所选模型的调用费用。'}
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
            onClick={() => {
              if (!preview.data || !echoed) return
              start.mutate(
                preview.data.request,
                { onSuccess: () => onStarted(preview.data.request.sid) },
              )
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
                : '授权并开始跑书'}
          </button>
        </div>
      </div>
    </div>
  )
}
