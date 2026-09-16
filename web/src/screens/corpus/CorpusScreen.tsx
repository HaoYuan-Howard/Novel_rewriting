import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { Mono } from '@/components/ui/Mono'
import { LinkButton } from '@/components/ui/ActionButton'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint, NotRunYet } from '@/components/state/States'
import { FreshnessBadge, FreshnessSymbol } from '@/components/vocab/Freshness'
import { CHAPTERED_SCREENS, SPEND_INK } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { AgentRunDialog } from '@/components/agent/AgentRunDialog'
import { AgentBatchDialog } from '@/components/agent/AgentBatchDialog'
import {
  UploadAuthDialog,
  toUploadOptions,
  type UploadAuthConfirm,
} from '@/components/agent/UploadAuthDialog'
import { DeleteBookDialog } from '@/components/corpus/DeleteBookDialog'
import { MaintenanceDialog } from '@/components/corpus/MaintenanceDialog'
import { useAgentQueue } from '@/hooks/useAgentRuns'
import { cx } from '@/lib/cx'
import { useCorpusScreen, type BookView } from './useCorpusScreen'

function validateFile(file: File): string | null {
  if (!/\.(txt|md)$/i.test(file.name)) return '只接受 .txt 或 .md 原作。'
  if (file.size === 0) return '这个文件是空的，stage0 没有可扫描的正文。'
  return null
}

interface UploadedBook {
  sid: string
  title: string
  /** 与 UploadResponse.autostart 同形；仅展示是否已触发自动开跑。 */
  autostarted?: boolean
}

interface UploadFailure {
  filename: string
  message: string
}

interface UploadBatchResult {
  books: UploadedBook[]
  failures: UploadFailure[]
  selectedCount: number
}

interface AgentTarget {
  sid: string
  closeToStage0: boolean
}

interface PendingUpload {
  files: File[]
  /** 每个文件一份幂等键；网络重试复用同一批键。 */
  keys: string[]
  auth: UploadAuthConfirm
}

/**
 * 语料库：所有书的产物矩阵 + 新鲜度，顶部是「开一本新书」的上传口。
 * 这一屏没有闸门也没有校验面板 —— 它不属于任何一层。
 */
export function CorpusScreen() {
  const vm = useCorpusScreen()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<{
    done: number
    total: number
    current: string
  } | null>(null)
  const [uploadResult, setUploadResult] = useState<UploadBatchResult | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [agentTarget, setAgentTarget] = useState<AgentTarget | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [batchBooks, setBatchBooks] = useState<UploadedBook[] | null>(null)
  const [deleteBook, setDeleteBook] = useState<UploadedBook | null>(null)
  const [maintenanceOpen, setMaintenanceOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [authOpen, setAuthOpen] = useState(false)
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const queue = useAgentQueue()
  const { defaultOrdinal } = useSampleCoverage()

  // sid / 书名子串过滤（大小写不敏感；空串显示全部）
  const needle = filter.trim().toLowerCase()
  const books = needle
    ? vm.books.filter(
        (b) => b.sid.toLowerCase().includes(needle) || b.title.toLowerCase().includes(needle),
      )
    : vm.books

  // 「打开」落到第一个「待继续」层的屏；带章号的屏拼默认章号
  const openPath = (b: BookView) =>
    '/n/' +
    b.sid +
    '/' +
    b.openScreen +
    (CHAPTERED_SCREENS.has(b.openScreen) ? '/' + defaultOrdinal(b.sid) : '')

  // ⚠ 顺序取自 picked 而不是 vm.books：勾选顺序就是排队顺序，
  //   人先勾的那本先跑，这是界面上唯一能表达优先级的地方。
  const pickedBooks = picked
    .map((sid) => vm.books.find((b) => b.sid === sid))
    .filter((b): b is BookView => !!b)
    .map((b) => ({ sid: b.sid, title: b.title }))

  const togglePick = (sid: string) =>
    setPicked((cur) => (cur.includes(sid) ? cur.filter((s) => s !== sid) : [...cur, sid]))

  const chooseFiles = (next: File[]) => {
    if (!next.length) return
    const problems = next
      .map((file) => ({ file, problem: validateFile(file) }))
      .filter((item): item is { file: File; problem: string } => !!item.problem)
    setUploadResult(null)
    if (problems.length) {
      setFileError(
        problems
          .slice(0, 3)
          .map(({ file, problem }) => file.name + '：' + problem)
          .join('；') + (problems.length > 3 ? `；另有 ${problems.length - 3} 本不合要求` : ''),
      )
      setFiles([])
      return
    }
    setFileError(null)
    setFiles(next)
  }

  const upload = useMutation({
    mutationFn: async (pending: PendingUpload) => {
      const selected = pending.files
      const books: UploadedBook[] = []
      const failures: UploadFailure[] = []
      setUploadProgress({ done: 0, total: selected.length, current: selected[0]?.name ?? '' })

      // 逐本上传，不把 N 本原始字节同时塞进浏览器和服务端内存；失败不回滚成功项。
      for (let index = 0; index < selected.length; index += 1) {
        const current = selected[index]
        const key = pending.keys[index]
        if (!current || !key) continue
        setUploadProgress({ done: index, total: selected.length, current: current.name })
        try {
          const result = await api.uploadCorpus(current, toUploadOptions(pending.auth, key))
          if (result.job.state !== 'ok') {
            throw new Error(
              'Stage 0 扫描没有完成：' + result.job.state + '。请查看这本书的作业日志。',
            )
          }
          if (!books.some((book) => book.sid === result.sid)) {
            books.push({
              sid: result.sid,
              title: current.name,
              autostarted:
                result.autostart === 'started' ||
                result.autostart === 'replay' ||
                Boolean(result.run),
            })
          }
        } catch (error) {
          failures.push({ filename: current.name, message: errorBody(error) })
        }
        setUploadProgress({ done: index + 1, total: selected.length, current: current.name })
      }
      return { books, failures, selectedCount: selected.length }
    },
    onSuccess: (result) => {
      setUploadProgress(null)
      setUploadResult(result)
      setPendingUpload(null)
      setAuthOpen(false)
      void qc.invalidateQueries({ queryKey: qk.novels() })
      result.books.forEach(({ sid }) => {
        void qc.invalidateQueries({ queryKey: qk.stage0Report(sid) })
        void qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) })
        void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
        void qc.invalidateQueries({ queryKey: qk.agentRuns(sid) })
      })
      setFiles([])
      if (input.current) input.current.value = ''

      if (result.selectedCount === 1 && result.books.length === 1 && !result.failures.length) {
        nav('/n/' + result.books[0]!.sid + '/run')
        return
      }

      setPicked(result.books.map((book) => book.sid))
      // 批量：已带授权自动开跑，不再弹 AgentBatchDialog；失败项保留供核对。
      if (result.books.length && result.failures.length) {
        // 部分失败：已成功项已开跑，不强制跳转
      }
    },
    onError: () => setUploadProgress(null),
  })

  const beginUpload = () => {
    if (!files.length) return
    setAuthOpen(true)
  }

  const confirmUpload = (auth: UploadAuthConfirm) => {
    const selected = [...files]
    const keys = pendingUpload && pendingUpload.files.length === selected.length
      && pendingUpload.files.every((file, i) => file === selected[i])
      ? pendingUpload.keys
      : selected.map(() => crypto.randomUUID())
    const pending: PendingUpload = { files: selected, keys, auth }
    setPendingUpload(pending)
    upload.mutate(pending)
  }

  const showUpload =
    uploadOpen || files.length > 0 || upload.isPending || !!uploadResult ||
    (vm.query.isSuccess && vm.books.length === 0)

  return (
    <section className="flex flex-col gap-18 px-30 py-26">
      <header data-testid="corpus-header" className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">语料库</h1>
          <Mono className="text-tag text-ink-4">{vm.base}</Mono>
        </div>
        <div className="flex items-center gap-10">
          <LinkButton onClick={() => setMaintenanceOpen(true)}>维护</LinkButton>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="搜索书名或 sid"
            aria-label="按 sid 或书名过滤"
            className="w-[220px] rounded-btn border border-line-btn bg-card px-10 py-6 text-body text-ink"
          />
          <span className="text-body text-ink-3">
            {vm.countLabel ?? (vm.query.isError ? '取不到，数不出来' : '读取中 …')}
          </span>
          {vm.books.length > 0 && showUpload && !upload.isPending ? (
            <>
              <span aria-hidden className="h-14 w-1 bg-line-btn" />
              <LinkButton
                onClick={() => {
                  if (files.length || uploadResult) {
                    setFiles([])
                    setFileError(null)
                    setUploadResult(null)
                    if (input.current) input.current.value = ''
                  }
                  setUploadOpen(false)
                }}
              >
                {files.length ? '取消上传' : '收起上传'}
              </LinkButton>
            </>
          ) : null}
        </div>
      </header>

      {/* 上传口 */}
      {showUpload ? (
        <div className="overflow-hidden rounded-card border border-line-btn bg-card">
          <div className="flex flex-col gap-12 px-22 py-18">
            <div className="text-h3 font-semibold">开一本或一批新书</div>
            <label
              className={cx(
                'flex cursor-pointer items-center gap-14 rounded-block border-[1.5px] border-dashed px-16 py-14 transition-colors focus-within:ring-3 focus-within:ring-accent-ring',
                dragging
                  ? 'border-accent-line-2 bg-accent-bg'
                  : 'border-ink-7 bg-sunken-2 hover:border-ink-4',
              )}
              onDragEnter={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={(event) => {
                // 划过子元素也会触发 leave —— relatedTarget 还在容器内就不算离开
                if (
                  event.relatedTarget instanceof Node &&
                  event.currentTarget.contains(event.relatedTarget)
                )
                  return
                setDragging(false)
              }}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                chooseFiles(Array.from(event.dataTransfer.files ?? []))
              }}
            >
              <span className="text-h2 text-ink-5">＋</span>
              <span className="flex min-w-0 flex-col gap-3">
                <span className="truncate text-body-lg">
                  {dragging
                    ? '松手选择这些原作'
                    : files.length === 1
                      ? files[0]!.name
                      : files.length > 1
                        ? `已选择 ${files.length} 本原作`
                        : '选择或拖入一本或多本 .txt / .md 原作'}
                </span>
                {files.length ? (
                  <span className="text-tag text-ink-4">
                    {Math.round(files.reduce((sum, file) => sum + file.size, 0) / 1024) +
                      ' KB 合计 · 逐本探测编码与扫描番外'}
                  </span>
                ) : null}
              </span>
              <input
                ref={input}
                type="file"
                multiple
                accept={'.txt,.md'}
                className="sr-only"
                onChange={(e) => {
                  chooseFiles(Array.from(e.target.files ?? []))
                }}
              />
              <span
                className="ml-auto flex-none rounded-btn border border-line-btn bg-card px-12 py-6 text-body"
                onClick={(e) => {
                  e.preventDefault()
                  input.current?.click()
                }}
              >
                选一本或多本
              </span>
            </label>
            {files.length > 1 ? (
              <div className="max-h-[132px] overflow-y-auto rounded-btn border border-line-soft bg-inset px-12 py-7">
                {files.map((selected) => (
                  <div key={selected.name + ':' + selected.size} className="flex gap-10 py-2">
                    <span className="min-w-0 flex-1 truncate text-body text-ink-2">
                      {selected.name}
                    </span>
                    <Mono className="text-mini text-ink-5">
                      {Math.max(1, Math.round(selected.size / 1024))} KB
                    </Mono>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="flex items-center gap-12">
              <button
                type="button"
                disabled={!files.length || upload.isPending}
                onClick={beginUpload}
                className={
                  'flex-none whitespace-nowrap rounded-btn border-none bg-ink px-18 py-9 text-body-lg font-semibold text-card ' +
                  (!files.length || upload.isPending
                    ? 'cursor-not-allowed opacity-40'
                    : 'cursor-pointer')
                }
              >
                {upload.isPending
                  ? `上传并开跑 ${uploadProgress?.done ?? 0}/${uploadProgress?.total ?? files.length}`
                  : files.length > 1
                    ? `授权后上传这 ${files.length} 本`
                    : files.length === 1
                      ? '授权后上传并自动开跑'
                      : '先选择文件'}
              </button>
              <span className="text-tag text-ink-4">
                {upload.isPending
                  ? `正在处理：${uploadProgress?.current ?? '…'}`
                  : files.length > 1
                    ? '逐本顺序上传；每本独立幂等键，同一批共用授权与 profiles。'
                    : '上传前确认授权与 profiles；成功后自动开跑并进入进度屏。'}
              </span>
            </div>
            {fileError || upload.isError ? (
              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                {fileError ?? (
                  <>
                    {errorBody(upload.error)}
                    <ErrorHint error={upload.error} />
                  </>
                )}
              </div>
            ) : null}
            {uploadResult ? (
              <div
                className={cx(
                  'flex flex-col gap-8 rounded-block border px-13 py-10',
                  uploadResult.failures.length
                    ? 'border-warn-line bg-warn-bg'
                    : 'border-gate-ok-line bg-gate-ok-bg',
                )}
              >
                <div className="text-body font-semibold text-ink-2">
                  上传：成功 {uploadResult.books.length} 本
                  {uploadResult.failures.length
                    ? ` · 失败 ${uploadResult.failures.length} 本`
                    : ' · 全部完成'}
                </div>
                {uploadResult.failures.map((failure) => (
                  <div key={failure.filename} className="text-tag text-warn-ink">
                    <span className="font-semibold">{failure.filename}</span>：{failure.message}
                  </div>
                ))}
                {uploadResult.books.length ? (
                  <div className="flex flex-wrap items-center gap-9">
                    <span className="text-tag text-ink-4">
                      成功项已按授权自动开跑；可打开进度屏查看，Stage 页仅只读监控。
                    </span>
                    {uploadResult.books.length === 1 ? (
                      <button
                        type="button"
                        onClick={() => nav('/n/' + uploadResult.books[0]!.sid + '/run')}
                        className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-13 py-7 text-body font-semibold"
                      >
                        打开进度屏
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setUploadOpen(true)}
          className="flex cursor-pointer items-center gap-10 rounded-card border border-dashed border-line-btn bg-sunken-2 px-18 py-11 text-left hover:border-ink-6"
        >
          <span className="text-h3 text-accent">＋</span>
          <span className="text-body-lg font-semibold">开一本或一批新书</span>
          <span className="ml-auto text-body text-ink-4">展开</span>
        </button>
      )}

      {/* 图例：形状本身在说话，不是只有颜色 */}
      <div className="flex flex-wrap items-center gap-18 px-2">
        {vm.legend.map((l) => (
          <span key={l.f} className="inline-flex items-baseline gap-6">
            <FreshnessBadge f={l.f} />
            <span className="text-micro text-ink-5">{l.desc}</span>
          </span>
        ))}
        <span className="ml-auto text-tag text-ink-4">
          Agent 并发上限 {queue.data?.max_concurrent ?? '…'} 本
        </span>
      </div>

      <QueryStates q={vm.query} loadingLabel="读语料库">
        {() =>
          vm.books.length === 0 ? (
            <NotRunYet
              title="一本书都还没有"
              body="这台机器上的 Output 目录里没有任何一本书。把一个 .txt 丢进上面的上传口，确认授权与 profiles 后上传会自动开跑；Stage 页用于只读监控，Stage6 可改选候选。"
              action={<LinkButton onClick={() => setUploadOpen(true)}>选择第一本原作</LinkButton>}
            />
          ) : (
            <div className="flex flex-col gap-14">
              {pickedBooks.length > 0 ? (
                <BatchBar
                  picked={pickedBooks.length}
                  onClear={() => setPicked([])}
                  onRun={() => setBatchBooks(pickedBooks)}
                />
              ) : null}
              {books.length === 0 ? (
                <div className="prose-cn px-2 text-body text-ink-4">
                  没有匹配「{filter.trim()}」的书 —— 清空过滤框回到完整列表。
                </div>
              ) : null}
              {books.map((b) => (
                <BookCard
                  key={b.sid}
                  b={b}
                  picked={picked.includes(b.sid)}
                  onPick={() => togglePick(b.sid)}
                  onOpen={() => nav(openPath(b))}
                  onAgent={() => setAgentTarget({ sid: b.sid, closeToStage0: false })}
                  onDelete={() => setDeleteBook({ sid: b.sid, title: b.title })}
                />
              ))}
            </div>
          )
        }
      </QueryStates>

      {authOpen ? (
        <UploadAuthDialog
          fileCount={files.length || pendingUpload?.files.length || 0}
          pending={upload.isPending}
          error={upload.error}
          onClose={() => {
            if (upload.isPending) return
            setAuthOpen(false)
          }}
          onConfirm={confirmUpload}
        />
      ) : null}

      {agentTarget ? (
        <AgentRunDialog
          sid={agentTarget.sid}
          onClose={() => {
            const target = agentTarget
            setAgentTarget(null)
            if (target.closeToStage0) nav('/n/' + target.sid + '/stage0')
          }}
          onStarted={(sid) => {
            setAgentTarget(null)
            nav('/n/' + sid + '/run')
          }}
        />
      ) : null}

      {batchBooks ? (
        <AgentBatchDialog
          books={batchBooks}
          onClose={() => {
            setBatchBooks(null)
            setPicked([])
          }}
        />
      ) : null}

      {deleteBook ? (
        <DeleteBookDialog
          book={deleteBook}
          onClose={() => setDeleteBook(null)}
          onDeleted={(result) => {
            setPicked((current) => current.filter((sid) => sid !== result.sid))
            void qc.invalidateQueries({ queryKey: qk.novels() })
          }}
        />
      ) : null}

      {maintenanceOpen ? (
        <MaintenanceDialog
          onClose={() => setMaintenanceOpen(false)}
          onRestored={() => {
            void qc.invalidateQueries({ queryKey: qk.novels() })
          }}
        />
      ) : null}
    </section>
  )
}

/**
 * 批量开跑的操作条。**只在勾了书之后出现**；并发上限常驻在图例行。
 *
 * ⚠ 并发上限来自服务端 `/api/agent/runs/queue`，界面不自己数、不写死。
 */
function BatchBar({
  picked,
  onClear,
  onRun,
}: {
  picked: number
  onClear: () => void
  onRun: () => void
}) {
  return (
    <div className="flex items-center gap-12 rounded-card border border-line bg-sunken-2 px-18 py-10">
      <span className="text-body text-ink-2">
        已勾 <span className="font-semibold">{picked}</span> 本
        <span className="text-ink-5">（勾选顺序就是排队顺序）</span>
      </span>
      <button
        type="button"
        onClick={onClear}
        className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-13 py-6 text-body text-ink-2"
      >
        清空
      </button>
      <button
        type="button"
        onClick={onRun}
        title="一次交 N 本：闸内的立刻跑，其余排队，一本结束自动带起下一本"
        className="cursor-pointer rounded-btn border border-line-btn bg-card px-13 py-6 text-body font-semibold"
        style={{ color: SPEND_INK }}
      >
        开跑这 {picked} 本
      </button>
    </div>
  )
}

function stageState(done: boolean | null) {
  if (done === true) return { mark: '✓', label: '已完成' }
  if (done === false) return { mark: '○', label: '待继续' }
  return { mark: '?', label: '判不了' }
}

function BookCard({
  b,
  picked,
  onPick,
  onOpen,
  onAgent,
  onDelete,
}: {
  b: BookView
  picked: boolean
  onPick: () => void
  onOpen: () => void
  onAgent: () => void
  onDelete: () => void
}) {
  return (
    <article className="overflow-hidden rounded-card border border-line bg-card">
      <div
        className="grid border-b border-line-soft"
        style={{ gridTemplateColumns: 'minmax(280px,1.1fr) minmax(210px,270px) 128px' }}
      >
        <div className="flex flex-col gap-6 px-18 py-14">
          <div className="flex items-center gap-10">
            <input
              type="checkbox"
              checked={picked}
              onChange={onPick}
              title="勾上它，和别的书一起交给 agent 批量跑"
              className="cursor-pointer"
            />
            <button
              type="button"
              onClick={onOpen}
              className="cursor-pointer border-none bg-transparent p-0 text-left text-h2 font-semibold text-ink hover:text-accent"
            >
              {b.title} <span aria-hidden>→</span>
            </button>
          </div>
          <div className="flex items-center gap-10">
            <Mono className="text-mini text-ink-4">{b.sid}</Mono>
            <span className="text-tag text-ink-4">{b.chaptersLabel}</span>
          </div>
        </div>
        <div className="flex flex-col items-end justify-center gap-5 border-l border-line-soft px-18 py-14">
          <span
            className="rounded-chip px-9 py-3 text-body font-semibold"
            style={{ color: b.verdictColor, background: b.verdictBg }}
          >
            {b.verdict}
          </span>
          <span className="text-micro text-ink-5">
            {b.mtimeLabel ?? '没有任何产物落过盘'}
          </span>
        </div>
        <div className="flex items-center justify-center gap-7 border-l border-line-soft bg-sunken-2 px-10">
          <button
            type="button"
            onClick={onAgent}
            title="让 agent 按操作规程代跑这本书（先 0 成本 preview，授权在确认框里勾）"
            className="cursor-pointer rounded-btn border border-line-btn bg-card px-11 py-7 text-body font-semibold hover:border-accent-line-2"
            style={{ color: SPEND_INK }}
          >
            Agent
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={'删除《' + b.title + '》项目'}
            title="把整本项目移入可恢复回收站"
            className="cursor-pointer border-none bg-transparent px-4 py-7 text-tag text-warn-ink hover:underline"
          >
            删除
          </button>
        </div>
      </div>

      <div
        className="grid gap-1 bg-line-soft"
        style={{ gridTemplateColumns: 'repeat(8,minmax(0,1fr))' }}
      >
        {b.columns.map((c) => (
          <div
            key={c.key}
            className={
              'flex flex-col gap-6 px-12 pt-10 pb-12 ' + (c.isStage5 ? 'bg-sunken' : 'bg-card')
            }
          >
            <div className="flex items-baseline gap-6">
              <Mono className="text-mini text-ink-5">{c.n}</Mono>
              <span className="text-tag font-semibold text-ink-3">{c.label}</span>
              <span
                data-testid={'stage-state-' + c.key}
                role="img"
                aria-label={c.label + '：' + stageState(c.done).label}
                title={stageState(c.done).label}
                className={cx(
                  'ml-auto text-tag font-semibold',
                  c.done === true
                    ? 'text-gate-ok-ink'
                    : c.done === false
                      ? 'text-ink-5'
                      : 'text-warn-ink',
                )}
              >
                {stageState(c.done).mark}
              </span>
            </div>

            {c.isStage5 ? (
              <Mono className="rounded-tag border border-line-2 bg-card px-7 py-3 text-mini text-ink-3">
                canonical {c.chaptersDone} 章
              </Mono>
            ) : null}
            {c.items.length ? (
              c.items.map((a) => (
                <div
                  key={a.name}
                  data-testid={'artifact-' + c.key + '-' + a.name}
                  className="flex min-w-0 items-center gap-6 rounded-btn border border-line-2 bg-card px-8 py-7"
                >
                  <FreshnessSymbol f={a.freshness} />
                  <Mono className="min-w-0 flex-1 truncate text-tag" title={a.name}>
                    {a.name}
                  </Mono>
                  {a.issue ? (
                    <span
                      role="img"
                      aria-label={a.issue}
                      title={a.issue}
                      className="flex-none text-tag font-semibold text-warn-ink"
                    >
                      !
                    </span>
                  ) : null}
                  <Mono className="flex-none text-micro text-ink-5">{a.size}</Mono>
                </div>
              ))
            ) : (
              <span className="text-micro text-ink-5">这一层还没有产物</span>
            )}
          </div>
        ))}
      </div>
    </article>
  )
}
