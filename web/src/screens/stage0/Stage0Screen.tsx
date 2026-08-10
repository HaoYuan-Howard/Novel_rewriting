import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { SEV } from '@/constants/vocabulary'
import { useApprovals } from '@/hooks/useApprovals'
import { useSid } from '@/hooks/useRouteParams'
import { QueryStates } from '@/components/state/QueryStates'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { SevMark } from '@/components/vocab/Sev'
import { cx } from '@/lib/cx'

/**
 * 剔除番外。
 *
 * 这一屏最关键的一条：**「没检出」不等于「没有番外」**。
 * 有的作者不写标记，直接把番外接在正文后面 —— 所以扫不到时不画绿灯，
 * 画的是「查不了」的虚线标记，并把人推到右边逐块手工圈。
 */
export function Stage0Screen() {
  const sid = useSid()
  const nav = useNavigate()
  const { doc, approve } = useApprovals()
  const [drop, setDrop] = useState<Record<string, boolean>>({})
  const [picks, setPicks] = useState<Record<string, boolean>>({})

  const q = useQuery({
    queryKey: qk.stage0(sid),
    queryFn: () => api.getStage0(sid),
    retry: false,
  })

  return (
    <QueryStates q={q} loadingLabel="扫番外 / 后记 / 作者有话说" missing={{ title: '剔除番外 · stage0' }}>
      {(d) => {
        const cands = d.candidates
        const found = cands.length > 0
        const on = (id: string) => drop[id] !== false
        const chosen = cands.filter((c) => on(c.id))
        const marked = d.blocks.filter((_, i) => picks['b' + i])
        const dropCount = chosen.length + marked.length
        const dropChars =
          chosen.reduce((s, c) => s + c.chars, 0) + marked.reduce((s, b) => s + b.chars, 0)
        const done = !!doc?.gate.s0

        return (
          <section className="flex flex-col gap-16 px-30 py-24">
            <header className="flex items-start justify-between gap-24">
              <div className="flex flex-col gap-5">
                <h1 className="m-0 text-screen font-semibold">剔除番外 · stage0</h1>
                <div className="prose-cn max-w-[52em] text-body text-ink-3">
                  在切分之前，先把不属于正文的部分从原文里拿掉：番外、后记、尾声、作者有话说。删掉的东西后面每一层都不会再看见。
                </div>
              </div>
              <div className="flex flex-col items-end gap-2 text-right">
                <Mono className="text-tag text-ink-4">{d.original_filename}</Mono>
                <Mono className="text-tag text-ink-4">
                  sha1 {d.source_sha1.slice(0, 12)} · {d.source_encoding} · {d.total_code_points} 码点
                </Mono>
              </div>
            </header>

            <Panel className="flex flex-wrap items-center gap-14 px-18 py-13">
              {/* 扫不到 = 查不了，不是通过 */}
              <SevMark sev={found ? 'info' : 'unver'} size={10} />
              <span className="text-section font-semibold">
                {found ? '扫到 ' + cands.length + ' 段命中标记行' : '没扫到番外标记行'}
              </span>
              <span className="prose-cn max-w-[44em] text-sm2 text-ink-3">{d.note}</span>
              <span className="ml-auto flex flex-wrap justify-end gap-6">
                {d.markers.map((m) => (
                  <Mono key={m} className="rounded-tag bg-inset px-7 py-2 text-micro text-ink-3">
                    {m}
                  </Mono>
                ))}
              </span>
            </Panel>

            <div
              className="grid items-start gap-16"
              style={{ gridTemplateColumns: 'minmax(0,0.9fr) minmax(0,1.1fr)' }}
            >
              <Panel>
                <PaneHeader
                  title="左 · 要删的范围"
                  meta={
                    <span
                      className="rounded-tag px-7 py-1"
                      style={
                        dropCount
                          ? undefined
                          : { color: SEV.unver.color, background: SEV.unver.bg }
                      }
                    >
                      {dropCount} 段
                    </span>
                  }
                  right={
                    <span className="text-tag text-ink-4">
                      {found ? '点一条可以取消删它' : '标记行没命中不代表没有番外'}
                    </span>
                  }
                />

                {found ? (
                  cands.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setDrop((s) => ({ ...s, [c.id]: !on(c.id) }))}
                      className={cx(
                        'flex w-full cursor-pointer items-start gap-10 border-b border-line-row px-18 py-12 text-left',
                        on(c.id) ? 'bg-spend-bg' : 'bg-transparent',
                      )}
                    >
                      <span
                        aria-hidden
                        className={cx(
                          'mt-2 flex h-16 w-16 flex-none items-center justify-center rounded-[3px] border-[1.5px] text-micro',
                          on(c.id) ? 'border-ink bg-ink text-card' : 'border-ink-7',
                        )}
                      >
                        {on(c.id) ? '✓' : ''}
                      </span>
                      <span className="flex min-w-0 flex-col gap-4">
                        <span className="flex items-center gap-8">
                          <Mono className="text-mini text-ink-4">{c.from_block}</Mono>
                          <span className="text-body font-semibold">{c.kind}</span>
                          <Mono className="text-micro text-ink-5">
                            标记「{c.marker}」 · {c.chars} 字
                          </Mono>
                        </span>
                        <span className="prose-cn text-sm2 text-ink-3">{c.head} …… {c.tail}</span>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="flex flex-col gap-10 px-18 py-16">
                    <div className="text-body font-semibold">没有可自动确认的范围，得你自己圈</div>
                    <div className="prose-cn text-sm2 text-ink-3">
                      右边是全书顺序铺开的段落。属于番外 / 后记 / 作者有话说的，逐块点「标为番外」；只删你标的，其余原样进 stage1。
                    </div>
                    <div className="flex items-center gap-10">
                      <span className="text-tag text-ink-4">
                        {marked.length
                          ? '已标 ' + marked.length + ' 块'
                          : '还没标任何块——什么都不删也可以直接进 stage1'}
                      </span>
                      {marked.length ? (
                        <LinkButton onClick={() => setPicks({})}>清空标记</LinkButton>
                      ) : null}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-12 border-t border-line-soft bg-sunken-2 px-16 py-13">
                  <div className="flex min-w-0 flex-col gap-3">
                    <span className="text-body font-semibold">
                      {dropCount ? '要删 ' + dropCount + ' 段 · 共 ' + dropChars + ' 字' : '当前一个字都不删'}
                    </span>
                    <span className="text-tag text-ink-4">删除前先落一份原文备份，删完不可逆</span>
                  </div>
                  <ActionButton
                    className="ml-auto"
                    action={{ kind: 'n0_extras', extra: null, paid: false }}
                    disabled={done}
                    disabledReason="已经确认过删除范围了"
                    onClick={() => {
                      approve.mutate('s0', {
                        onSuccess: () => nav('/n/' + sid + '/stage1'),
                      })
                    }}
                  >
                    {done
                      ? '已确认'
                      : dropCount
                        ? '删除并进入 stage1'
                        : '不删，直接进入 stage1'}
                  </ActionButton>
                </div>
              </Panel>

              <Panel>
                <PaneHeader
                  title="右 · 原文（未切分）"
                  meta={'顺序铺开 · ' + d.blocks.length + ' 块'}
                />
                {d.blocks.map((b, i) => {
                  const isMarked = !!picks['b' + i]
                  return (
                    <div
                      key={i}
                      className="border-b border-line-row px-16 py-12"
                      style={isMarked ? { background: SEV.hard.bg } : undefined}
                    >
                      <div className="flex flex-wrap items-center gap-8">
                        <span
                          className={cx(
                            'rounded-tag px-7 py-1 text-micro',
                            b.kind === 'hook_copy' ? 'bg-hook text-card' : 'bg-inset text-ink-3',
                          )}
                        >
                          {b.kind === 'hook_copy' ? '文案段' : '正文'}
                        </span>
                        <span className="text-body font-semibold">{b.label}</span>
                        <Mono className="text-micro text-ink-5">{b.chars} 字</Mono>
                        <button
                          type="button"
                          onClick={() => setPicks((s) => ({ ...s, ['b' + i]: !isMarked }))}
                          className={cx(
                            'ml-auto cursor-pointer rounded-btn border px-10 py-4 text-tag',
                            !isMarked && 'border-line-btn text-ink-3',
                          )}
                          style={
                            isMarked
                              ? { color: SEV.hard.color, borderColor: SEV.hard.border }
                              : undefined
                          }
                        >
                          {isMarked ? '已标为番外 · 取消' : '标为番外'}
                        </button>
                      </div>
                      <div className="prose-cn mt-6 text-body text-ink-2">{b.preview} …</div>
                    </div>
                  )
                })}
              </Panel>
            </div>
          </section>
        )
      }}
    </QueryStates>
  )
}
