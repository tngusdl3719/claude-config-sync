import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ItemState, LogEntry, Status, View } from '../types'

const PANE = 'config-sync'
const TITLE = '설정 동기화'
const POLL_MS = 30_000

const status = atom({ plugin: 'config-sync', key: 'status' } as const, null)
const view = atom({ plugin: 'config-sync', key: 'view' } as const, { name: 'main' })
const busy = atom({ plugin: 'config-sync', key: 'busy' } as const, null)

type Reply = { error?: string; [key: string]: unknown }

function bin($: EngineInterface): string {
  return `${$.plugin.root}/bin/claude-sync`
}

async function engine($: EngineInterface, args: string[], timeoutMs = 120_000): Promise<Reply> {
  try {
    const r = await $.process.run([bin($), ...args], { timeoutMs })
    try {
      return JSON.parse(r.stdout) as Reply
    } catch {
      return { error: (r.stderr || r.stdout || `exit ${r.exitCode}`).trim().slice(0, 300) }
    }
  } catch (error) {
    return { error: String(error).slice(0, 300) }
  }
}

function setView($: EngineInterface, next: View): Promise<View> {
  return update($, view, () => next)
}

function isStatus(reply: Reply): reply is Reply & Status {
  return reply.error === undefined && typeof reply.pc === 'string' && typeof reply.items === 'object'
}

async function refresh($: EngineInterface): Promise<void> {
  try {
    const reply = await engine($, ['status'], 20_000)
    if (isStatus(reply)) await update($, status, () => reply)
  } catch {
    // the session is going away; the next poll reads it again
  }
}

// Runs `sync --auto` in a process of its own, so neither a session's start nor its end waits on the network.
async function detachedSync($: EngineInterface): Promise<void> {
  try {
    await $.process.run(['/bin/sh', '-c', 'nohup "$0" sync --auto >/dev/null 2>&1 &', bin($)], { timeoutMs: 5_000 })
  } catch {
    // the next start or a manual sync tries again
  }
}

// What the person pressed: run it, show what came back, keep the status fresh.
async function act($: EngineInterface, label: string, args: string[], done?: string): Promise<Reply> {
  await update($, busy, () => label)
  const reply = await engine($, args)
  if (isStatus(reply)) await update($, status, () => reply)
  else await refresh($)
  await update($, busy, () => null)
  if (reply.error !== undefined) $.ui.toast(`동기화: ${reply.error}`)
  else if (done) $.ui.toast(done)
  return reply
}

export function ago(seconds: number): string {
  if (seconds < 60) return '방금'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`
  return `${Math.floor(seconds / 86400)}일 전`
}

function clockText(at: number, now: number): string {
  const d = new Date(at * 1000)
  const today = new Date(now * 1000)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return d.toDateString() === today.toDateString() ? `오늘 ${hm}` : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

type Tone = 'dim' | 'warning' | 'error'

export function bandLine(s: Status): { tone: Tone; text: string } {
  const last = s.last_pull === null ? '아직 없음' : ago(s.now - s.last_pull)
  const other = s.others[0] ? ` · ${s.others[0].pc} ${clockText(s.others[0].time, s.now)}` : ''
  switch (s.last_result) {
    case 'blocked':
      return { tone: 'error', text: '⚠ 금지어 때문에 올리기를 멈췄어요 · /sync 에서 확인' }
    case 'conflict':
      return { tone: 'error', text: `⚠ 충돌 ${s.conflicts.length}건 · /sync 에서 골라 주세요` }
    case 'needs_approval': {
      const who = s.pending_approval[0]?.commits[0]?.pc ?? '다른 PC'
      return { tone: 'warning', text: `⇅ 확인 필요 1건 · ${who}의 실행 파일 변경 · /sync` }
    }
    case 'offline':
      return { tone: 'error', text: `⚠ 서버에 연결하지 못했어요 · 마지막 동기화 ${last}` }
    case 'error':
      return { tone: 'error', text: '⚠ 동기화 오류 · /sync 에서 확인' }
    case 'never':
      return { tone: 'dim', text: '⇅ 아직 동기화 전 · /sync' }
  }
  if (s.incoming > 0) return { tone: 'warning', text: `⇅ 받을 것 ${s.incoming}개 · /sync` }
  if (s.receive_only) return { tone: 'dim', text: `⇣ 받기 ${last} ✓ (받기 전용)${other}` }
  return { tone: 'dim', text: `⇅ 동기화 ${last} ✓${other}` }
}

const STATE_LABEL: Record<ItemState, string> = {
  ok: '✓ 최신',
  local: '↑ 이 PC에서 바뀜',
  incoming: '↓ 받을 것 있음',
  conflict: '⚠ 충돌',
}

const RESULT_LABEL: Record<Status['last_result'], string> = {
  ok: '● 정상',
  offline: '● 서버 연결 안 됨',
  blocked: '● 올리기 멈춤 — 금지어 발견',
  conflict: '● 충돌',
  needs_approval: '● 확인 필요',
  error: '● 오류',
  never: '● 아직 동기화 전',
}

function groupState(items: { state: ItemState }[]): ItemState {
  for (const s of ['conflict', 'incoming', 'local'] as const) if (items.some(i => i.state === s)) return s
  return 'ok'
}

function names(items: { name: string }[]): string {
  if (items.length === 0) return '없음'
  const head = items.slice(0, 2).map(i => i.name).join(', ')
  return items.length > 2 ? `${head} 외 ${items.length - 2}` : head
}

function showValue(v: unknown): string {
  return v === null || v === undefined ? '(없음)' : JSON.stringify(v)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'sync', description: '설정 동기화 앱 열기 (공용 지침·설정·스킬·mods)' })
    await refresh($)
    const s = await read($, status)
    if (s?.auto_enabled) await detachedSync($)
    $.clock.every(POLL_MS, () => {
      void refresh($)
    })
    return result
  })

  on('session.end', async ($, e, next) => {
    if (e.reason !== 'clear') {
      const s = await read($, status)
      if (s?.auto_enabled) await detachedSync($)
    }
    return next(e)
  })

  on('command.run', { command: 'sync' }, async $ => {
    await setView($, { name: 'main' })
    await $.ui.open({ id: PANE, title: TITLE, focus: true })
    void refresh($)
    return { text: `${TITLE} 앱을 열었어요.` }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const below = await next(e)
    const s = await read($, status)
    if (s === null || e.props.hasSurvey) return below
    const { Box, Text } = $.ui.resolve(e)
    const line = bandLine(s)
    return (
      <Box flexDirection="column">
        <Box key="sync-band">
          <Text dimColor={line.tone === 'dim'} color={line.tone === 'dim' ? undefined : line.tone} wrap="truncate-end">
            {line.text}
          </Text>
        </Box>
        {below}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const table = $.ui.resolve(e)
    const { Box, Text, Button, Markdown } = table
    const Input = 'Input' in table ? table.Input : null
    const s = await read($, status)
    const v = await read($, view)
    const working = await read($, busy)
    const back = (
      <Button key="back" label="뒤로" hotkey="q" onPress={() => setView($, { name: 'main' })} />
    )

    if (s === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>상태를 읽는 중…</Text>
        </Box>
      )
    }

    if (v.name === 'detail') {
      return (
        <Box flexDirection="column" gap={1}>
          <Text bold>{v.title}</Text>
          <Markdown key="detail" text={v.text.trim() ? '```diff\n' + v.text + '\n```' : '바뀐 내용이 없어요.'} />
          {back}
        </Box>
      )
    }

    if (v.name === 'history') {
      return (
        <Box flexDirection="column">
          <Text bold>최근 동기화 기록</Text>
          {v.entries.length === 0 && <Text dimColor>기록이 없어요.</Text>}
          {v.entries.map((entry: LogEntry) => (
            <Box key={`log:${entry.sha}`} gap={1}>
              <Text dimColor>{clockText(entry.time, s.now)}</Text>
              <Text>{entry.pc}</Text>
              <Text wrap="truncate-end">{entry.subject.replace(/^sync: [^:]+: /, '')}</Text>
              <Button
                key={`revert:${entry.sha.slice(0, 7)}`}
                label="되돌리기"
                onPress={() => act($, '되돌리는 중', ['revert', entry.sha.slice(0, 7)], '되돌렸어요')}
              />
            </Box>
          ))}
          {back}
        </Box>
      )
    }

    if (v.name === 'blocklist') {
      return (
        <Box flexDirection="column">
          <Text bold>금지어</Text>
          <Text dimColor>이 PC에서 나가는 변경에 아래 단어가 있으면 올리지 않아요. 이 목록은 동기화되지 않아요.</Text>
          {s.words.length === 0 && <Text dimColor>금지어가 없어요.</Text>}
          {s.words.map(word => (
            <Box key={`word:${word}`} gap={1}>
              <Text>{word}</Text>
              <Button key={`unblock:${word}`} label="빼기" onPress={() => act($, '금지어 빼는 중', ['block', 'remove', word])} />
            </Box>
          ))}
          {Input !== null && <Input
            key="word"
            label="금지어 추가"
            placeholder="회사명, 내부 도메인 등"
            onSubmit={(value: string) => (value.trim() ? act($, '금지어 넣는 중', ['block', 'add', value.trim()]) : undefined)}
          />}
          {back}
        </Box>
      )
    }

    if (v.name === 'group') {
      const items = s.items[v.kind]
      return (
        <Box flexDirection="column">
          <Text bold>공용 {v.kind === 'skills' ? '스킬' : 'mods'}</Text>
          {items.map(item => (
            <Box key={`member:${item.name}`} gap={1}>
              <Button
                key={`item:${v.kind}/${item.name}`}
                plain
                onPress={async () => {
                  const reply = await engine($, ['diff', `${v.kind}/${item.name}`])
                  await setView($, { name: 'detail', title: `${item.name} 바뀐 내용`, text: String(reply.text ?? reply.error ?? '') })
                }}
              >
                {item.name} <Text dimColor>{STATE_LABEL[item.state]}</Text>
              </Button>
              {!s.receive_only && <Button
                key={`unshare:${v.kind}:${item.name}`}
                label="공용에서 빼기"
                onPress={() => act($, '공용에서 빼는 중', ['unshare', v.kind, item.name], `${item.name}: 이 PC 전용으로 돌렸어요`)}
              />}
            </Box>
          ))}
          {back}
        </Box>
      )
    }

    const openDiff = async (item: string, title: string) => {
      const reply = await engine($, ['diff', item])
      await setView($, { name: 'detail', title, text: String(reply.text ?? reply.error ?? '') })
    }
    const last = s.last_pull === null ? '아직 없음' : ago(s.now - s.last_pull)
    const resultColor = s.last_result === 'ok' ? 'success' : s.last_result === 'needs_approval' ? 'warning' : s.last_result === 'never' ? undefined : 'error'
    const approval = s.pending_approval[0]

    return (
      <Box flexDirection="column">
        <Box gap={1}>
          <Text>상태</Text>
          <Text color={resultColor}>{RESULT_LABEL[s.last_result]}</Text>
          <Text>마지막 동기화 {last}</Text>
          {working !== null && <Text color="warning">{working}…</Text>}
        </Box>
        <Text dimColor>
          받기 {s.last_pull === null ? '-' : clockText(s.last_pull, s.now)} · 올리기{' '}
          {s.receive_only ? '안 함 (받기 전용)' : s.last_push === null ? '-' : clockText(s.last_push, s.now)} · 이 PC {s.pc}
        </Text>
        {s.others.map(o => (
          <Text dimColor>
            {o.pc} 마지막 동기화: {clockText(o.time, s.now)}
          </Text>
        ))}
        {s.last_error !== null && s.last_result !== 'ok' && <Text color="error">{s.last_error}</Text>}

        <Box gap={1} marginTop={1}>
          <Button key="sync" label="지금 동기화" hotkey="s" variant="primary" onPress={() => act($, '동기화 중', ['sync'], '동기화했어요')} />
          <Button
            key="auto"
            label={`자동 동기화: ${s.auto_enabled ? '켜짐' : '꺼짐'}`}
            hotkey="a"
            onPress={() => act($, '바꾸는 중', ['auto', s.auto_enabled ? 'off' : 'on'])}
          />
          <Button
            key="history"
            label="기록"
            hotkey="h"
            onPress={async () => {
              const reply = await engine($, ['log', '-n', '20'])
              const entries = Array.isArray(reply) ? (reply as LogEntry[]) : []
              await setView($, { name: 'history', entries })
            }}
          />
          <Button key="blocklist" label={`금지어 ${s.words.length}개`} hotkey="b" onPress={() => setView($, { name: 'blocklist' })} />
        </Box>

        {s.blocked.length > 0 && (
          <Box flexDirection="column" marginTop={1}>
            <Text color="error" bold>올리기 멈춤 — 금지어 발견</Text>
            {s.blocked.slice(0, 5).map((hit, i) => (
              <Text>
                {hit.file}
                {hit.line > 0 ? ` ${hit.line}행` : ''} "{hit.text.slice(0, 60)}" <Text dimColor>걸린 말: {hit.word}</Text>
              </Text>
            ))}
            <Box gap={1}>
              <Button
                key="fix"
                label="해당 줄 고치기"
                hotkey="e"
                onPress={async () => {
                  const hit = s.blocked[0]!
                  await $.prompt.fill({ text: `~/.claude/shared/${hit.file} ${hit.line}행에서 "${hit.word}" 가 들어간 내용을 지우거나 일반적인 말로 바꿔 줘.`, mode: 'replace' })
                  await $.ui.close({ id: PANE })
                }}
              />
              <Button key="discard" label="이번 변경 되돌리기" hotkey="r" onPress={() => act($, '되돌리는 중', ['discard-blocked'], '올리지 않은 변경을 취소했어요')} />
              {!s.receive_only && (
                <Button key="allow-once" label="이번만 허용" hotkey="i" onPress={() => act($, '올리는 중', ['allow-once'], '이번 변경을 올렸어요')} />
              )}
            </Box>
          </Box>
        )}

        {s.conflicts.map((c, i) => (
          <Box key={`conflict:${c.id}`} flexDirection="column" marginTop={1}>
            <Text color="error" bold>충돌 · {c.kind === 'file' ? c.file : `설정 ${(c.path ?? []).join('.')}`}</Text>
            {c.kind === 'setting' && (
              <Text>
                이 PC: {showValue(c.local)}   서버: {showValue(c.remote)}
              </Text>
            )}
            <Box gap={1}>
              <Button key={`keep:${c.id}`} label="이 PC 값 유지" hotkey={i === 0 ? '1' : undefined} onPress={() => act($, '정리하는 중', ['resolve', c.id, 'keep'], '이 PC 값을 유지했어요')} />
              <Button key={`take:${c.id}`} label="서버 값 받기" hotkey={i === 0 ? '2' : undefined} onPress={() => act($, '정리하는 중', ['resolve', c.id, 'take'], '서버 값을 받았어요')} />
            </Box>
          </Box>
        ))}

        {approval !== undefined && (
          <Box flexDirection="column" marginTop={1}>
            <Text color="warning" bold>
              확인 필요 · {approval.commits[0]?.pc ?? '다른 PC'}에서 실행 파일이 바뀌었어요
            </Text>
            {approval.files.slice(0, 6).map(f => (
              <Text dimColor>
                {f}
              </Text>
            ))}
            {approval.settings.map(p => (
              <Text dimColor>
                설정 {p.join('.')}
              </Text>
            ))}
            <Box gap={1}>
              <Button key="approval-diff" label="바뀐 내용 보기" hotkey="d" onPress={() => openDiff('approval', '확인할 변경')} />
              <Button key="approve" label="승인" hotkey="y" variant="primary" onPress={() => act($, '적용하는 중', ['approve'], '승인하고 적용했어요')} />
              <Button key="reject" label="거절" hotkey="n" onPress={() => act($, '거절하는 중', ['reject'], '거절하고 되돌렸어요')} />
            </Box>
          </Box>
        )}

        {s.collisions.length > 0 && (
          <Text color="warning">
            이름이 겹쳐 건너뛴 공용 항목: {s.collisions.map(c => `${c.kind}/${c.name}`).join(', ')}
          </Text>
        )}

        <Text bold>{'\n'}공용 (동기화됨)</Text>
        <Button key="item:claude_md" plain onPress={() => openDiff('claude_md', '지침 바뀐 내용')}>
          ▸ 지침   shared/CLAUDE.md <Text dimColor>{STATE_LABEL[s.items.claude_md.state]}</Text>
        </Button>
        <Button key="item:settings" plain onPress={() => openDiff('settings', '설정 바뀐 내용')}>
          ▸ 설정   {s.items.settings.count}개 항목 · {s.items.settings.keys.slice(0, 3).join(', ')}… <Text dimColor>{STATE_LABEL[s.items.settings.state]}</Text>
        </Button>
        <Button key="group:skills" plain onPress={() => setView($, { name: 'group', kind: 'skills' })}>
          ▸ 스킬   {names(s.items.skills)} <Text dimColor>{STATE_LABEL[groupState(s.items.skills)]}</Text>
        </Button>
        <Button key="group:mods" plain onPress={() => setView($, { name: 'group', kind: 'mods' })}>
          ▸ mods   {names(s.items.mods)} <Text dimColor>{STATE_LABEL[groupState(s.items.mods)]}</Text>
        </Button>

        <Text bold>{'\n'}이 PC 전용 <Text dimColor>(동기화 안 됨)</Text></Text>
        {(['skills', 'mods'] as const).map(kind =>
          s.local_only[kind].map(name => (
            <Box key={`local:${kind}:${name}`} gap={1}>
              <Text>
                {kind === 'skills' ? '스킬' : 'mod'} {name}
              </Text>
              {!s.receive_only && (
                <Button
                  key={`share:${kind}:${name}`}
                  label="공용으로 보내기"
                  onPress={() => act($, '공용으로 보내는 중', ['share', kind, name], `${name}: 공용으로 보냈어요`)}
                />
              )}
            </Box>
          )),
        )}
        <Text dimColor>설정 {s.local_only.settings.join(', ') || '없음'}</Text>

        {s.require_approval && Input !== null && (
          <Box flexDirection="column" marginTop={1}>
            <Input
              key="register"
              label="새 PC 등록"
              placeholder="다른 PC의 설치 파일이 보여 준 공개키 한 줄 (ssh-ed25519 …)"
              submitLabel="등록"
              onSubmit={(value: string) => act($, '등록하는 중', ['register-pc', value.trim()], '새 PC를 서버에 등록했어요')}
            />
          </Box>
        )}
        <Text dimColor>Tab·↑↓ 이동 · Enter 누르기 · Esc 프롬프트로</Text>
      </Box>
    )
  })
}
