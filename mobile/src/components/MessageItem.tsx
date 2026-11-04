import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Markdown from 'react-native-markdown-display';
import { ChatMessage, ToolCallItem } from '../types';

interface MessageItemProps {
  message: ChatMessage;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const [showThinking, setShowThinking] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});

  const toggleTool = (id: string) => {
    setExpandedTools((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  if (isUser) {
    return (
      <View style={styles.userContainer}>
        <View style={[styles.userBubble, message.isQueued && styles.userBubbleQueued]}>
          <Text style={styles.userText}>{message.content}</Text>
          {message.isQueued && (
            <View style={styles.queuedBadge}>
              <Text style={styles.queuedBadgeText}>⏳ En cola</Text>
            </View>
          )}
        </View>
      </View>
    );
  }

  return (
    <View style={styles.assistantContainer}>
      <View style={styles.assistantHeaderRow}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>🤖</Text>
        </View>
        <Text style={styles.assistantLabel}>Pequeño Asistente</Text>
      </View>

      {/* Thinking / CoT reasoning block */}
      {!!message.reasoning_content && (
        <View style={styles.thinkingBox}>
          <TouchableOpacity
            style={styles.thinkingHeader}
            onPress={() => setShowThinking(!showThinking)}
            activeOpacity={0.7}
          >
            <View style={styles.thinkingTitleRow}>
              <Ionicons name="bulb-outline" size={14} color="#a1a1aa" />
              <Text style={styles.thinkingTitle}>Pensamiento del modelo</Text>
            </View>
            <Ionicons
              name={showThinking ? 'chevron-up' : 'chevron-down'}
              size={14}
              color="#a1a1aa"
            />
          </TouchableOpacity>
          {showThinking && (
            <Text style={styles.thinkingContent}>{message.reasoning_content}</Text>
          )}
        </View>
      )}

      {/* MCP Tool executions */}
      {message.tool_calls && message.tool_calls.length > 0 && (
        <View style={styles.toolsWrapper}>
          {message.tool_calls.map((tool) => (
            <View key={tool.id} style={styles.toolCard}>
              <TouchableOpacity
                style={styles.toolHeader}
                onPress={() => toggleTool(tool.id)}
                activeOpacity={0.7}
              >
                <View style={styles.toolLeft}>
                  <Ionicons
                    name="build-outline"
                    size={14}
                    color={tool.status === 'running' ? '#38bdf8' : '#34d399'}
                  />
                  <Text style={styles.toolName}>{tool.name}</Text>
                </View>
                <View style={styles.toolRight}>
                  <Text
                    style={[
                      styles.toolStatus,
                      {
                        color:
                          tool.status === 'running'
                            ? '#38bdf8'
                            : tool.status === 'error'
                            ? '#f87171'
                            : '#34d399',
                      },
                    ]}
                  >
                    {tool.status === 'running'
                      ? 'Ejecutando…'
                      : tool.status === 'error'
                      ? 'Error'
                      : 'Listo'}
                  </Text>
                  <Ionicons
                    name={expandedTools[tool.id] ? 'chevron-up' : 'chevron-down'}
                    size={13}
                    color="#71717a"
                  />
                </View>
              </TouchableOpacity>

              {expandedTools[tool.id] && (
                <View style={styles.toolBody}>
                  {tool.args && (
                    <View style={styles.toolSection}>
                      <Text style={styles.toolSectionLabel}>Parámetros:</Text>
                      <Text style={styles.toolJson}>
                        {JSON.stringify(tool.args, null, 2)}
                      </Text>
                    </View>
                  )}
                  {tool.result && (
                    <View style={styles.toolSection}>
                      <Text style={styles.toolSectionLabel}>Resultado:</Text>
                      <Text style={styles.toolResultText}>{tool.result}</Text>
                    </View>
                  )}
                </View>
              )}
            </View>
          ))}
        </View>
      )}

      {/* Main assistant message bubble */}
      <View style={styles.assistantBubble}>
        <Markdown style={markdownStyles}>
          {message.content ? message.content : message.isStreaming ? 'Pensando…' : ''}
        </Markdown>
        {message.isStreaming && !!message.content && (
          <Text style={styles.cursorBlink}>▋</Text>
        )}
      </View>
    </View>
  );
};

const markdownStyles = {
  body: {
    color: '#f4f4f5',
    fontSize: 15,
    lineHeight: 22,
  },
  paragraph: {
    marginTop: 0,
    marginBottom: 6,
  },
  strong: {
    fontWeight: '700' as const,
    color: '#ffffff',
  },
  em: {
    fontStyle: 'italic' as const,
    color: '#e4e4e7',
  },
  heading1: {
    fontSize: 20,
    fontWeight: '700' as const,
    color: '#ffffff',
    marginVertical: 8,
  },
  heading2: {
    fontSize: 18,
    fontWeight: '700' as const,
    color: '#ffffff',
    marginVertical: 6,
  },
  heading3: {
    fontSize: 16,
    fontWeight: '600' as const,
    color: '#38bdf8',
    marginVertical: 4,
  },
  heading4: {
    fontSize: 15,
    fontWeight: '600' as const,
    color: '#93c5fd',
    marginVertical: 4,
  },
  heading5: {
    fontSize: 14,
    fontWeight: '600' as const,
    color: '#e4e4e7',
    marginVertical: 3,
  },
  heading6: {
    fontSize: 14,
    fontWeight: '600' as const,
    color: '#a1a1aa',
    marginVertical: 2,
  },
  code_inline: {
    backgroundColor: '#161619',
    color: '#38bdf8',
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: '#2e2e34',
    fontSize: 13,
  },
  code_block: {
    backgroundColor: '#121214',
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2e2e34',
    marginVertical: 6,
  },
  fence: {
    backgroundColor: '#121214',
    color: '#e4e4e7',
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2e2e34',
    fontSize: 13,
    marginVertical: 6,
  },
  bullet_list: {
    marginVertical: 4,
  },
  ordered_list: {
    marginVertical: 4,
  },
  list_item: {
    flexDirection: 'row' as const,
    marginBottom: 4,
  },
  bullet_list_icon: {
    color: '#38bdf8',
    marginRight: 6,
  },
  ordered_list_icon: {
    color: '#38bdf8',
    marginRight: 6,
  },
  link: {
    color: '#38bdf8',
    textDecorationLine: 'underline' as const,
  },
  blockquote: {
    backgroundColor: '#18181b',
    borderLeftColor: '#38bdf8',
    borderLeftWidth: 3,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginVertical: 6,
  },
  hr: {
    backgroundColor: '#2e2e34',
    height: 1,
    marginVertical: 8,
  },
};

const styles = StyleSheet.create({
  userContainer: {
    marginVertical: 6,
    alignItems: 'flex-end',
    paddingHorizontal: 12,
  },
  userBubble: {
    backgroundColor: '#2563eb',
    paddingHorizontal: 15,
    paddingVertical: 10,
    borderRadius: 18,
    borderBottomRightRadius: 4,
    maxWidth: '85%',
  },
  userBubbleQueued: {
    backgroundColor: '#1d4ed8',
    opacity: 0.85,
  },
  queuedBadge: {
    marginTop: 4,
    alignSelf: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.25)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  queuedBadgeText: {
    color: '#e0e7ff',
    fontSize: 11,
    fontWeight: '500',
  },
  userText: {
    color: '#ffffff',
    fontSize: 15,
    lineHeight: 21,
  },
  assistantContainer: {
    marginVertical: 8,
    alignItems: 'flex-start',
    paddingHorizontal: 12,
  },
  assistantHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 6,
  },
  avatar: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#27272a',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 6,
  },
  avatarText: {
    fontSize: 13,
  },
  assistantLabel: {
    color: '#a1a1aa',
    fontSize: 12,
    fontWeight: '600',
  },
  assistantBubble: {
    backgroundColor: '#202024',
    paddingHorizontal: 15,
    paddingVertical: 12,
    borderRadius: 18,
    borderTopLeftRadius: 4,
    maxWidth: '92%',
    borderWidth: 1,
    borderColor: '#2d2d33',
  },
  assistantText: {
    color: '#f4f4f5',
    fontSize: 15,
    lineHeight: 22,
  },
  cursorBlink: {
    color: '#38bdf8',
    fontWeight: 'bold',
  },
  thinkingBox: {
    backgroundColor: '#18181b',
    borderWidth: 1,
    borderColor: '#2e2e34',
    borderRadius: 10,
    padding: 8,
    marginBottom: 8,
    maxWidth: '92%',
  },
  thinkingHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  thinkingTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  thinkingTitle: {
    color: '#a1a1aa',
    fontSize: 12,
    fontWeight: '500',
  },
  thinkingContent: {
    color: '#71717a',
    fontSize: 12,
    fontStyle: 'italic',
    marginTop: 6,
    lineHeight: 18,
  },
  toolsWrapper: {
    marginBottom: 8,
    maxWidth: '92%',
    gap: 6,
  },
  toolCard: {
    backgroundColor: '#1b1b1f',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2e2e36',
    overflow: 'hidden',
  },
  toolHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  toolLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  toolName: {
    color: '#e4e4e7',
    fontSize: 12,
    fontFamily: 'monospace',
    fontWeight: '600',
  },
  toolRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  toolStatus: {
    fontSize: 11,
    fontWeight: '500',
  },
  toolBody: {
    borderTopWidth: 1,
    borderTopColor: '#27272a',
    padding: 8,
    backgroundColor: '#141417',
  },
  toolSection: {
    marginTop: 4,
  },
  toolSectionLabel: {
    color: '#71717a',
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  toolJson: {
    color: '#93c5fd',
    fontSize: 11,
    fontFamily: 'monospace',
  },
  toolResultText: {
    color: '#a1a1aa',
    fontSize: 11,
    fontFamily: 'monospace',
  },
});
