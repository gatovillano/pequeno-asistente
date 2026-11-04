import React, { useState, useEffect } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ConnectionConfig } from '../types';
import { testConnection, saveConfig, normalizeUrl } from '../services/api';

interface SettingsModalProps {
  visible: boolean;
  onClose: () => void;
  config: ConnectionConfig;
  onConfigUpdated: (newConfig: ConnectionConfig) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  visible,
  onClose,
  config,
  onConfigUpdated,
}) => {
  const [url, setUrl] = useState(config.url);
  const [apiKey, setApiKey] = useState(config.apiKey);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
    details?: any;
  } | null>(null);

  useEffect(() => {
    setUrl(config.url);
    setApiKey(config.apiKey);
    setTestResult(null);
  }, [visible, config]);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    const targetUrl = normalizeUrl(url);
    const res = await testConnection(targetUrl, apiKey);
    setTesting(false);

    if (res.success) {
      setUrl(targetUrl);
      setTestResult({
        success: true,
        message: '¡Conexión exitosa con Pequeño Asistente!',
        details: res.data,
      });
    } else {
      setTestResult({
        success: false,
        message: res.error || 'Error al conectar con el servidor.',
      });
    }
  };

  const handleSave = async () => {
    const targetUrl = normalizeUrl(url);
    await saveConfig(targetUrl, apiKey);
    const check = await testConnection(targetUrl, apiKey);

    onConfigUpdated({
      url: targetUrl,
      apiKey: apiKey.trim(),
      isConnected: check.success,
      activeModel: check.data?.active_model,
      connectedMcpServers: check.data?.connected_mcp_servers,
    });
    onClose();
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        style={styles.modalOverlay}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.modalContent}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerTitleRow}>
              <Ionicons name="cloud-outline" size={22} color="#38bdf8" />
              <Text style={styles.headerTitle}>Conexión Remota (Cloudflare)</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={22} color="#a1a1aa" />
            </TouchableOpacity>
          </View>

          <ScrollView style={styles.body} showsVerticalScrollIndicator={false}>
            {/* Guide Card */}
            <View style={styles.guideCard}>
              <Text style={styles.guideTitle}>🌐 Dominio Oficial Conectado</Text>
              <Text style={styles.guideText}>
                Tu túnel de Cloudflare está configurado y vinculado permanentemente a:
              </Text>
              <View style={styles.commandBox}>
                <Text style={styles.commandText}>https://api-pa.kognitoai.cloud</Text>
              </View>
              <Text style={styles.guideFootnote}>
                El servidor corre en segundo plano en tu PC mediante systemd.
              </Text>
            </View>

            {/* Input URL */}
            <View style={styles.inputGroup}>
              <Text style={styles.label}>URL del Servidor</Text>
              <TextInput
                style={styles.input}
                placeholder="https://api-pa.kognitoai.cloud"
                placeholderTextColor="#52525b"
                value={url}
                onChangeText={setUrl}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
              />
            </View>

            {/* Input API Key */}
            <View style={styles.inputGroup}>
              <View style={styles.labelRow}>
                <Text style={styles.label}>API Key (Bearer Token)</Text>
                <Text style={styles.sublabel}>Opcional si no se definió clave</Text>
              </View>
              <View style={styles.passwordContainer}>
                <TextInput
                  style={[styles.input, { flex: 1, borderWidth: 0 }]}
                  placeholder="Tu clave secreta..."
                  placeholderTextColor="#52525b"
                  value={apiKey}
                  onChangeText={setApiKey}
                  secureTextEntry={!showKey}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <TouchableOpacity
                  onPress={() => setShowKey(!showKey)}
                  style={styles.eyeBtn}
                >
                  <Ionicons
                    name={showKey ? 'eye-off-outline' : 'eye-outline'}
                    size={20}
                    color="#a1a1aa"
                  />
                </TouchableOpacity>
              </View>
            </View>

            {/* Feedback / Test Result */}
            {testResult && (
              <View
                style={[
                  styles.resultCard,
                  testResult.success ? styles.resultSuccess : styles.resultError,
                ]}
              >
                <View style={styles.resultTitleRow}>
                  <Ionicons
                    name={
                      testResult.success
                        ? 'checkmark-circle-outline'
                        : 'alert-circle-outline'
                    }
                    size={20}
                    color={testResult.success ? '#10b981' : '#ef4444'}
                  />
                  <Text
                    style={[
                      styles.resultTitle,
                      { color: testResult.success ? '#10b981' : '#ef4444' },
                    ]}
                  >
                    {testResult.message}
                  </Text>
                </View>

                {testResult.success && testResult.details && (
                  <View style={styles.detailsContainer}>
                    <Text style={styles.detailItem}>
                      🤖 Modelo: {testResult.details.active_model || 'Predeterminado'}
                    </Text>
                    <Text style={styles.detailItem}>
                      🧩 Servidores MCP:{' '}
                      {testResult.details.connected_mcp_servers?.join(', ') || 'Ninguno'}
                    </Text>
                  </View>
                )}
              </View>
            )}

            {/* Action Buttons */}
            <View style={styles.actionsRow}>
              <TouchableOpacity
                style={[styles.btn, styles.testBtn]}
                onPress={handleTest}
                disabled={testing || !url.trim()}
              >
                {testing ? (
                  <ActivityIndicator size="small" color="#38bdf8" />
                ) : (
                  <>
                    <Ionicons name="flask-outline" size={18} color="#38bdf8" />
                    <Text style={styles.testBtnText}>Probar Conexión</Text>
                  </>
                )}
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.btn, styles.saveBtn]}
                onPress={handleSave}
                disabled={!url.trim()}
              >
                <Ionicons name="checkmark" size={18} color="#ffffff" />
                <Text style={styles.saveBtnText}>Guardar</Text>
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#18181b',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderWidth: 1,
    borderColor: '#27272a',
    maxHeight: '85%',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
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
    fontSize: 17,
    fontWeight: '700',
  },
  closeBtn: {
    padding: 4,
  },
  body: {
    padding: 16,
  },
  guideCard: {
    backgroundColor: '#202024',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: '#2e2e34',
    marginBottom: 16,
  },
  guideTitle: {
    color: '#f4f4f5',
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 4,
  },
  guideText: {
    color: '#a1a1aa',
    fontSize: 12,
    lineHeight: 17,
  },
  commandBox: {
    backgroundColor: '#101012',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginVertical: 6,
  },
  commandText: {
    color: '#34d399',
    fontSize: 12,
    fontFamily: 'monospace',
  },
  guideFootnote: {
    color: '#71717a',
    fontSize: 11,
  },
  inputGroup: {
    marginBottom: 14,
  },
  labelRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  label: {
    color: '#e4e4e7',
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 6,
  },
  sublabel: {
    color: '#71717a',
    fontSize: 11,
    marginBottom: 6,
  },
  input: {
    backgroundColor: '#202024',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#2e2e34',
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: '#fafafa',
    fontSize: 14,
  },
  passwordContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#202024',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#2e2e34',
  },
  eyeBtn: {
    padding: 10,
  },
  resultCard: {
    borderRadius: 10,
    padding: 12,
    marginBottom: 16,
    borderWidth: 1,
  },
  resultSuccess: {
    backgroundColor: '#064e3b22',
    borderColor: '#059669',
  },
  resultError: {
    backgroundColor: '#7f1d1d22',
    borderColor: '#dc2626',
  },
  resultTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  resultTitle: {
    fontSize: 13,
    fontWeight: '600',
    flex: 1,
  },
  detailsContainer: {
    marginTop: 8,
    gap: 4,
  },
  detailItem: {
    color: '#a1a1aa',
    fontSize: 12,
  },
  actionsRow: {
    flexDirection: 'row',
    gap: 10,
    marginVertical: 12,
    paddingBottom: 20,
  },
  btn: {
    flex: 1,
    height: 44,
    borderRadius: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  testBtn: {
    backgroundColor: '#1e293b',
    borderWidth: 1,
    borderColor: '#38bdf844',
  },
  testBtnText: {
    color: '#38bdf8',
    fontWeight: '600',
    fontSize: 14,
  },
  saveBtn: {
    backgroundColor: '#2563eb',
  },
  saveBtnText: {
    color: '#ffffff',
    fontWeight: '600',
    fontSize: 14,
  },
});
