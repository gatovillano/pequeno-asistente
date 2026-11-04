import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ConnectionConfig } from '../types';

interface HeaderProps {
  config: ConnectionConfig;
  onOpenDrawer: () => void;
  onOpenSettings: () => void;
  onNewThread: () => void;
  isStreaming?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  config,
  onOpenDrawer,
  onOpenSettings,
  onNewThread,
  isStreaming,
}) => {
  const modelShortName = config.activeModel
    ? config.activeModel.split('/').pop()?.replace('-instruct', '') || config.activeModel
    : 'Desconectado';

  return (
    <View style={styles.container}>
      <View style={styles.leftRow}>
        <TouchableOpacity
          style={styles.iconButton}
          onPress={onOpenDrawer}
          accessibilityLabel="Abrir conversaciones"
        >
          <Ionicons name="menu" size={24} color="#f4f4f5" />
        </TouchableOpacity>

        <View style={styles.titleContainer}>
          <Text style={styles.title}>Pequeño Asistente</Text>
          <View style={styles.statusRow}>
            <View
              style={[
                styles.statusDot,
                { backgroundColor: config.isConnected ? '#10b981' : '#ef4444' },
              ]}
            />
            <Text style={styles.modelText} numberOfLines={1}>
              {config.isConnected ? modelShortName : 'Sin conexión'}
            </Text>
          </View>
        </View>
      </View>

      <View style={styles.rightRow}>
        {isStreaming && (
          <View style={styles.streamingBadge}>
            <ActivityIndicator size="small" color="#38bdf8" />
          </View>
        )}

        <TouchableOpacity
          style={styles.iconButton}
          onPress={onNewThread}
          accessibilityLabel="Nueva conversación"
        >
          <Ionicons name="add-circle-outline" size={24} color="#38bdf8" />
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.iconButton}
          onPress={onOpenSettings}
          accessibilityLabel="Configuración y túnel"
        >
          <Ionicons
            name={config.isConnected ? 'settings-outline' : 'warning-outline'}
            size={22}
            color={config.isConnected ? '#a1a1aa' : '#f59e0b'}
          />
        </TouchableOpacity>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    height: 60,
    backgroundColor: '#18181b',
    borderBottomWidth: 1,
    borderBottomColor: '#27272a',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
  },
  leftRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  iconButton: {
    padding: 8,
    borderRadius: 8,
  },
  titleContainer: {
    marginLeft: 8,
    flex: 1,
  },
  title: {
    color: '#fafafa',
    fontSize: 16,
    fontWeight: '700',
    letterSpacing: -0.3,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 2,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    marginRight: 5,
  },
  modelText: {
    color: '#a1a1aa',
    fontSize: 11,
    fontWeight: '500',
  },
  rightRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  streamingBadge: {
    marginRight: 4,
  },
});
