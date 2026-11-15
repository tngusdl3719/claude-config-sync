import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import type { Status } from '../types'

const SURFACES = ['terminal', 'desktop'] as const
const KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHk3x7v1u0kq company-mac'

const BASE: Status = {
  pc: 'personal-mac',
  now: 1_800_000_180,
  last_pull: 1_800_000_000,
  last_push: 1_800_000_000,
  last_result: 'ok',
  last_error: null,
  auto_enabled: true,
  require_approval: false,
  others: [{ pc: 'company-mac', time: 1_799_990_000 }],
  items: {
    claude_md: { state: 'ok' },
    settings: { count: 12, keys: ['effortLevel', 'theme'], state: 'local' },
    skills: [{ name: 'brand', state: 'ok' }, { name: 'ui-ux-pro-max', state: 'incoming' }],
    mods: [{ name: 'usage-band', state: 'ok' }],
  },
  local_only: { skills: ['design-notes'], mods: [], settings: ['hooks', 'statusLine', 'autoMode'] },
  incoming: 0,
  outgoing: 0,
  conflicts: [],
  blocked: [],
  pending_approval: [],
  collisions: [],
  words: [],
}

const BAND_PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 100,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}

const PANE_PROPS = {
  title: '설정 동기화',
  isFocused: true,
  bodyColumns: 100,
  placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 40 },
  view: {},
}

type Calls = string[][]

// What the engine itself answers in a session: nothing sits beneath a test's plugin.
function bottoms(on: On) {
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.end', ($, e) => ({ sessionId: e.sessionId }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('ui.close', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('prompt.fill', () => ({ isFilled: true }))
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box key="engine-band" />
  })
  mock.clock(on)
}

// Every engine call answers with `reply(args)`; the args after the binary are recorded.
function engine(on: On, calls: Calls, reply: (args: string[]) => unknown, opened: string[] = []) {
  bottoms(on)
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('process.run', ($, e) => {
    const args = e.argv[0] === '/bin/sh' ? ['(detached)', ...e.argv.slice(3)] : e.argv.slice(1)
    calls.push(args)
    return {
      value: {
        exitCode: 0,
        stdout: JSON.stringify(reply(args)),
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })
}

async function start($: Engine) {
  await $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true })
}

test('band shows the last sync time and the other PC', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, () => BASE)
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'config-sync', surface, component: 'AbovePrompt', props: BAND_PROPS })
    const line = await ui.find({ key: 'sync-band' })
    expect(line?.text).toContain('동기화 3분 전')
    expect(line?.text).toContain('company-mac')
    await ui.unmount()
  }
  expect(calls.some(c => c[0] === '(detached)')).toBe(true)
})

test('band warns when the push was blocked', async ($, on) => {
  engine(on, [], () => ({ ...BASE, last_result: 'blocked', blocked: [{ file: 'CLAUDE.md', line: 2, text: 'x', word: 'corp' }] }))
  await start($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'config-sync', surface, component: 'AbovePrompt', props: BAND_PROPS })
    expect((await ui.find({ key: 'sync-band' }))?.text).toContain('금지어')
    await ui.unmount()
  }
})

test('/sync opens the pane', async ($, on) => {
  const opened: string[] = []
  engine(on, [], () => BASE, opened)
  await start($)
  const result = await $.command.run({ command: 'sync', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } })
  expect(opened).toContain('config-sync')
  expect(result.text).toContain('설정 동기화')
})

test('pane: s syncs, a toggles auto, a row shows its diff', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, args => (args[0] === 'diff' ? { text: '## 받을 변경\n+새 줄\n' } : BASE))
  await start($)
  for (const surface of SURFACES) {
    calls.length = 0
    const ui = await $.ui.mount({ plugin: 'config-sync', surface, component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
    await ui.press({ key: 'sync' })
    expect(calls).toContainEqual(['sync'])
    await ui.press({ key: 'auto' })
    expect(calls).toContainEqual(['auto', 'off'])
    await ui.press({ key: 'item:claude_md' })
    expect(calls).toContainEqual(['diff', 'claude_md'])
    expect((await ui.find({ key: 'detail' }))?.text ?? (await ui.find({ text: /새 줄/ }))?.text).toContain('새 줄')
    await ui.press({ key: 'back' })
    await ui.unmount()
  }
})

test('pane: share a PC-only skill and unshare a shared one', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, () => BASE)
  await start($)
  const ui = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
  await ui.press({ key: 'share:skills:design-notes' })
  expect(calls).toContainEqual(['share', 'skills', 'design-notes'])
  await ui.press({ key: 'group:skills' })
  await ui.press({ key: 'unshare:skills:brand' })
  expect(calls).toContainEqual(['unshare', 'skills', 'brand'])
  await ui.unmount()
})

test('pane: conflict, blocked and approval actions call the engine', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, () => ({
    ...BASE,
    require_approval: true,
    last_result: 'conflict',
    conflicts: [{ id: 'file:CLAUDE.md', kind: 'file', file: 'CLAUDE.md' }],
    blocked: [{ file: 'CLAUDE.md', line: 2, text: 'internal corp-host', word: 'corp-host' }],
    pending_approval: [{ remote: 'abc', files: ['mods/x/hooks/register.ts'], settings: [], commits: [{ sha: 'abc', pc: 'company-mac', time: 1, subject: 's' }] }],
  }))
  await start($)
  const ui = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
  await ui.press({ key: 'take:file:CLAUDE.md' })
  expect(calls).toContainEqual(['resolve', 'file:CLAUDE.md', 'take'])
  await ui.press({ key: 'allow-once' })
  expect(calls).toContainEqual(['allow-once'])
  await ui.press({ key: 'approve' })
  expect(calls).toContainEqual(['approve'])
  await ui.press({ key: 'reject' })
  expect(calls).toContainEqual(['reject'])
  await ui.unmount()
})

test('pane: a pasted public key registers the new PC', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, args => (args[0] === 'register-pc' ? { registered: 'company-mac' } : { ...BASE, require_approval: true }))
  await start($)
  const ui = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
  await ui.input({ key: 'register', text: KEY })
  expect(calls).toContainEqual(['register-pc', KEY])
  await ui.unmount()
})

test('pane: history lists entries and reverts one', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, args =>
    args[0] === 'log' ? [{ sha: 'abc1234', time: 1_800_000_000, pc: 'company-mac', subject: 'sync: company-mac: 지침', files: ['CLAUDE.md'] }] : BASE,
  )
  await start($)
  const ui = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
  await ui.press({ key: 'history' })
  expect((await ui.find({ text: /company-mac/ }))).toBeDefined()
  await ui.press({ key: 'revert:abc1234' })
  expect(calls).toContainEqual(['revert', 'abc1234'])
  await ui.unmount()
})

test('session end starts a detached sync unless it is a clear', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, () => BASE)
  await start($)
  calls.length = 0
  await $.session.end({ reason: 'clear', sessionId: 's', resume: { id: 's' } })
  expect(calls.some(c => c[0] === '(detached)')).toBe(false)
  await $.session.end({ reason: 'prompt_input_exit', sessionId: 's', resume: { id: 's' } })
  expect(calls.some(c => c[0] === '(detached)')).toBe(true)
})

test('receive-only PC: band says so and the pane offers no sharing', async ($, on) => {
  const calls: Calls = []
  engine(on, calls, () => ({ ...BASE, receive_only: true }))
  await start($)
  const band = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })
  expect((await band.find({ key: 'sync-band' }))?.text).toContain('받기 전용')
  await band.unmount()
  const ui = await $.ui.mount({ plugin: 'config-sync', surface: 'terminal', component: 'Pane', requestId: 'config-sync', props: PANE_PROPS })
  expect(await ui.find({ key: 'share:skills:design-notes' })).toBeUndefined()
  expect((await ui.find({ text: /받기 전용/ }))).toBeDefined()
  await ui.unmount()
})
