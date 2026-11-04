import React, { useState, useEffect, useRef } from 'react';
import {
  StyleSheet,
  View,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Alert,
  Text,
  TouchableOpacity,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { ChatMessage, ConnectionConfig, ThreadItem, ToolCallItem, PasswordRequestData } from './src/types';
import {
  loadSavedConfig,
  testConnection,
  fetchThreads,
  createNewThread,
  fetchThreadHistory,
  deleteThread,
  sendStreamingMessage,
  respondCredential,
} from './src/services/api';
import { Header } from './src/components/Header';
import { MessageItem } from './src/components/MessageItem';
import { InputBar } from './src/components/InputBar';
import { SettingsModal } from './src/components/SettingsModal';
import { ThreadsDrawer } from './src/components/ThreadsDrawer';
import { PasswordPromptModal } from './src/components/PasswordPromptModal';

export default function App() {
  const [config, setConfig] = useState<ConnectionConfig>({
    url: '',
    apiKey: '',
    isConnected: false,
  });

  const [threads, setThreads] = useState<ThreadItem[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [passwordRequest, setPasswordRequest] = useState<PasswordRequestData | null>(null);

  const [settingsVisible, setSettingsVisible] = useState(false);
  const [drawerVisible, setDrawerVisible] = useState(false);

  const flatListRef = useRef<FlatList>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);
  const activeThreadIdRef = useRef<string | null>(null);

  const selectActiveThread = (id: string | null) => {
    activeThreadIdRef.current = id;
    setActiveThreadId(id);
  };

  // Initialize app: load config and check server
  useEffect(() => {
    async function init() {
      const saved = await loadSavedConfig();
      if (!saved.url) {
        setConfig(saved);
        setSettingsVisible(true);
        return;
      }

      const check = await testConnection(saved.url, saved.apiKey);
      const updatedConfig: ConnectionConfig = {
        ...saved,
        isConnected: check.success,
        activeModel: check.data?.active_model,
        connectedMcpServers: check.data?.connected_mcp_servers,
      };
      setConfig(updatedConfig);

      if (check.success) {
        await refreshThreads(updatedConfig, undefined, true);
      } else {
        setSettingsVisible(true);
      }
    }
    init();
  }, []);

  const refreshThreads = async (
    currentConfig = config,
    explicitTargetId?: string | null,
    fetchHistory: boolean = false
  ) => {
    if (!currentConfig.url || !currentConfig.isConnected) return;
    try {
      const res = await fetchThreads(currentConfig.url, currentConfig.apiKey);
      setThreads(res.threads);

      let targetId: string | null =
        explicitTargetId !== undefined ? explicitTargetId : activeThreadIdRef.current;

      if (res.threads.length > 0) {
        const found = targetId ? res.threads.some((t) => t.id === targetId) : false;
        if (!found) {
          targetId =
            res.active_thread_id && res.threads.some((t) => t.id === res.active_thread_id)
              ? res.active_thread_id
              : res.threads[0].id;
          fetchHistory = true;
        }
      } else {
        targetId = null;
      }

      selectActiveThread(targetId);

      if (targetId) {
        if (fetchHistory) {
          await loadThreadMessages(currentConfig, targetId);
        }
      } else {
        setMessages([]);
      }
    } catch (err: any) {
      console.warn('Error fetching threads:', err.message);
    }
  };

  const loadThreadMessages = async (cfg: ConnectionConfig, threadId: string) => {
    try {
      const history = await fetchThreadHistory(cfg.url, cfg.apiKey, threadId);
      setMessages(history);
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: false });
      }, 100);
    } catch (err: any) {
      Alert.alert('Error', 'No se pudo cargar el historial: ' + err.message);
    }
  };

  const handleSelectThread = async (threadId: string) => {
    if (threadId === activeThreadIdRef.current) return;
    if (isStreaming) {
      handleAbort();
    }
    selectActiveThread(threadId);
    await loadThreadMessages(config, threadId);
  };

  const handleNewThread = async () => {
    if (!config.isConnected) {
      setSettingsVisible(true);
      return;
    }
    if (isStreaming) {
      handleAbort();
    }
    try {
      const newTh = await createNewThread(config.url, config.apiKey);
      selectActiveThread(newTh.id);
      setMessages([]);
      await refreshThreads(config, newTh.id, false);
    } catch (err: any) {
      Alert.alert('Error', 'No se pudo crear conversación: ' + err.message);
    }
  };

  const handleDeleteThread = async (threadId: string) => {
    try {
      await deleteThread(config.url, config.apiKey, threadId);
      const wasActive = activeThreadIdRef.current === threadId;
      const target = wasActive ? undefined : activeThreadIdRef.current;
      await refreshThreads(config, target, wasActive);
    } catch (err: any) {
      Alert.alert('Error', 'No se pudo eliminar el hilo: ' + err.message);
    }
  };

  const handleSendMessage = (text: string) => {
    if (!config.isConnected) {
      setSettingsVisible(true);
      return;
    }

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };

    const asstMsgId = `asst-${Date.now()}`;
    const placeholderAsstMsg: ChatMessage = {
      id: asstMsgId,
      role: 'assistant',
      content: '',
      isStreaming: true,
      tool_calls: [],
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg, placeholderAsstMsg]);
    setIsStreaming(true);

    setTimeout(() => {
      flatListRef.current?.scrollToEnd({ animated: true });
    }, 50);

    const currentThreadId = activeThreadIdRef.current;
    const cancelFn = sendStreamingMessage(
      config.url,
      config.apiKey,
      text,
      currentThreadId,
      {
        onToken: (token) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === asstMsgId
                ? { ...msg, content: msg.content + token }
                : msg
            )
          );
          flatListRef.current?.scrollToEnd({ animated: true });
        },
        onThinking: (chunk) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === asstMsgId
                ? {
                    ...msg,
                    reasoning_content: (msg.reasoning_content || '') + chunk,
                  }
                : msg
            )
          );
        },
        onToolStart: (name, args) => {
          const toolItem: ToolCallItem = {
            id: `tool-${Date.now()}-${Math.random()}`,
            name,
            args,
            status: 'running',
          };
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === asstMsgId
                ? {
                    ...msg,
                    tool_calls: [...(msg.tool_calls || []), toolItem],
                  }
                : msg
            )
          );
        },
        onToolEnd: (name, result) => {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== asstMsgId) return msg;
              const tools = (msg.tool_calls || []).map((t) =>
                t.name === name && t.status === 'running'
                  ? { ...t, result, status: 'done' as const }
                  : t
              );
              return { ...msg, tool_calls: tools };
            })
          );
        },
        onPasswordRequest: (data) => {
          setPasswordRequest(data);
        },
        onComplete: (finalContent, targetThreadId) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === asstMsgId
                ? {
                    ...msg,
                    content: finalContent || msg.content,
                    isStreaming: false,
                  }
                : msg
            )
          );
          setIsStreaming(false);
          cancelStreamRef.current = null;
          const finalId = targetThreadId || activeThreadIdRef.current;
          if (finalId) {
            selectActiveThread(finalId);
          }
          refreshThreads(config, finalId, false);
        },
        onError: (err) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === asstMsgId
                ? {
                    ...msg,
                    content: msg.content
                      ? `${msg.content}\n\n⚠️ Error: ${err}`
                      : `⚠️ Error: ${err}`,
                    isStreaming: false,
                  }
                : msg
            )
          );
          setIsStreaming(false);
          cancelStreamRef.current = null;
        },
      }
    );

    cancelStreamRef.current = cancelFn;
  };

  const handleAbort = () => {
    if (cancelStreamRef.current) {
      cancelStreamRef.current();
      cancelStreamRef.current = null;
    }
    setPasswordRequest(null);
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false } : msg))
    );
  };

  const handlePasswordSubmit = async (password: string, username?: string) => {
    if (!passwordRequest) return;
    const req = passwordRequest;
    setPasswordRequest(null);
    await respondCredential(config.url, config.apiKey, {
      requestId: req.requestId,
      secret: password,
      username,
      cancelled: false,
    });
  };

  const handlePasswordCancel = async () => {
    if (!passwordRequest) return;
    const req = passwordRequest;
    setPasswordRequest(null);
    await respondCredential(config.url, config.apiKey, {
      requestId: req.requestId,
      cancelled: true,
    });
  };

  const handleAudioRecorded = async (uri: string) => {
    if (!config.isConnected) {
      Alert.alert('Sin Conexión', 'Conecta la app primero.');
      return;
    }

    try {
      const formData = new FormData();
      formData.append('file', {
        uri,
        name: 'voice_note.m4a',
        type: 'audio/m4a',
      } as any);

      const headers: Record<string, string> = {
        'Content-Type': 'multipart/form-data',
      };
      if (config.apiKey) {
        headers['Authorization'] = `Bearer ${config.apiKey}`;
      }

      const res = await fetch(`${config.url}/api/audio/transcribe`, {
        method: 'POST',
        headers,
        body: formData,
      });

      if (!res.ok) {
        throw new Error(`Error en transcripción (${res.status})`);
      }

      const data = await res.json();
      if (data.text && data.text.trim()) {
        handleSendMessage(data.text.trim());
      } else {
        Alert.alert('Audio', 'No se detectaron palabras en el audio.');
      }
    } catch (err: any) {
      Alert.alert('Error', 'No se pudo transcribir el audio: ' + err.message);
    }
  };

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="light" backgroundColor="#18181b" />

        <Header
          config={config}
          onOpenDrawer={() => setDrawerVisible(true)}
          onOpenSettings={() => setSettingsVisible(true)}
          onNewThread={handleNewThread}
          isStreaming={isStreaming}
        />

        {/* Offline Warning Banner */}
        {!config.isConnected && (
          <TouchableOpacity
            style={styles.offlineBanner}
            onPress={() => setSettingsVisible(true)}
            activeOpacity={0.8}
          >
            <Ionicons name="cloud-offline-outline" size={16} color="#fbbf24" />
            <Text style={styles.offlineBannerText}>
              Sin conexión. Toca aquí para configurar tu URL de Cloudflare.
            </Text>
          </TouchableOpacity>
        )}

        <KeyboardAvoidingView
          style={styles.chatArea}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
        >
          <FlatList
            ref={flatListRef}
            data={messages}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <MessageItem message={item} />}
            contentContainerStyle={styles.messagesList}
            ListEmptyComponent={
              <View style={styles.emptyContainer}>
                <Text style={styles.emptyLogo}>🤖</Text>
                <Text style={styles.emptyTitle}>Pequeño Asistente Remoto</Text>
                <Text style={styles.emptySubtitle}>
                  Conectado a tu PC Linux mediante Cloudflare Tunnel y FastAPI.
                  {'\n'}Tienes acceso a tus archivos, comandos, herramientas MCP y memoria.
                </Text>
              </View>
            }
          />

          <InputBar
            onSendMessage={handleSendMessage}
            onAbort={handleAbort}
            onAudioRecorded={handleAudioRecorded}
            isStreaming={isStreaming}
            disabled={!config.isConnected}
          />
        </KeyboardAvoidingView>

        {/* Modals & Drawers */}
        <SettingsModal
          visible={settingsVisible}
          onClose={() => setSettingsVisible(false)}
          config={config}
          onConfigUpdated={async (newCfg) => {
            setConfig(newCfg);
            if (newCfg.isConnected) {
              await refreshThreads(newCfg, undefined, true);
            }
          }}
        />

        <ThreadsDrawer
          visible={drawerVisible}
          onClose={() => setDrawerVisible(false)}
          threads={threads}
          activeThreadId={activeThreadId}
          onSelectThread={handleSelectThread}
          onNewThread={handleNewThread}
          onDeleteThread={handleDeleteThread}
        />

        <PasswordPromptModal
          visible={!!passwordRequest}
          data={passwordRequest}
          onSubmit={handlePasswordSubmit}
          onCancel={handlePasswordCancel}
        />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#121214',
  },
  chatArea: {
    flex: 1,
  },
  messagesList: {
    paddingVertical: 12,
    flexGrow: 1,
  },
  offlineBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: '#3b2506',
    borderBottomWidth: 1,
    borderBottomColor: '#78350f',
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  offlineBannerText: {
    color: '#fef3c7',
    fontSize: 12,
    fontWeight: '500',
  },
  emptyContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    paddingVertical: 60,
  },
  emptyLogo: {
    fontSize: 48,
    marginBottom: 12,
  },
  emptyTitle: {
    color: '#fafafa',
    fontSize: 18,
    fontWeight: '700',
    marginBottom: 8,
    textAlign: 'center',
  },
  emptySubtitle: {
    color: '#71717a',
    fontSize: 13,
    lineHeight: 19,
    textAlign: 'center',
  },
});
