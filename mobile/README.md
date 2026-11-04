# 📱 Pequeño Asistente - App Android (React Native / Expo)

Aplicación móvil oficial de **Pequeño Asistente** para Android. Te permite interactuar remotamente con tu asistente de escritorio Linux, ejecutar herramientas MCP (archivos, terminal, web, etc.), acceder a tu memoria persistente y dictar por voz desde cualquier lugar a través de **Cloudflare Tunnel** y **FastAPI**.

---

## ✨ Características de la App Móvil

1. **Conexión Remota Segura**:
   - Conexión vía URL pública generada por Cloudflare Tunnel (`https://xxx.trycloudflare.com`) o IP local LAN (`http://192.168.1.X:8765`).
   - Soporte opcional para autenticación con Bearer API Key.
   - Botón de diagnóstico para probar la conexión en 1 clic.

2. **Chat en Tiempo Real con Streaming (SSE)**:
   - Recepción progresiva de respuestas token por token.
   - Tarjetas de ejecución de herramientas MCP en vivo con parámetros y resultados desplegables.
   - Bloque colapsable de pensamiento del modelo (Chain of Thought / Reasoning).
   - Botón de detención (Abort) en tiempo real.

3. **Gestión de Conversaciones (Multi-Thread)**:
   - Panel lateral desplegable con todas las conversaciones persistentes guardadas en tu PC Linux.
   - Crear nuevos hilos y borrar conversaciones obsoletas desde el móvil.

4. **Dictado por Voz Integrado**:
   - Botón de micrófono en el cajón de entrada.
   - Graba notas de voz y las transcribe en local utilizando el motor `faster-whisper` de tu PC a través del endpoint `/api/audio/transcribe`.

---

## 🚀 Cómo Usar y Probar en tu Teléfono

### 1. Iniciar el Servidor y Túnel en tu PC Linux
En la terminal de tu computadora:

```bash
cd "Pequeño asistente"
./scripts/start_remote.sh
```

El script iniciará el servidor FastAPI y abrirá el túnel seguro de Cloudflare, mostrando en pantalla algo como:

```text
🎉 ¡TÚNEL ACTIVO EXITOSAMENTE!
👉 URL PÚBLICA PARA ANDROID:
   https://random-subdomain.trycloudflare.com
```

### 2. Abrir la App en tu Teléfono con Expo Go
1. Instala **Expo Go** desde Google Play Store en tu dispositivo Android.
2. En tu PC, abre una terminal y dirígete a la carpeta `mobile`:
   ```bash
   cd mobile
   npm start
   ```
3. Escanea el código QR que aparece en la terminal con la app **Expo Go** (o cámara de tu teléfono).
4. La app cargará instantáneamente.
5. En la app móvil, toca el icono de **Ajustes** (⚙️) o el aviso superior, pega tu URL pública de Cloudflare (`https://random-subdomain.trycloudflare.com`) y toca **Guardar y Probar Conexión**.

¡Listo! Ya tienes control total de tu Pequeño Asistente en tu móvil.

---

## 📦 Compilar APK Nativo Independiente

Si deseas generar un archivo `.apk` instalable sin depender de Expo Go:

### Opción A: Compilación Local con Android SDK y Gradle
Como ya tienes el Android SDK instalado en tu máquina:

```bash
cd mobile
npx expo prebuild --platform android
cd android
./gradlew assembleRelease
```
El archivo APK listo para instalar se generará en:
`android/app/build/outputs/apk/release/app-release.apk`

### Opción B: Con EAS Build en la Nube
```bash
npx eas-cli login
npx eas-cli build -p android --profile preview
```
