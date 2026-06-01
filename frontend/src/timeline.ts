import type { StreamItem } from './api'

export interface LocalMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  createdAt?: string
}

export type TimelineEntry =
  | {
      id: string
      source: 'stream'
      sequence: number
      timestamp?: string
      item: StreamItem
    }
  | {
      id: string
      source: 'chat'
      sequence: number
      timestamp?: string
      message: LocalMessage
    }

export interface TimelineState {
  entries: Record<string, TimelineEntry>
  order: string[]
  nextSequence: number
}

export function createTimelineState(): TimelineState {
  return { entries: {}, order: [], nextSequence: 0 }
}

export function cloneTimelineState(state: TimelineState): TimelineState {
  return {
    entries: { ...state.entries },
    order: [...state.order],
    nextSequence: state.nextSequence,
  }
}

export function appendStreamItem(state: TimelineState, item: StreamItem): TimelineState {
  const id = `stream:${item.id}`
  const existing = state.entries[id]
  if (existing?.source === 'stream') {
    state.entries[id] = { ...existing, item, timestamp: item.timestamp ?? existing.timestamp }
    return state
  }
  state.entries[id] = {
    id,
    source: 'stream',
    sequence: state.nextSequence,
    timestamp: item.timestamp,
    item,
  }
  state.order.push(id)
  state.nextSequence += 1
  return state
}

export function appendChatMessage(state: TimelineState, message: LocalMessage): TimelineState {
  const id = `chat:${message.id}`
  const existing = state.entries[id]
  if (existing?.source === 'chat') {
    state.entries[id] = { ...existing, message, timestamp: message.createdAt ?? existing.timestamp }
    return state
  }
  state.entries[id] = {
    id,
    source: 'chat',
    sequence: state.nextSequence,
    timestamp: message.createdAt,
    message,
  }
  state.order.push(id)
  state.nextSequence += 1
  return state
}

export function orderedTimelineEntries(state: TimelineState): TimelineEntry[] {
  return state.order
    .map((id) => state.entries[id])
    .filter((entry): entry is TimelineEntry => Boolean(entry))
}

export function createTimelineStateFromHistory(
  streamItems: StreamItem[],
  streamOrder: string[],
  chatMessages: LocalMessage[],
): TimelineState {
  const streamById = new Map(streamItems.map((item) => [item.id, item]))
  const candidates: Array<{ timestamp?: string; fallback: number; entry: StreamItem | LocalMessage; source: 'stream' | 'chat' }> = []

  streamOrder.forEach((id, index) => {
    const item = streamById.get(id)
    if (item) {
      candidates.push({ timestamp: item.timestamp, fallback: index, entry: item, source: 'stream' })
    }
  })

  chatMessages.forEach((message, index) => {
    candidates.push({
      timestamp: message.createdAt,
      fallback: streamOrder.length + index,
      entry: message,
      source: 'chat',
    })
  })

  const parseTime = (value?: string) => {
    if (!value) return null
    const parsed = Date.parse(value)
    return Number.isNaN(parsed) ? null : parsed
  }

  const ordered = [...candidates].sort((a, b) => {
    const aTime = parseTime(a.timestamp)
    const bTime = parseTime(b.timestamp)
    if (aTime !== null && bTime !== null) {
      return aTime - bTime || a.fallback - b.fallback
    }
    if (aTime === null && bTime !== null) return -1
    if (aTime !== null && bTime === null) return 1
    return a.fallback - b.fallback
  })

  const state = createTimelineState()
  for (const candidate of ordered) {
    if (candidate.source === 'stream') {
      appendStreamItem(state, candidate.entry as StreamItem)
    } else {
      appendChatMessage(state, candidate.entry as LocalMessage)
    }
  }
  return state
}
