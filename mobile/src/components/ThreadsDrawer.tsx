import React from 'react';
import {
  Modal,
  View,
  Text,
  TouchableOpacity,
  FlatList,
  StyleSheet,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ThreadItem } from '../types';

interface ThreadsDrawerProps {
  visible: boolean;
  onClose: () => void;
  threads: ThreadItem[];
  activeThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: string) => void;
}

export const ThreadsDrawer: React.FC<ThreadsDrawerProps> = ({
  visible,
  onClose,
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  onDeleteThread,
}) => {
  const confirmDelete = (thread: ThreadItem) => {
    Alert.alert(
      'Eliminar Conversación',
      `¿Deseas eliminar "${thread.title}"?`,
      [
        { text: 'Cancelar', style: 'cancel' },
        {
          text: 'Eliminar',
          style: 'destructive',
          onPress: () => onDeleteThread(thread.id),
        },
      ]
    );
  };

  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <View style={styles.drawerContainer}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerTitleRow}>
              <Ionicons name="chatbubbles-outline" size={20} color="#38bdf8" />
              <Text style={styles.headerTitle}>Conversaciones</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={20} color="#a1a1aa" />
            </TouchableOpacity>
          </View>

          {/* New Thread Button */}
          <TouchableOpacity
            style={styles.newThreadBtn}
            onPress={() => {
              onNewThread();
              onClose();
            }}
          >
            <Ionicons name="add" size={18} color="#ffffff" />
            <Text style={styles.newThreadBtnText}>Nueva Conversación</Text>
          </TouchableOpacity>

          {/* Threads List */}
          <FlatList
            data={threads}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.listContent}
            ListEmptyComponent={
              <View style={styles.emptyContainer}>
                <Text style={styles.emptyText}>No hay conversaciones previas.</Text>
              </View>
            }
            renderItem={({ item }) => {
              const isActive = item.id === activeThreadId;
              return (
                <View
                  style={[
                    styles.threadItemRow,
                    isActive && styles.threadItemRowActive,
                  ]}
                >
                  <TouchableOpacity
                    style={styles.threadItemMain}
                    onPress={() => {
                      onSelectThread(item.id);
                      onClose();
                    }}
                  >
                    <Ionicons
                      name={isActive ? 'chatbubble' : 'chatbubble-outline'}
                      size={16}
                      color={isActive ? '#38bdf8' : '#71717a'}
                    />
                    <View style={styles.threadTextCol}>
                      <Text
                        style={[
                          styles.threadTitle,
                          isActive && styles.threadTitleActive,
                        ]}
                        numberOfLines={1}
                      >
                        {item.title}
                      </Text>
                      {item.updated_at && (
                        <Text style={styles.threadDate} numberOfLines={1}>
                          {item.updated_at.slice(0, 10)}
                        </Text>
                      )}
                    </View>
                  </TouchableOpacity>

                  <TouchableOpacity
                    style={styles.deleteBtn}
                    onPress={() => confirmDelete(item)}
                  >
                    <Ionicons name="trash-outline" size={16} color="#71717a" />
                  </TouchableOpacity>
                </View>
              );
            }}
          />
        </View>

        {/* Backdrop touch to close */}
        <TouchableOpacity style={styles.backdrop} onPress={onClose} />
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    flexDirection: 'row',
  },
  drawerContainer: {
    width: '78%',
    backgroundColor: '#18181b',
    borderRightWidth: 1,
    borderRightColor: '#27272a',
    paddingTop: 40,
    zIndex: 10,
  },
  backdrop: {
    width: '22%',
    backgroundColor: 'rgba(0,0,0,0.6)',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#27272a',
  },
  headerTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  headerTitle: {
    color: '#fafafa',
    fontSize: 16,
    fontWeight: '700',
  },
  closeBtn: {
    padding: 4,
  },
  newThreadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: '#2563eb',
    margin: 14,
    paddingVertical: 10,
    borderRadius: 10,
  },
  newThreadBtnText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '600',
  },
  listContent: {
    paddingHorizontal: 10,
    paddingBottom: 30,
  },
  emptyContainer: {
    padding: 24,
    alignItems: 'center',
  },
  emptyText: {
    color: '#71717a',
    fontSize: 13,
  },
  threadItemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 8,
    marginVertical: 2,
  },
  threadItemRowActive: {
    backgroundColor: '#202028',
    borderLeftWidth: 3,
    borderLeftColor: '#38bdf8',
  },
  threadItemMain: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    flex: 1,
  },
  threadTextCol: {
    flex: 1,
  },
  threadTitle: {
    color: '#d4d4d8',
    fontSize: 14,
    fontWeight: '500',
  },
  threadTitleActive: {
    color: '#38bdf8',
    fontWeight: '600',
  },
  threadDate: {
    color: '#71717a',
    fontSize: 10,
    marginTop: 2,
  },
  deleteBtn: {
    padding: 6,
  },
});
