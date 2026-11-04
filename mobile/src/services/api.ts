import AsyncStorage from '@react-native-async-storage/async-storage';
import { ConnectionConfig, ThreadItem, ChatMessage, StreamCallbacks } from '../types';

const STORAGE_KEY_URL = '@pequeno_asistente_url';
const STORAGE_KEY_KEY = '@pequeno_asistente_key';
const DEFAULT_SERVER_URL = 'https://api-pa.kognitoai.cloud';

export function normalizeUrl(url: string): string {
  let clean = url.trim().replace(/\/+$/, '');
  if (!clean) return '';
  if (!clean.startsWith('http://') && !clean.startsWith('https://')) {
    if (clean.includes('localhost') || /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/.test(clean)) {
      clean = 'http://' + clean;
    } else {
      clean = 'https://' + clean;
    }
  }
  return clean;
}

export async function loadSavedConfig(): Promise<ConnectionConfig> {
  try {
    const rawUrl = await AsyncStorage.getItem(STORAGE_KEY_URL);
    const url = normalizeUrl(rawUrl || DEFAULT_SERVER_URL);
    const apiKey = (await AsyncStorage.getItem(STORAGE_KEY_KEY)) || '';
    return {
      url,
      apiKey: apiKey.trim(),
      isConnected: false,
    };
  } catch {
    return { url: DEFAULT_SERVER_URL, apiKey: '', isConnected: false };
  }
}

export async function saveConfig(url: string, apiKey: string): Promise<void> {
  const cleanUrl = normalizeUrl(url);
  await AsyncStorage.setItem(STORAGE_KEY_URL, cleanUrl);
  await AsyncStorage.setItem(STORAGE_KEY_KEY, apiKey.trim());
}

function getHeaders(apiKey: string): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
  };
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`;
  }
  return headers;
}

export async function testConnection(
  url: string,
  apiKey: string
): Promise<{ success: boolean; data?: any; error?: string }> {
  try {
    const cleanUrl = normalizeUrl(url);
    if (!cleanUrl) {
      return { success: false, error: 'Por favor ingresa una URL válida.' };
    }

    const res = await fetch(`${cleanUrl}/health`, {
      method: 'GET',
      headers: getHeaders(apiKey),
    });

    if (res.status === 401 || res.status === 403) {
      return {
        success: false,
        error: 'Error de autenticación. Verifica tu API Key.',
      };
    }

    if (!res.ok) {
      return {
        success: false,
        error: `Servidor respondió con código ${res.status}: ${res.statusText}`,
      };
    }

    const data = await res.json();
    return { success: true, data };
  } catch (err: any) {
    return {
      success: false,
      error: `Error de red al conectar: ${err.message || 'Verifica que la URL incluya https://'}`,
    };
  }
}

export async function fetchThreads(
  url: string,
  apiKey: string
): Promise<{ threads: ThreadItem[]; activeThreadId?: string; active_thread_id?: string }> {
  const cleanUrl = normalizeUrl(url);
  const res = await fetch(`${cleanUrl}/api/threads`, {
    headers: getHeaders(apiKey),
  });
  if (!res.ok) {
    throw new Error(`Error cargando hilos (${res.status})`);
  }
  const data = await res.json();
  const activeId = data.active_thread_id || data.activeThreadId;
  return {
    threads: data.threads || [],
    activeThreadId: activeId,
    active_thread_id: activeId,
  };
}

export async function createNewThread(
  url: string,
  apiKey: string,
  title?: string
): Promise<ThreadItem> {
  const cleanUrl = normalizeUrl(url);
  const res = await fetch(`${cleanUrl}/api/threads`, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify({ title: title || 'Nueva conversación' }),
  });
  if (!res.ok) {
    throw new Error(`Error creando hilo (${res.status})`);
  }
  return await res.json();
}

export async function fetchThreadHistory(
  url: string,
  apiKey: string,
  threadId: string
): Promise<ChatMessage[]> {
  const cleanUrl = normalizeUrl(url);
  const res = await fetch(`${cleanUrl}/api/threads/${threadId}`, {
    headers: getHeaders(apiKey),
  });
  if (!res.ok) {
    throw new Error(`Error cargando historial (${res.status})`);
  }
  const data = await res.json();
  const rawMsgs = data.messages || [];

  const parsedMessages: ChatMessage[] = [];
  for (let index = 0; index < rawMsgs.length; index++) {
    const m = rawMsgs[index];
    if (m.role === 'system' || m.role === 'tool') continue;

    if (m.role === 'user') {
      if (!m.content) continue;
      parsedMessages.push({
        id: `msg-${threadId}-${index}`,
        role: 'user',
        content: m.content,
        timestamp: m.timestamp,
      });
    } else if (m.role === 'assistant') {
      const toolCalls = (m.tool_calls || []).map((tc: any, i: number) => {
        let args = {};
        try {
          args = typeof tc.function?.arguments === 'string'
            ? JSON.parse(tc.function.arguments)
            : tc.function?.arguments || {};
        } catch {}
        return {
          id: tc.id || `tc-${index}-${i}`,
          name: tc.function?.name || 'herramienta',
          args,
          status: 'done' as const,
        };
      });

      if (!m.content && !m.reasoning_content && toolCalls.length === 0) continue;

      parsedMessages.push({
        id: `msg-${threadId}-${index}`,
        role: 'assistant',
        content: m.content || '',
        reasoning_content: m.reasoning_content,
        tool_calls: toolCalls,
        timestamp: m.timestamp,
      });
    }
  }

  return parsedMessages;
}

export async function deleteThread(
  url: string,
  apiKey: string,
  threadId: string
): Promise<boolean> {
  const cleanUrl = normalizeUrl(url);
  const res = await fetch(`${cleanUrl}/api/threads/${threadId}`, {
    method: 'DELETE',
    headers: getHeaders(apiKey),
  });
  return res.ok;
}

export async function abortTurn(
  url: string,
  apiKey: string,
  threadId?: string
): Promise<void> {
  const cleanUrl = normalizeUrl(url);
  await fetch(`${cleanUrl}/api/chat/abort`, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify({ thread_id: threadId }),
  });
}

export async function respondCredential(
  url: string,
  apiKey: string,
  params: {
    requestId: string;
    secret?: string;
    username?: string;
    cancelled?: boolean;
  }
): Promise<boolean> {
  const cleanUrl = normalizeUrl(url);
  try {
    const res = await fetch(`${cleanUrl}/api/security/credential-response`, {
      method: 'POST',
      headers: getHeaders(apiKey),
      body: JSON.stringify({
        request_id: params.requestId,
        secret: params.secret || '',
        username: params.username,
        cancelled: !!params.cancelled,
      }),
    });
    return res.ok;
  } catch (err) {
    console.warn('Failed to respond credential:', err);
    return false;
  }
}

/**
 * Sends a message and processes Server-Sent Events (SSE) streaming chunks.
 * Compatible with React Native's XMLHttpRequest progressive onreadystatechange.
 */
export function sendStreamingMessage(
  url: string,
  apiKey: string,
  message: string,
  threadId: string | null,
  callbacks: StreamCallbacks
): () => void {
  const cleanUrl = normalizeUrl(url);
  const xhr = new XMLHttpRequest();
  let seenIndex = 0;
  let isAborted = false;

  xhr.open('POST', `${cleanUrl}/api/chat`, true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.setRequestHeader('Accept', 'text/event-stream');
  if (apiKey) {
    xhr.setRequestHeader('Authorization', `Bearer ${apiKey}`);
  }

  const parseSSEBlock = (textBlock: string) => {
    const lines = textBlock.split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith(':')) continue;

      if (trimmed.startsWith('data:')) {
        const rawJson = trimmed.slice(5).trim();
        if (!rawJson) continue;
        try {
          const payload = JSON.parse(rawJson);
          switch (payload.type) {
            case 'queued':
              if (callbacks.onQueued) {
                callbacks.onQueued(payload.position);
              }
              break;
            case 'dequeued':
              if (callbacks.onDequeued) {
                callbacks.onDequeued();
              }
              break;
            case 'token':
              callbacks.onToken(payload.content);
              break;
            case 'thinking':
              callbacks.onThinking(payload.content);
              break;
            case 'tool_start':
              callbacks.onToolStart(payload.name, payload.args || {});
              break;
            case 'tool_end':
              callbacks.onToolEnd(payload.name, payload.result || '');
              break;
            case 'password_request':
              if (callbacks.onPasswordRequest) {
                callbacks.onPasswordRequest({
                  requestId: payload.request_id,
                  target: payload.target || 'sudo',
                  title: payload.title || 'Autenticación de Superusuario (sudo)',
                  prompt: payload.prompt || '',
                  username: payload.username,
                  showUsernameField: payload.show_username_field,
                });
              }
              break;
            case 'complete':
              callbacks.onComplete(payload.content || '', payload.thread_id || threadId || '');
              break;
            case 'error':
              callbacks.onError(payload.error || 'Error desconocido');
              break;
          }
        } catch {
          // Ignore incomplete JSON chunks until next fragment
        }
      }
    }
  };

  xhr.onreadystatechange = () => {
    if (isAborted) return;

    if (xhr.readyState === 3 || xhr.readyState === 4) {
      const currentText = xhr.responseText || '';
      const newChunk = currentText.substring(seenIndex);
      seenIndex = currentText.length;

      if (newChunk) {
        parseSSEBlock(newChunk);
      }
    }

    if (xhr.readyState === 4) {
      if (xhr.status >= 400) {
        let errMessage = `Error ${xhr.status}`;
        try {
          const parsed = JSON.parse(xhr.responseText);
          if (parsed.detail) errMessage = parsed.detail;
        } catch {}
        callbacks.onError(errMessage);
      }
    }
  };

  xhr.onerror = () => {
    if (!isAborted) {
      callbacks.onError('Error de conexión de red con el asistente.');
    }
  };

  const payload = JSON.stringify({
    message,
    thread_id: threadId,
    stream: true,
  });

  xhr.send(payload);

  // Return cancel function
  return () => {
    isAborted = true;
    xhr.abort();
    abortTurn(url, apiKey, threadId || undefined).catch(() => {});
  };
}
