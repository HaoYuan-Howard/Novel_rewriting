import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { SCRIM } from '@/constants/vocabulary'
import type { TrashNovelResult } from '@/types/novels'

export function DeleteBookDialog({
  book,
  onClose,
  onDeleted,
}: {
  book: { sid: string; title: string }
  onClose: () => void
  onDeleted: (result: TrashNovelResult) => void
}) {
  const [confirmation, setConfirmation] = useState('')
  const [result, setResult] = useState<TrashNovelResult | null>(null)
  const remove = useMutation({
    mutationFn: () => api.trashNovel(book.sid, book.title),
    onSuccess: (next) => {
      setResult(next)
      onDeleted(next)
    },
  })
  const confirmed = confirmation === book.sid
  // ⚠ **请求在途时不许关框。** `api.trashNovel` 发出去就取消不掉：这时点「取消」
  // 或点遮罩，界面上像是撤销了，盘上那本书仍会被移入回收站，而恢复位置
  // （`trash_rel`，唯一一处告诉人「去哪儿找回来」的地方）再也不会显示。
  const closeIfIdle = () => {
    if (remove.isPending) return
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center px-16 py-16"
      style={{ background: SCRIM }}
      onClick={closeIfIdle}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="删除项目"
        className="flex max-h-[calc(100vh-32px)] w-[620px] flex-col overflow-hidden rounded-modal border border-warn-line bg-card shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="shrink-0 border-b border-warn-line bg-warn-bg px-22 py-16">
          <div className="text-h3 font-semibold text-warn-ink">
            {result ? '项目已移入回收站' : '删除项目'}
          </div>
          <div className="mt-4 text-body text-ink-3">
            《{book.title}》 · <Mono className="text-mini">{book.sid}</Mono>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-22 py-18">
          {result ? (
            <div className="flex flex-col gap-10">
              <div className="rounded-block border border-gate-ok-line bg-gate-ok-bg px-14 py-11 text-body text-gate-ok-ink">
                这本书已从语料库移走，没有永久销毁。
              </div>
              <div className="text-body text-ink-3">恢复位置</div>
              <Mono className="break-all rounded-btn border border-line bg-inset px-12 py-9 text-tag text-ink-2">
                {result.trash_rel}
              </Mono>
            </div>
          ) : (
            <div className="flex flex-col gap-13">
              <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-11 text-body text-warn-ink">
                整本项目目录和该书审批状态会移入 Output/_service/trash/。付费账、作业审计、
                agent 记录和内容地址上传会保留；正在运行或排队的书会拒绝删除。
              </div>
              <label className="flex flex-col gap-6 text-body text-ink-2">
                <span>
                  输入完整 sid <Mono className="text-mini">{book.sid}</Mono> 确认目标
                </span>
                <input
                  autoFocus
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  aria-label="输入 sid 确认删除"
                  className="rounded-btn border border-line-btn bg-card px-11 py-8 font-mono text-body text-ink outline-none focus:border-warn"
                />
              </label>
              {remove.isError ? (
                <div className="rounded-block border border-warn-line bg-warn-bg px-14 py-10 text-body text-warn-ink">
                  {errorBody(remove.error)}
                  <ErrorHint error={remove.error} />
                </div>
              ) : null}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-10 border-t border-line-soft bg-sunken-2 px-22 py-13">
          <span className="text-tag text-ink-4">
            {remove.isPending
              ? '请求已经发出，取消不掉了——等它回来再关。'
              : result
                ? '语料库数据已自动刷新。'
                : '这是项目级操作，不是清空某一层。'}
          </span>
          <button
            type="button"
            onClick={closeIfIdle}
            disabled={remove.isPending}
            className={
              'ml-auto rounded-btn border border-line-btn bg-card px-16 py-8 text-body-lg ' +
              (remove.isPending ? 'cursor-not-allowed opacity-40' : 'cursor-pointer')
            }
          >
            {result ? '关闭' : '取消'}
          </button>
          {result ? null : (
            <button
              type="button"
              disabled={!confirmed || remove.isPending}
              onClick={() => remove.mutate()}
              className={
                'rounded-btn border border-warn-line bg-warn px-18 py-8 text-body-lg font-semibold text-white ' +
                (!confirmed || remove.isPending
                  ? 'cursor-not-allowed opacity-40'
                  : 'cursor-pointer')
              }
            >
              {remove.isPending ? '正在移入回收站…' : '移入回收站'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
