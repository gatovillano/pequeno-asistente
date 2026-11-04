import React, { useState, useEffect } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { PasswordRequestData } from '../types';

interface PasswordPromptModalProps {
  visible: boolean;
  data: PasswordRequestData | null;
  onSubmit: (password: string, username?: string) => Promise<void>;
  onCancel: () => Promise<void>;
}

export const PasswordPromptModal: React.FC<PasswordPromptModalProps> = ({
  visible,
  data,
  onSubmit,
  onCancel,
}) => {
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (visible && data) {
      setPassword('');
      setUsername(data.username || '');
      setShowPassword(false);
      setIsSubmitting(false);
    }
  }, [visible, data]);

  if (!visible || !data) return null;

  const isSudo = data.target === 'sudo';
  const accentColor = isSudo ? '#ef4444' : '#f59e0b';

  const handleConfirm = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    try {
      await onSubmit(password, data.showUsernameField ? username : undefined);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDismiss = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    try {
      await onCancel();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={handleDismiss}
    >
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.card}>
          {/* Header */}
          <View style={styles.header}>
            <View style={[styles.iconWrap, { backgroundColor: isSudo ? '#450a0a' : '#451a03', borderColor: accentColor }]}>
              <Ionicons
                name={isSudo ? 'shield-checkmark' : 'key'}
                size={28}
                color={accentColor}
              />
            </View>
            <View style={styles.headerText}>
              <Text style={styles.title}>{data.title || 'Autenticación Requerida'}</Text>
              <View style={styles.badgeRow}>
                <View style={[styles.badge, { borderColor: accentColor }]}>
                  <Text style={[styles.badgeText, { color: accentColor }]}>
                    {isSudo ? 'Permisos de Superusuario (root)' : `Credencial: ${data.target}`}
                  </Text>
                </View>
              </View>
            </View>
          </View>

          {/* Prompt Description */}
          <View style={styles.promptContainer}>
            <Text style={styles.promptText}>{data.prompt}</Text>
          </View>

          {/* Optional Username field */}
          {data.showUsernameField && (
            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>Usuario:</Text>
              <View style={styles.inputWrapper}>
                <Ionicons name="person-outline" size={18} color="#71717a" style={styles.inputIcon} />
                <TextInput
                  style={styles.textInput}
                  value={username}
                  onChangeText={setUsername}
                  placeholder="root"
                  placeholderTextColor="#71717a"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </View>
            </View>
          )}

          {/* Password Input */}
          <View style={styles.inputGroup}>
            <Text style={styles.inputLabel}>
              {isSudo ? 'Contraseña de sudo / administrador:' : 'Contraseña o clave secreta:'}
            </Text>
            <View style={styles.inputWrapper}>
              <Ionicons name="lock-closed-outline" size={18} color="#71717a" style={styles.inputIcon} />
              <TextInput
                style={styles.textInput}
                value={password}
                onChangeText={setPassword}
                placeholder="••••••••••••"
                placeholderTextColor="#71717a"
                secureTextEntry={!showPassword}
                autoCapitalize="none"
                autoCorrect={false}
                autoFocus
                onSubmitEditing={handleConfirm}
              />
              <TouchableOpacity
                style={styles.eyeButton}
                onPress={() => setShowPassword(!showPassword)}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              >
                <Ionicons
                  name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                  size={19}
                  color="#a1a1aa"
                />
              </TouchableOpacity>
            </View>
          </View>

          <Text style={styles.disclaimerText}>
            🔒 La contraseña se transfiere mediante cifrado TLS seguro y nunca se guarda en el historial de chat ni en registros del agente.
          </Text>

          {/* Actions */}
          <View style={styles.actions}>
            <TouchableOpacity
              style={styles.cancelButton}
              onPress={handleDismiss}
              disabled={isSubmitting}
            >
              <Text style={styles.cancelButtonText}>Cancelar</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.confirmButton,
                { backgroundColor: accentColor },
                isSubmitting && { opacity: 0.7 },
              ]}
              onPress={handleConfirm}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <ActivityIndicator size="small" color="#ffffff" />
              ) : (
                <>
                  <Ionicons name="checkmark-sharp" size={18} color="#ffffff" style={{ marginRight: 6 }} />
                  <Text style={styles.confirmButtonText}>Autenticar</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  card: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: '#18181b',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#27272a',
    padding: 22,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.5,
    shadowRadius: 16,
    elevation: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  iconWrap: {
    width: 48,
    height: 48,
    borderRadius: 14,
    borderWidth: 1.5,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  headerText: {
    flex: 1,
  },
  title: {
    fontSize: 17,
    fontWeight: '700',
    color: '#fafafa',
    marginBottom: 4,
  },
  badgeRow: {
    flexDirection: 'row',
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 6,
    borderWidth: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
  },
  promptContainer: {
    backgroundColor: '#111113',
    borderRadius: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: '#27272a',
    marginBottom: 16,
  },
  promptText: {
    color: '#e4e4e7',
    fontSize: 13.5,
    lineHeight: 19,
  },
  inputGroup: {
    marginBottom: 14,
  },
  inputLabel: {
    fontSize: 12.5,
    fontWeight: '600',
    color: '#a1a1aa',
    marginBottom: 6,
  },
  inputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#09090b',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#27272a',
    paddingHorizontal: 12,
    height: 46,
  },
  inputIcon: {
    marginRight: 8,
  },
  textInput: {
    flex: 1,
    color: '#fafafa',
    fontSize: 15,
    paddingVertical: 0,
  },
  eyeButton: {
    padding: 4,
  },
  disclaimerText: {
    fontSize: 11,
    color: '#71717a',
    lineHeight: 15,
    marginBottom: 20,
    marginTop: 2,
  },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 10,
  },
  cancelButton: {
    paddingVertical: 11,
    paddingHorizontal: 18,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#3f3f46',
    backgroundColor: 'transparent',
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelButtonText: {
    color: '#d4d4d8',
    fontSize: 14,
    fontWeight: '600',
  },
  confirmButton: {
    flexDirection: 'row',
    paddingVertical: 11,
    paddingHorizontal: 20,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 120,
  },
  confirmButtonText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '700',
  },
});
