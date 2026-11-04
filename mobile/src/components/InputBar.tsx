import React, { useState } from 'react';
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Platform,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Audio } from 'expo-av';

interface InputBarProps {
  onSendMessage: (text: string) => void;
  onAbort: () => void;
  onAudioRecorded?: (audioUri: string) => void;
  isStreaming: boolean;
  disabled?: boolean;
}

export const InputBar: React.FC<InputBarProps> = ({
  onSendMessage,
  onAbort,
  onAudioRecorded,
  isStreaming,
  disabled,
}) => {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [isRecording, setIsRecording] = useState(false);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;

    if (Platform.OS !== 'web') {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    }
    onSendMessage(trimmed);
    setText('');
  };

  const startRecording = async () => {
    try {
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert(
          'Permiso Requerido',
          'Se necesita acceso al micrófono para grabar notas de voz.'
        );
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording: newRecording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );
      setRecording(newRecording);
      setIsRecording(true);

      if (Platform.OS !== 'web') {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      }
    } catch (err: any) {
      Alert.alert('Error', 'No se pudo iniciar la grabación: ' + err.message);
    }
  };

  const stopRecording = async () => {
    if (!recording) return;

    try {
      setIsRecording(false);
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI();
      setRecording(null);

      if (uri && onAudioRecorded) {
        onAudioRecorded(uri);
      }
    } catch (err: any) {
      Alert.alert('Error', 'Error al procesar la grabación: ' + err.message);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.inputCard}>
        <TextInput
          style={styles.textInput}
          placeholder={
            isRecording
              ? 'Grabando audio…'
              : disabled
              ? 'Conecta al asistente primero…'
              : isStreaming
              ? 'Escribe para añadir a la cola…'
              : 'Escribe a Pequeño Asistente…'
          }
          placeholderTextColor="#71717a"
          value={text}
          onChangeText={setText}
          multiline
          maxLength={4000}
          editable={!disabled && !isRecording}
        />

        {/* Voice recording button */}
        <TouchableOpacity
          style={[styles.actionBtn, isRecording && styles.recordingBtn]}
          onPress={isRecording ? stopRecording : startRecording}
          disabled={disabled}
          accessibilityLabel={isRecording ? 'Detener grabación' : 'Grabar voz'}
        >
          <Ionicons
            name={isRecording ? 'stop-circle' : 'mic-outline'}
            size={22}
            color={isRecording ? '#ef4444' : '#a1a1aa'}
          />
        </TouchableOpacity>

        {/* Send and Abort buttons */}
        {isStreaming && (
          <TouchableOpacity
            style={[styles.actionBtn, styles.abortBtn]}
            onPress={onAbort}
            accessibilityLabel="Detener respuesta"
          >
            <Ionicons name="stop" size={16} color="#ffffff" />
          </TouchableOpacity>
        )}

        {(!isStreaming || text.trim().length > 0) && (
          <TouchableOpacity
            style={[
              styles.actionBtn,
              text.trim().length > 0 && styles.sendBtnActive,
            ]}
            onPress={handleSend}
            disabled={!text.trim() || disabled}
            accessibilityLabel={isStreaming ? 'Añadir a la cola' : 'Enviar mensaje'}
          >
            <Ionicons
              name={isStreaming ? 'time-outline' : 'arrow-up'}
              size={20}
              color={text.trim().length > 0 ? '#ffffff' : '#52525b'}
            />
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#18181b',
    borderTopWidth: 1,
    borderTopColor: '#27272a',
  },
  inputCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#202024',
    borderRadius: 24,
    borderWidth: 1,
    borderColor: '#2e2e34',
    paddingHorizontal: 12,
    paddingVertical: 4,
    minHeight: 46,
  },
  textInput: {
    flex: 1,
    color: '#fafafa',
    fontSize: 15,
    maxHeight: 120,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  actionBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: 4,
  },
  recordingBtn: {
    backgroundColor: '#3f1818',
  },
  sendBtnActive: {
    backgroundColor: '#2563eb',
  },
  abortBtn: {
    backgroundColor: '#dc2626',
  },
});
