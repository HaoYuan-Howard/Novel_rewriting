import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  useAgentCapabilities,
  useAgentProfileSettings,
} from '@/hooks/useAgentRuns'
import { SCRIM } from '@/constants/vocabulary'
import { Mono } from '@/components/ui/Mono'
import { ErrorHint } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { PipelineAgentProfilesForm } from './AgentProfilesForm'
import {
  emptyPipelineAgentProfilesDraft,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
} from '@/lib/agentProfiles'
import { cx } from '@/lib/cx'
import type { PipelineAgentProfiles } from '@/types/agent'
import type { UploadCorpusOptions } from '@/types/jobs'

export interface UploadAuthConfirm {
  authorized: boolean
  egressAuthorized: boolean
  profiles: PipelineAgentProfiles
}

/**
 * 上传前收集付费/外发授权与 profiles；确认后由 CorpusScreen 带幂等键调用 uploadCorpus。
 * 不走 agent preview/start —— 上传接口本身负责自动开跑。
 */
export function UploadAuthDialog({
  fileCount,
  pending,
  error,
  onClose,
  onConfirm,
}: {
  fileCount: number
  pending: boolean
  error: unknown
  onClose: () => void
  onConfirm: (value: UploadAuthConfirm) => void
}) {
  const [authorized, setAuthorized] = useState(false)
  const [egressAuthorized, setEgressAuthorized] = useState(false)
  const [profilesInitialized, setProfilesInitialized] = useState(false)
  const [profileDraft, setProfileDraft] = useState(emptyPipelineAgentProfilesDraft)
  const navigate = useNavigate()
  const capabilities = useAgentCapabilities()
  const settings = useAgentProfileSettings(true)

  useEffect(() => {
    if (!capabilities.data || profilesInitialized || !settings.data) return
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
      : { profiles: null, errors: [] as string[] },
    [capabilities.data, profileDraft],
  )

  const disabled = !authorized || !egressAuthorized || !materialized.profiles
    || materialized.errors.length > 0 || pending
    || capabilities.isPending || settings.isPending
    || capabilities.isError || settings.isError

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center p-16"
      style={{ background: SCRIM }}
      onClick={onClose}
    >
      <div
        data-testid="upload-auth-dialog"
        className="flex max-h-[calc(100vh-32px)] w-[min(760px,calc(100vw-32px))] flex-col overflow-hidden rounded-modal border border-line-btn bg-card shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-10 border-b border-line-soft px-22 py-16">
          <span className="text-h3 font-semibold">上传并自动开跑</span>
          <Mono className="ml-auto text-mini text-ink-4">
            {fileCount === 1 ? '1 本' : `${fileCount} 本`}
          </Mono>
        </div>

        <div className="flex min-h-0 flex-col gap-12 overflow-y-auto px-22 py-18">
          <p className="m-0 text-body text-ink-3">
            先确认付费与正文外发授权，并选定本次流水线 profiles。
            上传成功后服务端会自动开跑；每本使用独立幂等键，网络重试会复用同一键。
          </p>

          {capabilities.isPending || settings.isPending ? (
            <div className="rounded-block border border-line bg-inset px-14 py-11 text-body text-ink-4">
              正在读取常用配置并探测本机 Agent 能力…
            </div>
          ) : capabilities.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
              取不到本机 Agent capability，因此不能上传开跑。
              <ErrorHint error={capabilities.error} />
            </div>
          ) : settings.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
              取不到设置页保存的常用配置。
              <ErrorHint error={settings.error} />
            </div>
          ) : capabilities.data && settings.data ? (
            <>
              {settings.data.state === 'configured' ? (
                <div className="flex items-center gap-10 rounded-block border border-gate-ok-line bg-gate-ok-bg px-14 py-10 text-body text-gate-ok-ink">
                  <span>已载入设置页保存的常用配置；下方改动只影响本次上传开跑。</span>
                  <LinkButton
                    className="ml-auto"
                    onClick={() => { onClose(); navigate('/settings') }}
                  >
                    管理常用配置
                  </LinkButton>
                </div>
              ) : null}
              <PipelineAgentProfilesForm
                capabilities={capabilities.data}
                value={profileDraft}
                onChange={setProfileDraft}
                errors={materialized.errors}
                disabled={pending}
              />
            </>
          ) : null}

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={authorized}
              disabled={pending}
              onChange={(event) => setAuthorized(event.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许本会话产生外部模型调用费用。
            </span>
          </label>

          <label className="flex cursor-pointer items-start gap-8 rounded-block border border-line-btn bg-sunken-2 px-14 py-10">
            <input
              type="checkbox"
              checked={egressAuthorized}
              disabled={pending}
              onChange={(event) => setEgressAuthorized(event.target.checked)}
              className="mt-3"
            />
            <span className="text-body text-ink-2">
              允许将小说正文及派生上下文发送给外部模型。
            </span>
          </label>

          {error ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              <ErrorHint error={error} />
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13">
          <span className="text-tag text-ink-4">
            {!authorized || !egressAuthorized
              ? '两项授权都勾选后才能上传并自动开跑。'
              : '确认后开始上传；成功后进入进度屏。'}
          </span>
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg disabled:opacity-40"
          >
            返回
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              if (!materialized.profiles) return
              onConfirm({
                authorized,
                egressAuthorized,
                profiles: materialized.profiles,
              })
            }}
            className={cx(
              'rounded-btn border-none bg-ink px-16 py-8 text-body-lg font-semibold text-card',
              disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
            )}
          >
            {pending ? '上传中…' : '确认上传并开跑'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** 供上传调用方把对话框确认值转成 uploadCorpus options（幂等键由调用方生成/复用）。 */
export function toUploadOptions(
  confirm: UploadAuthConfirm,
  idempotencyKey: string,
): UploadCorpusOptions {
  return {
    idempotencyKey,
    authorized: confirm.authorized,
    egressAuthorized: confirm.egressAuthorized,
    profiles: confirm.profiles,
  }
}
