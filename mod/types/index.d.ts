export type ItemState = 'ok' | 'local' | 'incoming' | 'conflict'

export type Hit = { file: string; line: number; text: string; word: string }

export type Conflict = {
  id: string
  kind: 'file' | 'setting'
  file?: string
  path?: string[]
  local?: unknown
  remote?: unknown
}

export type Approval = {
  remote: string
  files: string[]
  settings: string[][]
  commits: { sha: string; pc: string; time: number; subject: string }[]
}

export type Status = {
  pc: string
  now: number
  last_pull: number | null
  last_push: number | null
  last_result: 'ok' | 'offline' | 'blocked' | 'conflict' | 'needs_approval' | 'error' | 'never'
  last_error: string | null
  auto_enabled: boolean
  require_approval: boolean
  receive_only?: boolean
  backend?: 'git' | 'notion'
  others: { pc: string; time: number }[]
  items: {
    claude_md: { state: ItemState }
    settings: { count: number; keys: string[]; state: ItemState }
    skills: { name: string; state: ItemState }[]
    mods: { name: string; state: ItemState }[]
  }
  local_only: { skills: string[]; mods: string[]; settings: string[] }
  incoming: number
  outgoing: number
  conflicts: Conflict[]
  blocked: Hit[]
  pending_approval: Approval[]
  collisions: { kind: string; name: string; reason: string }[]
  words: string[]
}

export type LogEntry = { sha: string; time: number; pc: string; subject: string; files: string[] }

export type View =
  | { name: 'main' }
  | { name: 'group'; kind: 'skills' | 'mods' }
  | { name: 'detail'; title: string; text: string }
  | { name: 'history'; entries: LogEntry[] }
  | { name: 'blocklist' }

declare module 'claude-code' {
  interface PluginState {
    'config-sync': { status: Status | null; view: View; busy: string | null }
  }
}
