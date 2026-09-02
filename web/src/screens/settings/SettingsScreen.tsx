import { useEffect, useMemo, useState } from 'react'
import { errorBody, isApiError } from '@/api/errors'
import { PipelineAgentProfilesForm } from '@/components/agent/AgentProfilesForm'
import { ErrorHint, LoadFailed, LoadingPane } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import {
  useAgentCapabilities,
  useAgentProfileSettings,
  useSaveAgentProfileSettings,
} from '@/hooks/useAgentRuns'
import { useChrome } from '@/hooks/useChrome'
import {
  emptyPipelineAgentProfilesDraft,
  materializePipelineAgentProfiles,
  pipelineDraftOf,
  type PipelineAgentProfilesDraft,
} from '@/lib/agentProfiles'
import { cx } from '@/lib/cx'
import type {
  AgentCapabilitiesResponse,
  AgentProfileSettingsView,
} from '@/types/agent'

function initialDraft(
  capabilities: AgentCapabilitiesResponse,
  settings: AgentProfileSettingsView,
): PipelineAgentProfilesDraft {
  if (settings.state === 'configured') return pipelineDraftOf(settings.profiles)
  if (settings.state === 'unset') return pipelineDraftOf(capabilities.default_profiles)
  return emptyPipelineAgentProfilesDraft()
}

export function SettingsScreen() {
  const capabilities = useAgentCapabilities()
  const settings = useAgentProfileSettings()
  const save = useSaveAgentProfileSettings()
  const { notify } = useChrome()
  const [draft, setDraft] = useState(emptyPipelineAgentProfilesDraft)
  const [baseRevision, setBaseRevision] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!capabilities.data || !settings.data || baseRevision !== null) return
    setDraft(initialDraft(capabilities.data, settings.data))
    setBaseRevision(settings.data.revision)
  }, [baseRevision, capabilities.data, settings.data])

  const materialized = useMemo(
    () => capabilities.data
      ? materializePipelineAgentProfiles(capabilities.data, draft)
      : { profiles: null, errors: [] },
    [capabilities.data, draft],
  )

  const replaceDraft = (next: PipelineAgentProfilesDraft) => {
    setDraft(next)
    setDirty(true)
  }

  const adopt = (next: AgentProfileSettingsView) => {
    if (capabilities.data) setDraft(initialDraft(capabilities.data, next))
    setBaseRevision(next.revision)
    setDirty(false)
  }

  const reload = async () => {
    const result = await settings.refetch()
    if (result.data) {
      save.reset()
      adopt(result.data)
    }
  }

  if (capabilities.isError) {
    return <div className="p-30"><LoadFailed error={capabilities.error} onRetry={() => { void capabilities.refetch() }} /></div>
  }
  if (settings.isError) {
    return <div className="p-30"><LoadFailed error={settings.error} onRetry={() => { void settings.refetch() }} /></div>
  }
  if (capabilities.isPending || settings.isPending || baseRevision === null) {
    return <LoadingPane label="读取 Agent 设置与本机能力" rows={7} />
  }
  if (!capabilities.data || !settings.data) return null

  const saveDisabled = save.isPending || materialized.profiles === null || !dirty
  const canClear = save.isPending || settings.data.state === 'unset'
  const conflict = isApiError(save.error)
    && save.error.backendKind === 'AgentProfileSettingsConflict'

  return (
    <section className="flex flex-col gap-18 px-30 py-26">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">设置</h1>
          <div className="text-body text-ink-3">
            当前输出目录的常用 Commander 与 Executor 配置
          </div>
        </div>
        <div className="flex items-center gap-10 text-tag text-ink-4">
          <span>
            {settings.data.state === 'configured'
              ? '已配置'
              : settings.data.state === 'unreadable'
                ? '配置不可读'
                : '尚未配置'}
          </span>
          <Mono>{settings.data.revision.slice(0, 12)}</Mono>
        </div>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)_300px] items-start gap-18">
        <div className="flex min-w-0 flex-col gap-14 rounded-card border border-line-btn bg-card px-22 py-20">
          <div>
            <div className="text-h3 font-semibold">Agent 跑书常用配置</div>
            <div className="mt-5 text-body text-ink-3">
              保存后，单本与批量跑书会自动载入；确认框仍冻结并展示本次实际配置。
              已启动或已排队的 run 不会被随后修改。
            </div>
          </div>

          {settings.data.state === 'unreadable' ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              {settings.data.issue}
              <div className="mt-5 text-tag">
                当前原始 revision 已保留；重新选完并保存即可做一次受 CAS 保护的覆盖修复。
              </div>
            </div>
          ) : null}

          <PipelineAgentProfilesForm
            capabilities={capabilities.data}
            value={draft}
            onChange={replaceDraft}
            errors={materialized.errors}
            disabled={save.isPending}
          />

          {save.isError ? (
            <div role="alert" className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
              {conflict
                ? '另一个页面已经更新了常用配置；本页没有覆盖它。请重读后再修改。'
                : errorBody(save.error)}
              <ErrorHint error={save.error} />
            </div>
          ) : null}

          <div className="flex items-center gap-10 border-t border-line-soft pt-14">
            <LinkButton
              disabled={save.isPending}
              onClick={() => { void reload() }}
            >
              重读当前设置
            </LinkButton>
            <LinkButton
              disabled={canClear}
              title={settings.data.state === 'unset' ? '当前本来就没有用户常用配置' : undefined}
              onClick={() => {
                save.mutate(
                  { profiles: null, expected_revision: baseRevision },
                  {
                    onSuccess: (next) => {
                      adopt(next)
                      notify('常用 Agent 配置已清空')
                    },
                  },
                )
              }}
            >
              清空常用配置
            </LinkButton>
            <span className="ml-auto text-tag text-ink-4">
              {dirty ? '有尚未保存的改动' : '当前表单已与读取快照一致'}
            </span>
            <button
              type="button"
              disabled={saveDisabled}
              title={saveDisabled && materialized.profiles === null
                ? '请先完成全部 profile 选择并解决上方配置错误'
                : undefined}
              onClick={() => {
                if (!materialized.profiles) return
                save.mutate(
                  {
                    profiles: materialized.profiles,
                    expected_revision: baseRevision,
                  },
                  {
                    onSuccess: (next) => {
                      adopt(next)
                      notify('常用 Agent 配置已保存')
                    },
                  },
                )
              }}
              className={cx(
                'rounded-btn bg-ink px-18 py-9 text-body-lg font-semibold text-card',
                saveDisabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
              )}
            >
              {save.isPending ? '保存中…' : '保存常用配置'}
            </button>
          </div>
        </div>

        <aside className="flex flex-col gap-12 rounded-card border border-line bg-inset px-18 py-17">
          <div>
            <div className="text-body font-semibold text-ink-2">保存范围</div>
            <div className="mt-4 text-body text-ink-4">
              配置写入当前 output base 的 <Mono>_service</Mono>，不是浏览器本地状态，也不是小说产物。
            </div>
          </div>
          <div>
            <div className="text-body font-semibold text-ink-2">不会保存</div>
            <div className="mt-4 text-body text-ink-4">
              API key、CLI 登录凭据、付费授权和正文外发授权。后两项仍须每次跑书显式确认。
            </div>
          </div>
          <div>
            <div className="text-body font-semibold text-ink-2">两类 Executor</div>
            <div className="mt-4 text-body text-ink-4">
              本页管理已有供应商中立驱动的 Agent Executor（Claude Code / Codex）。
              Legacy API 当前仍是显式 DeepSeek 回滚路径；在 provider 策略、token 预算和响应契约
              完整抽象并接入前，不放一个看似可选、实际无人消费的控件。
            </div>
          </div>
          {capabilities.data.default_profiles === null ? (
            <div className="rounded-block border border-line-btn bg-card px-12 py-9 text-tag text-ink-4">
              产品默认仍为 null；这里保存的是你的常用偏好，不代表模型已经通过整书默认切换验收。
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  )
}
