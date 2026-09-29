import { useState } from 'react'
import { errorBody, isApiError } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { useNetworkSettings, useSaveNetworkSettings } from '@/hooks/useAgentRuns'
import type { NetworkPreference, NetworkSettingsView } from '@/types/agent'
import { cx } from '@/lib/cx'

/** 直连是默认；未配置与显式直连在界面上同义，只是盘上三态不同。 */
function draftOf(view: NetworkSettingsView): NetworkPreference {
  return view.network ?? { mode: 'direct', proxy_url: null }
}

export function NetworkSettingsCard() {
  const settings = useNetworkSettings()
  const save = useSaveNetworkSettings()
  const [draft, setDraft] = useState<NetworkPreference | null>(null)
  const [baseRevision, setBaseRevision] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  if (settings.isPending || !settings.data) {
    return (
      <div className="rounded-card border border-line-btn bg-card px-22 py-20 text-body text-ink-3">
        正在读取网络偏好…
      </div>
    )
  }
  const view = settings.data
  const current = draft ?? draftOf(view)
  const revision = baseRevision ?? view.revision

  const replace = (next: NetworkPreference) => {
    setDraft(next)
    setDirty(true)
  }

  const adopt = (next: NetworkSettingsView) => {
    setDraft(draftOf(next))
    setBaseRevision(next.revision)
    setDirty(false)
  }

  const conflict = isApiError(save.error)
    && save.error.backendKind === 'NetworkSettingsConflict'
  const proxyInvalid = current.mode === 'proxy' && !current.proxy_url
  const saveDisabled = save.isPending || proxyInvalid || !dirty

  return (
    <div data-testid="network-settings" className="flex flex-col gap-14 rounded-card border border-line-btn bg-card px-22 py-20">
      <div className="flex items-start justify-between gap-18">
        <div>
          <div className="text-h3 font-semibold">付费外发网络</div>
          <div className="mt-5 text-body text-ink-3">
            模型请求走直连还是走本地代理。<b>默认直连</b>：2026-09-29 实测，high effort
            的长请求经本地代理会在约 60 秒被系统性切断（一次实跑 31 次 attempt 只换来
            7 次成功），同请求直连 0.7–32.9 秒完成。
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-10 text-tag text-ink-4">
          <span>
            {view.state === 'configured'
              ? '已配置'
              : view.state === 'unreadable'
                ? '配置不可读'
                : '未配置（按直连）'}
          </span>
          <Mono>{view.revision.slice(0, 12)}</Mono>
        </div>
      </div>

      {view.state === 'unreadable' ? (
        <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
          {view.issue}
          <div className="mt-5 text-tag">
            当前按直连处置；重新选择并保存即可做一次受 CAS 保护的覆盖修复。
          </div>
        </div>
      ) : null}

      <fieldset className="flex flex-col gap-9">
        <legend className="text-tag text-ink-4">通道</legend>
        <label className="flex items-center gap-8 text-body">
          <input
            type="radio"
            name="network-mode"
            checked={current.mode === 'direct'}
            onChange={() => { replace({ mode: 'direct', proxy_url: null }) }}
          />
          直连（默认）
        </label>
        <label className="flex items-center gap-8 text-body">
          <input
            type="radio"
            name="network-mode"
            checked={current.mode === 'proxy'}
            onChange={() => {
              replace({ mode: 'proxy', proxy_url: current.proxy_url ?? '' })
            }}
          />
          走代理
        </label>
        {current.mode === 'proxy' ? (
          <label className="grid gap-4 text-tag">
            代理地址（http/https）
            <input
              className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
              placeholder="http://127.0.0.1:7890"
              value={current.proxy_url ?? ''}
              onChange={(event) => {
                replace({ mode: 'proxy', proxy_url: event.target.value })
              }}
            />
            {proxyInvalid ? (
              <span className="text-warn-ink">走代理必须填代理地址。</span>
            ) : null}
          </label>
        ) : null}
      </fieldset>

      {save.isError ? (
        <div role="alert" className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
          {conflict
            ? '另一个页面已经更新了网络偏好；本页没有覆盖它。请重读后再修改。'
            : errorBody(save.error)}
          <ErrorHint error={save.error} />
        </div>
      ) : null}

      <div className="flex items-center gap-10 border-t border-line-soft pt-14">
        <button
          type="button"
          className="rounded-btn border border-line-btn bg-card px-14 py-8 text-body text-ink-2 disabled:opacity-40"
          disabled={save.isPending}
          onClick={() => {
            void settings.refetch().then((result) => {
              if (result.data) adopt(result.data)
            })
          }}
        >
          重读当前设置
        </button>
        <button
          type="button"
          className={cx(
            'ml-auto rounded-btn bg-ink px-16 py-8 text-body-lg font-semibold text-card',
            saveDisabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
          )}
          disabled={saveDisabled}
          onClick={() => {
            save.mutate(
              { network: current, expected_revision: revision },
              { onSuccess: adopt },
            )
          }}
        >
          {save.isPending ? '保存中…' : '保存网络偏好'}
        </button>
      </div>

      <div className="text-tag text-ink-4">
        {dirty ? '有尚未保存的改动' : '网络表单已与读取快照一致'}。
        保存后在下次起跑的付费子进程生效；已启动或已排队的 run 不受影响。
        代理只作用于 DeepSeek 外发，不改变浏览器或本地 API 的网络。
      </div>
    </div>
  )
}
