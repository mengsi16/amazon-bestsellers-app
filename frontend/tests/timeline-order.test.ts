import assert from 'node:assert/strict'
import {
  appendChatMessage,
  appendStreamItem,
  createTimelineState,
  createTimelineStateFromHistory,
  orderedTimelineEntries,
} from '../src/timeline.ts'
import type { StreamItem } from '../src/api.ts'

const state = createTimelineState()

appendStreamItem(state, {
  id: 'old-analysis',
  kind: 'assistant_text',
  content: '旧分析结果',
  final: true,
} satisfies StreamItem)

appendChatMessage(state, {
  id: 'chat-user-hi',
  role: 'user',
  content: '你好',
  streaming: false,
})

appendStreamItem(state, {
  id: 'refresh-start',
  kind: 'system_note',
  content: '正在刷新排名',
  final: true,
} satisfies StreamItem)

const entries = orderedTimelineEntries(state)

assert.deepEqual(
  entries.map((entry) => entry.id),
  ['stream:old-analysis', 'chat:chat-user-hi', 'stream:refresh-start'],
)

const historyState = createTimelineStateFromHistory(
  [
    {
      id: 'old-refresh',
      kind: 'system_note',
      content: '旧刷新',
      final: true,
      timestamp: '2026-05-31T06:18:00.000000',
    },
    {
      id: 'new-refresh',
      kind: 'system_note',
      content: '新刷新',
      final: true,
      timestamp: '2026-05-31T16:11:00.000000',
    },
  ],
  ['old-refresh', 'new-refresh'],
  [
    {
      id: 'chat-user-hi',
      role: 'user',
      content: '你好',
      streaming: false,
      createdAt: '2026-05-31T13:06:25.244650',
    },
  ],
)

assert.deepEqual(
  orderedTimelineEntries(historyState).map((entry) => entry.id),
  ['stream:old-refresh', 'chat:chat-user-hi', 'stream:new-refresh'],
)

const partialTimestampHistoryState = createTimelineStateFromHistory(
  [
    {
      id: 'legacy-without-timestamp',
      kind: 'system_note',
      content: '旧历史',
      final: true,
    },
    {
      id: 'new-refresh-after-chat',
      kind: 'system_note',
      content: '收到增量更新请求',
      final: true,
      timestamp: '2026-06-01T02:51:07.000000',
    },
  ],
  ['legacy-without-timestamp', 'new-refresh-after-chat'],
  [
    {
      id: 'chat-user-history-hi',
      role: 'user',
      content: '你好',
      streaming: false,
      createdAt: '2026-06-01T02:50:23.000000',
    },
  ],
)

assert.deepEqual(
  orderedTimelineEntries(partialTimestampHistoryState).map((entry) => entry.id),
  [
    'stream:legacy-without-timestamp',
    'chat:chat-user-history-hi',
    'stream:new-refresh-after-chat',
  ],
)

const liveRefreshAfterChatState = createTimelineState()

appendStreamItem(liveRefreshAfterChatState, {
  id: 'analysis-complete',
  kind: 'system_note',
  content: '分析完成',
  final: true,
} satisfies StreamItem)

appendChatMessage(liveRefreshAfterChatState, {
  id: 'chat-user-hi-2',
  role: 'user',
  content: '你好',
  streaming: false,
})

appendChatMessage(liveRefreshAfterChatState, {
  id: 'chat-assistant-hi-2',
  role: 'assistant',
  content: '你好，有什么需要帮助的吗？',
  streaming: false,
})

appendStreamItem(liveRefreshAfterChatState, {
  id: 'refresh-start-2',
  kind: 'system_note',
  content: '正在刷新排名',
  final: true,
} satisfies StreamItem)

appendStreamItem(liveRefreshAfterChatState, {
  id: 'refresh-backend-2',
  kind: 'system_note',
  content: '收到增量更新请求',
  final: true,
} satisfies StreamItem)

appendStreamItem(liveRefreshAfterChatState, {
  id: 'refresh-done-2',
  kind: 'system_note',
  content: '刷新流程已结束，未收到排名变化统计',
  final: true,
} satisfies StreamItem)

assert.deepEqual(
  orderedTimelineEntries(liveRefreshAfterChatState).map((entry) => entry.id),
  [
    'stream:analysis-complete',
    'chat:chat-user-hi-2',
    'chat:chat-assistant-hi-2',
    'stream:refresh-start-2',
    'stream:refresh-backend-2',
    'stream:refresh-done-2',
  ],
)
