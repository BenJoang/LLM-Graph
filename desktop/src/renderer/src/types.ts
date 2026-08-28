export interface Profile {
  name: string
  model: string
  support_tools: boolean
}

export interface Session {
  id: string
  title: string
  profile_name: string
  vision_profile_name: string
  working_dir: string
  context_window_tokens: number
  recursion_limit: number
  created_at: string
  updated_at: string
  archived: boolean
}

export interface ToolCall {
  id?: string
  name?: string
  args?: Record<string, unknown>
}

export interface Message {
  id: string
  role: string
  content: string
  tool_calls: ToolCall[]
  tool_call_id?: string | null
  name?: string | null
  status?: string | null
}

export interface SessionDetail {
  session: Session
  messages: Message[]
}

export interface StreamEvent {
  type: string
  data: Record<string, unknown>
}

export interface LiveTool {
  callId: string
  name: string
  args: Record<string, unknown>
  content?: string
  status: string
  duration?: number | null
}

export interface LiveTimelineStep {
  type: 'step'
  key: string
  content: string
}

export interface LiveTimelineTool extends LiveTool {
  type: 'tool'
  key: string
}

export type LiveTimelinePart = LiveTimelineStep | LiveTimelineTool
