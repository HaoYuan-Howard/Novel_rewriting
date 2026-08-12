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
import { useCorpusScreen, type BookView } from './useCorpusScreen'

/**
 * 语料库：所有书的产物矩阵 + 新鲜度，顶部是「开一本新书」的上传口。
 * 这一屏没有闸门也没有校验面板 —— 它不属于任何一层。
 */
export function CorpusScreen() {
  const vm = useCorpusScreen()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const input = useRef<HTMLInputElement>(null)

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
        </div>
      </header>

      {/* 上传口 */}
      <div className="grid overflow-hidden rounded-card border border-line-btn bg-card" style={{ gridTemplateColumns: 'minmax(0,1fr) 300px' }}>
        <div className="flex flex-col gap-12 px-22 py-18">
          <div className="flex flex-col gap-4">
            <div className="text-h3 font-semibold">开一本新书</div>
            <div className="text-body text-ink-3">
              丢一个 txt / md 进来 → stage0 立刻扫非正文（不花钱），完了停在审校屏等你确认删除范围
            </div>
          </div>
          <label className="flex cursor-pointer items-center gap-14 rounded-block border-[1.5px] border-dashed border-ink-7 bg-sunken-2 px-16 py-14 hover:border-ink-4">
            <span className="text-h2 text-ink-5">＋</span>
            <span className="flex min-w-0 flex-col gap-3">
              <span className="truncate text-body-lg">
                {file ? file.name : '选择一本 .txt / .md 原作'}
              </span>
              <span className="text-tag text-ink-4">
                {file
                  ? Math.round(file.size / 1024) + ' KB · 编码待探测（stage0 会给出结论）'
                  : '一本完整的原作，含文案段与番外——剔除那一步会把番外拿掉'}
              </span>
            </span>
            <input
              ref={input}
              type="file"
              accept={'.txt,.md'}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) setFile(f)
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
              {file ? '上传并扫非正文（stage0）' : '先选一个 txt / md'}
            </button>
            <span className="text-tag text-ink-4">
              {file
                ? 'stage0 与 stage1 都是确定性处理，不调 LLM、不花钱。'
                : '上传会发送文件原始字节；成功后立刻建 sid。'}
            </span>
            {upload.isError ? (
              <span className={'text-tag text-warn'}>{errorBody(upload.error)}</span>
            ) : null}
          </div>
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

      {/* 图例：形状本身在说话，不是只有颜色 */}
      <div className="flex flex-wrap items-center gap-18 px-2">
        {vm.legend.map((l) => (
          <span key={l.f} className="inline-flex items-baseline gap-6">
            <FreshnessBadge f={l.f} />
            <span className="text-micro text-ink-5">{l.desc}</span>
          </span>
        ))}
      </div>

      <QueryStates q={vm.query} loadingLabel="读语料库">
        {() =>
          vm.books.length === 0 ? (
            <NotRunYet
              title="一本书都还没有"
              body="这台机器上的 Output 目录里没有任何一本书。把一个 .txt 丢进上面的上传口，stage0 会先扫番外，然后 stage1 做确定性切分——两步都不花钱。"
            />
          ) : (
            <div className="flex flex-col gap-14">
              {vm.books.map((b) => (
                <BookCard key={b.sid} b={b} onOpen={() => nav('/n/' + b.sid + '/stage1')} />
              ))}
            </div>
          )
        }
      </QueryStates>
    </section>
  )
}

function BookCard({ b, onOpen }: { b: BookView; onOpen: () => void }) {
  return (
    <article className="overflow-hidden rounded-card border border-line bg-card">
      <div
        className="grid border-b border-line-soft"
        style={{ gridTemplateColumns: 'minmax(300px,1.1fr) 260px' }}
      >
        <div className="flex flex-col gap-6 px-18 py-14">
          <button
            type="button"
            onClick={onOpen}
            className="cursor-pointer border-none bg-transparent p-0 text-left text-h2 font-semibold text-ink hover:text-accent"
          >
            {b.title}
          </button>
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
      </div>

      <div
        className="grid gap-1 bg-line-soft"
        style={{ gridTemplateColumns: 'repeat(7,minmax(0,1fr))' }}
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
                    <FreshnessTag f={a.freshness} />
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
