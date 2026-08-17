import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { Mono } from '@/components/ui/Mono'
import { LinkButton } from '@/components/ui/ActionButton'
import { QueryStates } from '@/components/state/QueryStates'
import { NotRunYet } from '@/components/state/States'
import { FreshnessBadge, FreshnessDot, FreshnessTag } from '@/components/vocab/Freshness'
import { CHAPTERED_SCREENS, SPEND_INK } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { AgentRunDialog } from '@/components/agent/AgentRunDialog'
import { AgentBatchDialog } from '@/components/agent/AgentBatchDialog'
import { useAgentQueue } from '@/hooks/useAgentRuns'
import { cx } from '@/lib/cx'
import { useCorpusScreen, type BookView } from './useCorpusScreen'

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

function validateFile(file: File): string | null {
  if (!/\.(txt|md)$/i.test(file.name)) return '只接受 .txt 或 .md 原作。'
  if (file.size > MAX_UPLOAD_BYTES) return '文件超过 20 MB，请先拆分或压缩正文。'
  if (file.size === 0) return '这个文件是空的，stage0 没有可扫描的正文。'
  return null
}

/**
 * 语料库：所有书的产物矩阵 + 新鲜度，顶部是「开一本新书」的上传口。
 * 这一屏没有闸门也没有校验面板 —— 它不属于任何一层。
 */
export function CorpusScreen() {
  const vm = useCorpusScreen()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [agentSid, setAgentSid] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [filter, setFilter] = useState('')
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

  const chooseFile = (next: File | null) => {
    if (!next) return
    const problem = validateFile(next)
    setFileError(problem)
    setFile(problem ? null : next)
  }

  const upload = useMutation({
    mutationFn: async () => {
      const result = await api.uploadCorpus(file!)
      if (result.job.state !== 'ok') {
        throw new Error('Stage 0 扫描没有完成：' + result.job.state + '。请查看作业日志。')
      }
      return result
    },
    onSuccess: ({ sid }) => {
      void qc.invalidateQueries({ queryKey: qk.novels() })
      void qc.invalidateQueries({ queryKey: qk.stage0Report(sid) })
      void qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
      nav('/n/' + sid + '/stage0')
    },
  })

  const showUpload =
    uploadOpen || !!file || upload.isPending || (vm.query.isSuccess && vm.books.length === 0)

  return (
    <section className="flex flex-col gap-18 px-30 py-26">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">语料库</h1>
          <Mono className="text-tag text-ink-4">{vm.base}</Mono>
        </div>
        <div className="flex items-center gap-12">
          <span className="text-body text-ink-3">
            {vm.countLabel ?? (vm.query.isError ? '取不到，数不出来' : '读取中 …')}
          </span>
          <span aria-hidden className="h-14 w-1 bg-line-btn" />
          <LinkButton onClick={() => void vm.query.refetch()}>重新读取</LinkButton>
          {vm.books.length > 0 && showUpload && !upload.isPending ? (
            <LinkButton
              onClick={() => {
                if (file) {
                  setFile(null)
                  setFileError(null)
                  if (input.current) input.current.value = ''
                }
                setUploadOpen(false)
              }}
            >
              {file ? '取消上传' : '收起上传'}
            </LinkButton>
          ) : null}
        </div>
      </header>

      {/* 上传口 */}
      {showUpload ? (
        <div
          className="grid overflow-hidden rounded-card border border-line-btn bg-card"
          style={{ gridTemplateColumns: 'minmax(0,1fr) 300px' }}
        >
          <div className="flex flex-col gap-12 px-22 py-18">
            <div className="flex flex-col gap-4">
              <div className="text-h3 font-semibold">开一本新书</div>
              <div className="text-body text-ink-3">
                丢一个 txt / md 进来 → stage0 立刻扫非正文（不花钱），完了停在审校屏等你确认删除范围
              </div>
            </div>
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
                chooseFile(event.dataTransfer.files?.[0] ?? null)
              }}
            >
              <span className="text-h2 text-ink-5">＋</span>
              <span className="flex min-w-0 flex-col gap-3">
                <span className="truncate text-body-lg">
                  {dragging
                    ? '松手选择这本原作'
                    : file
                      ? file.name
                      : '选择或拖入一本 .txt / .md 原作'}
                </span>
                <span className="text-tag text-ink-4">
                  {file
                    ? Math.round(file.size / 1024) + ' KB · 编码待探测（stage0 会给出结论）'
                    : '≤ 20 MB；保留完整原作，stage0 会检查编码并处理番外'}
                </span>
              </span>
              <input
                ref={input}
                type="file"
                accept={'.txt,.md'}
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  chooseFile(f ?? null)
                }}
              />
              <span
                className="ml-auto flex-none rounded-btn border border-line-btn bg-card px-12 py-6 text-body"
                onClick={(e) => {
                  e.preventDefault()
                  input.current?.click()
                }}
              >
                选文件
              </span>
            </label>
            <div className="flex items-center gap-12">
              <button
                type="button"
                disabled={!file || upload.isPending}
                onClick={() => upload.mutate()}
                className={
                  'rounded-btn border-none bg-ink px-18 py-9 text-body-lg font-semibold text-card ' +
                  (!file || upload.isPending ? 'cursor-not-allowed opacity-40' : 'cursor-pointer')
                }
              >
                {upload.isPending
                  ? '上传并扫描中…'
                  : file
                    ? '上传并扫非正文（stage0）'
                    : '先选一个 txt / md'}
              </button>
              <span className="text-tag text-ink-4">
                {file
                  ? 'stage0 与 stage1 都是确定性处理，不调 LLM、不花钱。'
                  : '上传会发送文件原始字节；成功后立刻建 sid。'}
              </span>
            </div>
            {fileError || upload.isError ? (
              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                {fileError ?? errorBody(upload.error)}
              </div>
            ) : null}
          </div>
          <div className="flex flex-col gap-9 border-l border-line-soft bg-sunken-3 px-20 py-18">
            <div className="text-tag font-semibold text-ink-3">上传后这本书会走的路</div>
            {vm.flow.map((f) => (
              <div key={f.n} className="flex gap-9">
                <Mono className="w-24 flex-none text-mini text-ink-5">{f.n}</Mono>
                <span className="text-tag leading-[1.7] text-ink-3">{f.t}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setUploadOpen(true)}
          className="flex cursor-pointer items-center gap-10 rounded-card border border-dashed border-line-btn bg-sunken-2 px-18 py-11 text-left hover:border-ink-6"
        >
          <span className="text-h3 text-accent">＋</span>
          <span className="text-body-lg font-semibold">开一本新书</span>
          <span className="text-body text-ink-4">拖入 txt / md，免费完成 stage0 扫描后停下审校</span>
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
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按 sid / 书名过滤"
          aria-label="按 sid 或书名过滤"
          className="ml-auto w-[220px] rounded-btn border border-line-btn bg-card px-10 py-5 text-body text-ink"
        />
      </div>

      <QueryStates q={vm.query} loadingLabel="读语料库">
        {() =>
          vm.books.length === 0 ? (
            <NotRunYet
              title="一本书都还没有"
              body="这台机器上的 Output 目录里没有任何一本书。把一个 .txt 丢进上面的上传口，stage0 会先扫番外，然后 stage1 做确定性切分——两步都不花钱。"
              action={<LinkButton onClick={() => setUploadOpen(true)}>选择第一本原作</LinkButton>}
            />
          ) : (
            <div className="flex flex-col gap-14">
              <BatchBar
                picked={pickedBooks.length}
                running={queue.data?.running.length ?? 0}
                pending={queue.data?.pending.length ?? 0}
                gate={queue.data?.max_concurrent ?? null}
                onClear={() => setPicked([])}
                onRun={() => setBatchOpen(true)}
              />
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
                  onAgent={() => setAgentSid(b.sid)}
                />
              ))}
            </div>
          )
        }
      </QueryStates>

      {agentSid ? (
        <AgentRunDialog
          sid={agentSid}
          onClose={() => setAgentSid(null)}
          onStarted={(sid) => {
            setAgentSid(null)
            nav('/n/' + sid + '/run')
          }}
        />
      ) : null}

      {batchOpen ? (
        <AgentBatchDialog
          books={pickedBooks}
          onClose={() => {
            setBatchOpen(false)
            setPicked([])
          }}
        />
      ) : null}
    </section>
  )
}

/**
 * 批量开跑的操作条。**只在勾了书之后才有动作**，平时是一条并发现状。
 *
 * ⚠ 「几个在跑 / 几个在排 / 闸多大」三个数全部来自服务端 `/api/agent/runs/queue`——
 * 界面不自己数、也不写死上限（同「贵贱只有一处算」那条纪律）。
 */
function BatchBar({
  picked,
  running,
  pending,
  gate,
  onClear,
  onRun,
}: {
  picked: number
  running: number
  pending: number
  gate: number | null
  onClear: () => void
  onRun: () => void
}) {
  return (
    <div className="flex items-center gap-12 rounded-card border border-line bg-sunken-2 px-18 py-11">
      <span className="text-body text-ink-2">
        {picked > 0 ? (
          <>
            已勾 <span className="font-semibold">{picked}</span> 本
            <span className="text-ink-5">（勾选顺序就是排队顺序）</span>
          </>
        ) : (
          <span className="text-ink-4">勾上几本可以一次交给 agent 跑；一本一本点也照旧。</span>
        )}
      </span>

      <span className="text-tag text-ink-4">
        agent 现在：{running} 在跑
        {pending > 0 ? ' · ' + pending + ' 排队' : ''}
        {gate !== null ? ' · 上限 ' + gate : ''}
      </span>

      {picked > 0 ? (
        <>
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
        </>
      ) : null}
    </div>
  )
}

function BookCard({
  b,
  picked,
  onPick,
  onOpen,
  onAgent,
}: {
  b: BookView
  picked: boolean
  onPick: () => void
  onOpen: () => void
  onAgent: () => void
}) {
  return (
    <article className="overflow-hidden rounded-card border border-line bg-card">
      <div
        className="grid border-b border-line-soft"
        style={{ gridTemplateColumns: 'minmax(300px,1.1fr) 270px 96px' }}
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
              {b.title}
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
          <span className="text-tag text-ink-4">{b.verdictDetail}</span>
          <span className="text-micro text-ink-5">
            {b.mtimeLabel ?? '没有任何产物落过盘'}
          </span>
        </div>
        <div className="flex flex-col items-center justify-center gap-8 border-l border-line-soft bg-sunken-2 px-12">
          <button
            type="button"
            onClick={onOpen}
            className="cursor-pointer rounded-btn border border-line-btn bg-card px-13 py-7 text-body font-semibold text-ink-2 hover:border-accent-line-2 hover:text-accent-ink"
          >
            打开 →
          </button>
          <button
            type="button"
            onClick={onAgent}
            title="让 agent 按操作规程代跑这本书（先 0 成本 preview，授权在确认框里勾）"
            className="cursor-pointer rounded-btn border border-line-btn bg-card px-13 py-7 text-body font-semibold hover:border-accent-line-2"
            style={{ color: SPEND_INK }}
          >
            agent 跑书
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
                className={cx(
                  'ml-auto text-micro',
                  c.done === true
                    ? 'text-gate-ok-ink'
                    : c.done === false
                      ? 'text-ink-5'
                      : 'text-warn-ink',
                )}
              >
                {c.done === true ? '✓ ' : c.done === null ? '? ' : ''}
                {c.stateLabel}
              </span>
            </div>

            {c.isStage5 ? (
              <div className="flex flex-col gap-5 rounded-btn border border-dashed border-line-btn bg-sunken-2 px-8 py-7">
                {c.modes.length ? (
                  c.modes.map((m) => (
                    <Mono key={m} className="text-mini text-ink-3">
                      {m}
                    </Mono>
                  ))
                ) : (
                  <span className="text-mini text-ink-5">还没跑过任何一条臂</span>
                )}
                <span className="text-micro leading-[1.6] text-ink-5">
                  成稿不进产物表，只记跑过哪几条臂
                </span>
              </div>
            ) : c.items.length ? (
              c.items.map((a) => (
                <div
                  key={a.name}
                  className="flex flex-col gap-4 rounded-btn border border-line-2 bg-card px-8 py-6"
                >
                  <div className="flex min-w-0 items-center gap-6">
                    <FreshnessDot f={a.freshness} />
                    <Mono className="min-w-0 flex-1 truncate text-tag" title={a.name}>
                      {a.name}
                    </Mono>
                  </div>
                  <div className="flex items-center gap-6">
                    {a.issue ? (
                      <span className="rounded-tag bg-warn-bg px-6 py-1 text-micro text-warn-ink">
                        {a.issue}
                      </span>
                    ) : (
                      <FreshnessTag f={a.freshness} />
                    )}
                    <Mono className="ml-auto flex-none text-micro text-ink-5">{a.size}</Mono>
                  </div>
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
