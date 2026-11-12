export interface ToolCallItem {
  id: string;
  name: string;
  args?: Record<string, any>;
  result?: string;
  status: 'running' | 'done' | 'error';
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  reasoning_content?: string;
  tool_calls?: ToolCallItem[];
  timestamp?: string;
  isStreaming?: boolean;
  isQueued?: boolean;
}

export interface ThreadItem {
  id: string;
  title: string;
  created_at: string;
  updated_at?: string;
  message_count?: number;
}

export interface ConnectionConfig {
  url: string;
  apiKey: string;
  isConnected: boolean;
  activeModel?: string;
  activeProvider?: string;
  connectedMcpServers?: string[];
}

export interface PasswordRequestData {
  requestId: string;
  target: string;
  title: string;
  prompt: string;
  username?: string;
  showUsernameField?: boolean;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onThinking: (chunk: string) => void;
  onToolStart: (name: string, args: Record<string, any>) => void;
  onToolEnd: (name: string, result: string) => void;
  onPasswordRequest?: (data: PasswordRequestData) => void;
  onQueued?: (position?: number) => void;
  onDequeued?: () => void;
  onComplete: (finalContent: string, threadId: string) => void;
  onTitleUpdated?: (title: string, threadId: string) => void;
  onError: (error: string) => void;
}
