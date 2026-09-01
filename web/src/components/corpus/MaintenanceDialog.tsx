import { useState } from 'react'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { SCRIM } from '@/constants/vocabulary'
import { useMaintenance } from '@/hooks/useMaintenance'
import type { RetentionPolicy } from '@/types/maintenance'

const EMPTY_POLICY: RetentionPolicy = {
  log_ttl_seconds: null,
  telemetry_ttl_seconds: null,
  trash_ttl_seconds: null,
  checkpoint_ttl_seconds: null,
  max_entries: 512,
}

function ttl(value: string): number | null {
  if (!value.trim()) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
      {errorBody(error)}
      <ErrorHint error={error} />
    </div>
  )
}

export function MaintenanceDialog({
  onClose,
  onRestored,
}: {
  onClose: () => void
  onRestored: () => void
}) {
  const maintenance = useMaintenance()
  const [policy, setPolicy] = useState(EMPTY_POLICY)
  const [retentionConfirmed, setRetentionConfirmed] = useState(false)
  const [trashId, setTrashId] = useState('')
  const [expectedTitle, setExpectedTitle] = useState('')
  const [restoreConfirmed, setRestoreConfirmed] = useState(false)
  const busy = maintenance.retentionApply.isPending || maintenance.restoreApply.isPending

  const invalidateRetention = () => {
    maintenance.retentionPreview.reset()
    maintenance.retentionApply.reset()
    setRetentionConfirmed(false)
  }
  const changePolicy = (
    key: Exclude<keyof RetentionPolicy, 'max_entries'>,
    value: number | null,
  ) => {
    invalidateRetention()
    setPolicy((current) => ({ ...current, [key]: value }))
  }
  const resetRestorePreview = () => {
    maintenance.restorePreview.reset()
    maintenance.restoreApply.reset()
    setRestoreConfirmed(false)
  }

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center px-16 py-16"
      style={{ background: SCRIM }}
      onClick={() => {
        if (!busy) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="服务维护"
        className="flex max-h-[calc(100vh-32px)] w-[780px] flex-col overflow-hidden rounded-modal border border-line bg-card shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-line px-22 py-16">
          <div className="text-h3 font-semibold">服务维护</div>
          <div className="mt-4 text-body text-ink-4">
            两项操作都先只读 preview；apply 只接受刚才返回的完整 token/CAS。
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-18 overflow-y-auto px-22 py-18">
          <section className="flex flex-col gap-10 rounded-card border border-line-soft p-16">
            <div>
              <div className="text-body-lg font-semibold">Retention 清理</div>
              <div className="text-tag text-ink-4">留空表示关闭该类 TTL，不是立即删除。</div>
            </div>
            <div className="grid grid-cols-2 gap-9">
              {([
                ['log_ttl_seconds', '日志 TTL（秒）'],
                ['telemetry_ttl_seconds', 'operator telemetry TTL（秒）'],
                ['trash_ttl_seconds', '完整回收站 TTL（秒）'],
                ['checkpoint_ttl_seconds', '已交付 checkpoint TTL（秒）'],
              ] as const).map(([key, label]) => (
                <label key={key} className="flex flex-col gap-4 text-tag text-ink-3">
                  {label}
                  <input
                    type="number"
                    min={1}
                    value={policy[key] ?? ''}
                    onChange={(event) => changePolicy(key, ttl(event.target.value))}
                    className="rounded-btn border border-line-btn bg-card px-10 py-7 text-body text-ink"
                  />
                </label>
              ))}
            </div>
            <label className="flex items-center gap-8 text-tag text-ink-3">
              扫描上限
              <input
                type="number"
                min={1}
                value={policy.max_entries}
                onChange={(event) => {
                  invalidateRetention()
                  setPolicy((current) => ({
                    ...current,
                    max_entries: Math.max(1, Number(event.target.value) || 1),
                  }))
                }}
                className="w-28 rounded-btn border border-line-btn bg-card px-10 py-7 text-body text-ink"
              />
            </label>
            <button
              type="button"
              disabled={maintenance.retentionPreview.isPending}
              onClick={() => maintenance.retentionPreview.mutate(policy)}
              className="w-fit cursor-pointer rounded-btn border border-line-btn bg-card px-14 py-7 text-body font-semibold"
            >
              {maintenance.retentionPreview.isPending ? '正在预览…' : '预览 retention 计划'}
            </button>
            {maintenance.retentionPreview.isError ? (
              <ErrorBox error={maintenance.retentionPreview.error} />
            ) : null}
            {maintenance.retentionPreview.data ? (
              <div className="flex flex-col gap-8 rounded-block border border-line bg-inset px-13 py-10">
                <div className="text-body text-ink-2">
                  扫描 {maintenance.retentionPreview.data.examined} 项，候选{' '}
                  <strong>{maintenance.retentionPreview.data.candidates.length}</strong> 项
                </div>
                <Mono className="break-all text-micro text-ink-5">
                  token {maintenance.retentionPreview.data.plan_token}
                </Mono>
                <div className="max-h-32 overflow-y-auto text-tag text-ink-3">
                  {maintenance.retentionPreview.data.candidates.length
                    ? maintenance.retentionPreview.data.candidates.map((item) => (
                        <div key={item.kind + ':' + item.rel}>
                          {item.kind} · {item.rel} · {item.observed_size} bytes
                        </div>
                      ))
                    : '没有候选；apply 将保持空操作。'}
                </div>
                <label className="flex items-center gap-7 text-body text-ink-2">
                  <input
                    type="checkbox"
                    checked={retentionConfirmed}
                    onChange={(event) => setRetentionConfirmed(event.target.checked)}
                  />
                  我已核对这份计划，并确认 apply
                </label>
                <button
                  type="button"
                  disabled={!retentionConfirmed || maintenance.retentionApply.isPending}
                  onClick={() =>
                    maintenance.retentionApply.mutate(maintenance.retentionPreview.data!)
                  }
                  className="w-fit rounded-btn border border-warn-line bg-warn px-14 py-7 text-body font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {maintenance.retentionApply.isPending ? '正在应用…' : '应用这份 retention 计划'}
                </button>
              </div>
            ) : null}
            {maintenance.retentionApply.isError ? (
              <ErrorBox error={maintenance.retentionApply.error} />
            ) : null}
            {maintenance.retentionApply.data ? (
              <div className="text-body text-gate-ok-ink">
                已移除 {maintenance.retentionApply.data.removed.length} 项；receipt{' '}
                {maintenance.retentionApply.data.receipts.length} 份。
              </div>
            ) : null}
          </section>

          <section className="flex flex-col gap-10 rounded-card border border-line-soft p-16">
            <div>
              <div className="text-body-lg font-semibold">从回收站恢复项目</div>
              <div className="text-tag text-ink-4">
                book、approval 与该 sid 的 service run 一起恢复；真实移动由受控 CLI 子进程执行。
              </div>
            </div>
            <div className="grid grid-cols-2 gap-9">
              <label className="flex flex-col gap-4 text-tag text-ink-3">
                trash id
                <input
                  value={trashId}
                  onChange={(event) => {
                    resetRestorePreview()
                    setTrashId(event.target.value)
                  }}
                  placeholder="1700000000-deadbeef"
                  className="rounded-btn border border-line-btn bg-card px-10 py-7 font-mono text-body text-ink"
                />
              </label>
              <label className="flex flex-col gap-4 text-tag text-ink-3">
                完整书名确认
                <input
                  value={expectedTitle}
                  onChange={(event) => {
                    resetRestorePreview()
                    setExpectedTitle(event.target.value)
                  }}
                  className="rounded-btn border border-line-btn bg-card px-10 py-7 text-body text-ink"
                />
              </label>
            </div>
            <button
              type="button"
              disabled={
                !trashId || !expectedTitle || maintenance.restorePreview.isPending
              }
              onClick={() =>
                maintenance.restorePreview.mutate({ trashId, expectedTitle })
              }
              className="w-fit rounded-btn border border-line-btn bg-card px-14 py-7 text-body font-semibold disabled:cursor-not-allowed disabled:opacity-40"
            >
              {maintenance.restorePreview.isPending ? '正在预览…' : '预览恢复'}
            </button>
            {maintenance.restorePreview.isError ? (
              <ErrorBox error={maintenance.restorePreview.error} />
            ) : null}
            {maintenance.restorePreview.data ? (
              <div className="flex flex-col gap-8 rounded-block border border-line bg-inset px-13 py-10">
                <div className="text-body text-ink-2">
                  《{maintenance.restorePreview.data.title}》 · sid{' '}
                  <Mono>{maintenance.restorePreview.data.sid}</Mono>
                </div>
                <Mono className="break-all text-micro text-ink-5">
                  token {maintenance.restorePreview.data.plan_token}
                </Mono>
                <label className="flex items-center gap-7 text-body text-ink-2">
                  <input
                    type="checkbox"
                    checked={restoreConfirmed}
                    onChange={(event) => setRestoreConfirmed(event.target.checked)}
                  />
                  我已核对 trash id、书名与 sid，并确认恢复
                </label>
                <button
                  type="button"
                  disabled={!restoreConfirmed || maintenance.restoreApply.isPending}
                  onClick={() =>
                    maintenance.restoreApply.mutate(maintenance.restorePreview.data!, {
                      onSuccess: onRestored,
                    })
                  }
                  className="w-fit rounded-btn border border-line-btn bg-ink px-14 py-7 text-body font-semibold text-card disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {maintenance.restoreApply.isPending ? '正在恢复…' : '应用这份恢复计划'}
                </button>
              </div>
            ) : null}
            {maintenance.restoreApply.isError ? (
              <ErrorBox error={maintenance.restoreApply.error} />
            ) : null}
            {maintenance.restoreApply.data ? (
              <div className="text-body text-gate-ok-ink">
                已恢复到 {maintenance.restoreApply.data.book_rel}
              </div>
            ) : null}
          </section>
        </div>

        <div className="flex justify-end border-t border-line-soft bg-sunken-2 px-22 py-13">
          <button
            type="button"
            disabled={busy}
            onClick={onClose}
            className="rounded-btn border border-line-btn bg-card px-16 py-8 text-body disabled:cursor-not-allowed disabled:opacity-40"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
