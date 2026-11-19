package com.gatovillano.androidmcp

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private val uiScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var memoryStore: MemoryStore
    private lateinit var agentEngine: AgentEngine
    private val llmClient = LlmClient()
    private val remoteMcpClient = RemoteMcpClient()

    private val chatHistory = mutableListOf<ChatMessage>()
    private var activeTab = 0 // 0=CHAT, 1=MCP, 2=MEMORIA, 3=MODELOS
    private var isSending = false

    private lateinit var tabButtons: List<Button>
    private lateinit var contentContainer: FrameLayout

    // Chat views
    private lateinit var chatScroll: ScrollView
    private lateinit var chatListLayout: LinearLayout
    private lateinit var chatStatusText: TextView
    private lateinit var chatInput: EditText
    private lateinit var sendBtn: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        memoryStore = MemoryStore(this)
        agentEngine = AgentEngine(this, memoryStore)
        chatHistory.clear()
        chatHistory.addAll(memoryStore.loadHistory())

        window.decorView.setBackgroundColor(Color.WHITE)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }

        // Top E-Ink Header
        val header = TextView(this).apply {
            text = "PEQUEÑO ASISTENTE E-INK"
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), dp(8))
        }
        root.addView(header)
        root.addView(divider(2))

        // Tab Bar
        val tabBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.WHITE)
            setPadding(dp(8), dp(6), dp(8), dp(6))
        }
        val labels = listOf("CHAT", "MCP", "MEMORIA", "MODELOS")
        tabButtons = labels.mapIndexed { idx, label ->
            einkButton(label, filled = idx == activeTab) {
                activeTab = idx
                updateTabStyles()
                renderActiveTab()
            }.apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    setMargins(dp(3), 0, dp(3), 0)
                }
            }
        }
        tabButtons.forEach { tabBar.addView(it) }
        root.addView(tabBar)
        root.addView(divider(2))

        // Main Tab Container
        contentContainer = FrameLayout(this).apply {
            setBackgroundColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f
            )
        }
        root.addView(contentContainer)

        setContentView(root)
        renderActiveTab()
    }

    override fun onResume() {
        super.onResume()
        if (activeTab == 1) renderActiveTab()
    }

    override fun onDestroy() {
        uiScope.cancel()
        super.onDestroy()
    }

    private fun updateTabStyles() {
        tabButtons.forEachIndexed { idx, btn ->
            styleButton(btn, filled = idx == activeTab)
        }
    }

    private fun renderActiveTab() {
        contentContainer.removeAllViews()
        val view = when (activeTab) {
            0 -> buildChatView()
            1 -> buildMcpView()
            2 -> buildMemoryView()
            else -> buildModelsView()
        }
        contentContainer.addView(view)
    }

    // ==================== TAB 0: CHAT ====================
    private fun buildChatView(): View {
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
        }

        val providerId = AssistantPrefs.getProviderId(this)
        val model = AssistantPrefs.getModel(this, providerId)

        val topBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(10), dp(6), dp(10), dp(6))
        }
        chatStatusText = TextView(this).apply {
            text = "Modelo: $model"
            textSize = 13f
            setTextColor(Color.BLACK)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val newThreadBtn = einkButton("NUEVO HILO", filled = false) {
            chatHistory.clear()
            memoryStore.clearHistory()
            refreshChatMessages()
        }
        topBar.addView(chatStatusText)
        topBar.addView(newThreadBtn)
        layout.addView(topBar)
        layout.addView(divider(1))

        chatListLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        chatScroll = ScrollView(this).apply {
            setBackgroundColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
            addView(chatListLayout)
        }
        layout.addView(chatScroll)
        layout.addView(divider(2))

        val inputRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(8), dp(8), dp(8))
            setBackgroundColor(Color.WHITE)
        }
        chatInput = einkEditText("Escribe tu orden para Android o Linux...").apply {
            maxLines = 4
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                setMargins(0, 0, dp(8), 0)
            }
        }
        sendBtn = einkButton("ENVIAR", filled = true) {
            sendChatMessage()
        }
        inputRow.addView(chatInput)
        inputRow.addView(sendBtn)
        layout.addView(inputRow)

        refreshChatMessages()
        return layout
    }

    private fun refreshChatMessages() {
        if (!::chatListLayout.isInitialized) return
        chatListLayout.removeAllViews()

        if (chatHistory.isEmpty()) {
            val empty = TextView(this).apply {
                text = "Listo para operar sobre Android y tus servidores MCP.\n\n" +
                    "Ejemplos:\n" +
                    "• \"¿Qué batería tengo y qué apps están instaladas?\"\n" +
                    "• \"Abre Book Reader\"\n" +
                    "• \"Revisa las ventanas abiertas en mi Linux\""
                textSize = 15f
                setTextColor(Color.BLACK)
                setPadding(dp(12), dp(16), dp(12), dp(16))
                background = einkBox(borderWidth = 1)
            }
            chatListLayout.addView(empty)
            return
        }

        for (msg in chatHistory) {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = einkBox(borderWidth = if (msg.role == "user") 2 else 1)
                setPadding(dp(10), dp(8), dp(10), dp(8))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { setMargins(0, 0, 0, dp(8)) }
            }
            val roleLabel = when (msg.role) {
                "user" -> "TÚ"
                "tool_log" -> "HERRAMIENTA MCP"
                else -> "PEQUEÑO ASISTENTE"
            }
            val headerTv = TextView(this).apply {
                text = roleLabel
                textSize = 12f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(Color.BLACK)
            }
            val bodyTv = TextView(this).apply {
                text = msg.content
                textSize = if (msg.role == "tool_log") 13f else 15f
                setTextColor(Color.BLACK)
                setTextIsSelectable(true)
                setPadding(0, dp(4), 0, 0)
            }
            card.addView(headerTv)
            card.addView(bodyTv)
            chatListLayout.addView(card)
        }
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun sendChatMessage() {
        if (isSending) return
        val prompt = chatInput.text.toString().trim()
        if (prompt.isEmpty()) return

        chatInput.setText("")
        chatHistory.add(ChatMessage(role = "user", content = prompt))
        memoryStore.saveHistory(chatHistory)
        refreshChatMessages()

        isSending = true
        sendBtn.isEnabled = false
        chatStatusText.text = "Pensando..."

        uiScope.launch {
            try {
                val reply = agentEngine.runTurn(
                    history = chatHistory.toList(),
                    onStatus = { status ->
                        runOnUiThread { chatStatusText.text = status }
                    },
                    onToolLog = { logLine ->
                        runOnUiThread {
                            chatHistory.add(ChatMessage(role = "tool_log", content = logLine))
                            refreshChatMessages()
                        }
                    }
                )
                chatHistory.add(ChatMessage(role = "assistant", content = reply))
                memoryStore.saveHistory(chatHistory)
                refreshChatMessages()
            } catch (e: Exception) {
                chatHistory.add(ChatMessage(role = "assistant", content = "Error: ${e.message}"))
                refreshChatMessages()
            } finally {
                isSending = false
                sendBtn.isEnabled = true
                val p = AssistantPrefs.getProviderId(this@MainActivity)
                chatStatusText.text = "Modelo: ${AssistantPrefs.getModel(this@MainActivity, p)}"
            }
        }
    }

    // ==================== TAB 1: MCP (LOCAL + REMOTOS) ====================
    private fun buildMcpView(): View {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(20))
            setBackgroundColor(Color.WHITE)
        }

        // Section 1: Local Android Tools & Embedded Server
        content.addView(sectionTitle("1. SERVIDOR MCP LOCAL (CONTROL DE ANDROID)"))

        val localCheck = CheckBox(this).apply {
            text = "Usar las 14 herramientas nativas de Android en el Chat"
            textSize = 14f
            setTextColor(Color.BLACK)
            isChecked = AssistantPrefs.isLocalAndroidToolsEnabled(this@MainActivity)
            setOnCheckedChangeListener { _, checked ->
                AssistantPrefs.setLocalAndroidToolsEnabled(this@MainActivity, checked)
            }
        }
        content.addView(localCheck)

        val running = McpServerService.isRunning
        val port = McpConfig.getPort(this)
        val token = McpConfig.getOrCreateToken(this)
        val acc = if (AndroidAccessibilityBridge.isConnected()) "Activo" else "Inactivo"
        val notif = if (NotificationBridgeService.isConnected()) "Activo" else "Inactivo"
        val cam = if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) "OK" else "Falta"
        val loc = if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) "OK" else "Falta"
        val ips = McpConfig.getDeviceIpv4Addresses()

        val statusBox = TextView(this).apply {
            text = buildString {
                appendLine("Servidor HTTP MCP (:8765): ${if (running) "ACTIVO" else "DETENIDO"}")
                appendLine("Accesibilidad (UI): $acc  |  Notificaciones: $notif")
                appendLine("Cámara: $cam  |  Ubicación: $loc")
                appendLine("Endpoints:")
                appendLine("  http://127.0.0.1:$port/mcp")
                ips.forEach { (iface, ip) -> appendLine("  [$iface] http://$ip:$port/mcp") }
                append("Bearer Token: $token")
            }
            textSize = 13f
            setTextColor(Color.BLACK)
            setTextIsSelectable(true)
            background = einkBox(1)
            setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        content.addView(statusBox)

        val srvBtnRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, dp(6))
        }
        srvBtnRow.addView(
            einkButton(if (running) "DETENER SERVIDOR" else "INICIAR SERVIDOR", filled = !running) {
                if (McpServerService.isRunning) {
                    stopService(Intent(this, McpServerService::class.java))
                } else {
                    ContextCompat.startForegroundService(this, Intent(this, McpServerService::class.java))
                }
                content.postDelayed({ renderActiveTab() }, 350)
            }.apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(0, 0, dp(4), 0) } }
        )
        srvBtnRow.addView(
            einkButton("COPIAR TOKEN", filled = false) {
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("MCP Token", token))
                Toast.makeText(this, "Token copiado", Toast.LENGTH_SHORT).show()
            }.apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(dp(4), 0, 0, 0) } }
        )
        content.addView(srvBtnRow)

        val permRow1 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(2), 0, dp(4)) }
        permRow1.addView(einkButton("PERMISOS CÁMARA/GPS", false) { requestRuntimePermissions() }
            .apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(0, 0, dp(4), 0) } })
        permRow1.addView(einkButton("ACCESIBILIDAD (UI)", false) { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
            .apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(dp(4), 0, 0, 0) } })
        content.addView(permRow1)

        val permRow2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(2), 0, dp(10)) }
        permRow2.addView(einkButton("NOTIFICACIONES", false) { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
            .apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(0, 0, dp(4), 0) } })
        permRow2.addView(einkButton("SIN RESTRICCIÓN BATERÍA", false) { requestBatteryExemption() }
            .apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(dp(4), 0, 0, 0) } })
        content.addView(permRow2)

        content.addView(divider(2))

        // Section 2: Remote MCP Servers (e.g. Linux MCP Gateway)
        content.addView(sectionTitle("2. SERVIDORES MCP REMOTOS (CONTROLAR LINUX)"))

        val remotes = AssistantPrefs.getRemoteMcps(this).toMutableList()
        if (remotes.isEmpty()) {
            content.addView(TextView(this).apply {
                text = "Aún no tienes servidores MCP remotos. Agrega abajo tu Gateway MCP de Linux."
                textSize = 13f
                setTextColor(Color.BLACK)
                setPadding(0, dp(4), 0, dp(8))
            })
        } else {
            remotes.forEachIndexed { idx, srv ->
                val card = LinearLayout(this).apply {
                    orientation = LinearLayout.VERTICAL
                    background = einkBox(1)
                    setPadding(dp(10), dp(8), dp(10), dp(8))
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    ).apply { setMargins(0, 0, 0, dp(8)) }
                }
                val cb = CheckBox(this).apply {
                    text = "${srv.name} — ${srv.url}"
                    textSize = 14f
                    setTextColor(Color.BLACK)
                    isChecked = srv.enabled
                    setOnCheckedChangeListener { _, checked ->
                        remotes[idx] = srv.copy(enabled = checked)
                        AssistantPrefs.saveRemoteMcps(this@MainActivity, remotes)
                    }
                }
                val actionRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, dp(4), 0, 0) }
                val testBtn = einkButton("PROBAR CONEXIÓN", false) {
                    Toast.makeText(this, "Probando ${srv.name}...", Toast.LENGTH_SHORT).show()
                    uiScope.launch {
                        try {
                            val tools = remoteMcpClient.listTools(srv)
                            Toast.makeText(this@MainActivity, "OK: ${tools.size} herramientas en ${srv.name}", Toast.LENGTH_LONG).show()
                        } catch (e: Exception) {
                            Toast.makeText(this@MainActivity, "Fallo: ${e.message}", Toast.LENGTH_LONG).show()
                        }
                    }
                }
                val delBtn = einkButton("ELIMINAR", false) {
                    remotes.removeAt(idx)
                    AssistantPrefs.saveRemoteMcps(this, remotes)
                    renderActiveTab()
                }
                actionRow.addView(testBtn.apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(0, 0, dp(4), 0) } })
                actionRow.addView(delBtn.apply { layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { setMargins(dp(4), 0, 0, 0) } })
                card.addView(cb)
                card.addView(actionRow)
                content.addView(card)
            }
        }

        // Quick Add Form
        val nameInput = einkEditText("Nombre (ej: linux-gateway)")
        val urlInput = einkEditText("URL (ej: http://192.168.1.150:8766/mcp)")
        val tokenInput = einkEditText("Bearer Token (opcional)")

        val presetLinuxBtn = einkButton("AUTORELLENAR GATEWAY LINUX", false) {
            nameInput.setText("linux")
            urlInput.setText("http://192.168.1.150:8766/mcp")
            tokenInput.setText("pa-linux-mcp-8766-secure")
        }
        val addBtn = einkButton("AÑADIR SERVIDOR MCP REMOTO", true) {
            val n = nameInput.text.toString().trim().ifEmpty { "mcp_${remotes.size + 1}" }
            val u = urlInput.text.toString().trim()
            val t = tokenInput.text.toString().trim()
            if (!u.startsWith("http://") && !u.startsWith("https://")) {
                Toast.makeText(this, "Ingresa una URL http:// o https:// válida", Toast.LENGTH_SHORT).show()
                return@einkButton
            }
            val id = n.lowercase().replace(Regex("[^a-z0-9_]"), "_")
            remotes.add(RemoteMcpServerConfig(id = id, name = n, url = u, bearerToken = t, enabled = true))
            AssistantPrefs.saveRemoteMcps(this, remotes)
            renderActiveTab()
        }

        content.addView(nameInput.apply { setMarginsBottom(6) })
        content.addView(urlInput.apply { setMarginsBottom(6) })
        content.addView(tokenInput.apply { setMarginsBottom(8) })
        content.addView(presetLinuxBtn.apply { setMarginsBottom(6) })
        content.addView(addBtn)

        return ScrollView(this).apply {
            setBackgroundColor(Color.WHITE)
            addView(content)
        }
    }

    // ==================== TAB 2: MEMORIA ====================
    private fun buildMemoryView(): View {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(20))
            setBackgroundColor(Color.WHITE)
        }

        content.addView(sectionTitle("USER.md (PERFIL DE USUARIO)"))
        val userEdit = einkEditText("Perfil de usuario...").apply {
            setText(memoryStore.readUserProfile())
            minLines = 5
            gravity = Gravity.TOP or Gravity.START
            setMarginsBottom(12)
        }
        content.addView(userEdit)

        content.addView(sectionTitle("MEMORY.md (MEMORIA PERSISTENTE)"))
        val memEdit = einkEditText("Hechos recordados...").apply {
            setText(memoryStore.readMemory())
            minLines = 7
            gravity = Gravity.TOP or Gravity.START
            setMarginsBottom(12)
        }
        content.addView(memEdit)

        val saveBtn = einkButton("GUARDAR MEMORIA", true) {
            memoryStore.writeUserProfile(userEdit.text.toString())
            memoryStore.writeMemory(memEdit.text.toString())
            Toast.makeText(this, "Memoria guardada en el dispositivo", Toast.LENGTH_SHORT).show()
        }
        content.addView(saveBtn)

        return ScrollView(this).apply {
            setBackgroundColor(Color.WHITE)
            addView(content)
        }
    }

    // ==================== TAB 3: MODELOS / PROVEEDORES ====================
    private fun buildModelsView(): View {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(20))
            setBackgroundColor(Color.WHITE)
        }

        content.addView(sectionTitle("PROVEEDORES LLM (IGUAL QUE EN LINUX)"))

        val presets = ProviderCatalog.presets
        var currentId = AssistantPrefs.getProviderId(this)
        val initialIdx = presets.indexOfFirst { it.id == currentId }.coerceAtLeast(0)

        val spinner = Spinner(this).apply {
            background = einkBox(2)
            setPadding(dp(8), dp(8), dp(8), dp(8))
            adapter = ArrayAdapter(
                this@MainActivity,
                android.R.layout.simple_spinner_dropdown_item,
                presets.map { it.name }
            )
            setSelection(initialIdx)
        }
        content.addView(spinner.apply { setMarginsBottom(10) })

        content.addView(fieldLabel("API Key:"))
        val apiKeyInput = einkEditText("Pega tu API Key...").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            setMarginsBottom(8)
        }
        content.addView(apiKeyInput)

        content.addView(fieldLabel("URL Base:"))
        val baseUrlInput = einkEditText("https://...").apply { setMarginsBottom(8) }
        content.addView(baseUrlInput)

        content.addView(fieldLabel("Modelo activo:"))
        val modelInput = einkEditText("Nombre del modelo").apply { setMarginsBottom(10) }
        content.addView(modelInput)

        fun loadProviderFields(id: String) {
            currentId = id
            apiKeyInput.setText(AssistantPrefs.getApiKey(this, id))
            baseUrlInput.setText(AssistantPrefs.getBaseUrl(this, id))
            modelInput.setText(AssistantPrefs.getModel(this, id))
        }
        loadProviderFields(presets[initialIdx].id)

        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                loadProviderFields(presets[position].id)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        val saveBtn = einkButton("GUARDAR CONFIGURACIÓN", true) {
            AssistantPrefs.setProviderId(this, currentId)
            AssistantPrefs.setApiKey(this, currentId, apiKeyInput.text.toString())
            AssistantPrefs.setBaseUrl(this, currentId, baseUrlInput.text.toString())
            AssistantPrefs.setModel(this, currentId, modelInput.text.toString())
            Toast.makeText(this, "Guardado: ${ProviderCatalog.byId(currentId).name} / ${modelInput.text}", Toast.LENGTH_SHORT).show()
        }
        content.addView(saveBtn.apply { setMarginsBottom(8) })

        val suggestedBtn = einkButton("ELEGIR MODELO SUGERIDO", false) {
            val models = ProviderCatalog.byId(currentId).suggestedModels.toTypedArray()
            AlertDialog.Builder(this)
                .setTitle("Modelos sugeridos")
                .setItems(models) { _, which -> modelInput.setText(models[which]) }
                .show()
        }
        content.addView(suggestedBtn.apply { setMarginsBottom(8) })

        val fetchBtn = einkButton("CARGAR MODELOS DESDE API", false) {
            val preset = ProviderCatalog.byId(currentId)
            val url = baseUrlInput.text.toString().trim()
            val key = apiKeyInput.text.toString().trim()
            Toast.makeText(this, "Consultando catálogo de ${preset.name}...", Toast.LENGTH_SHORT).show()
            uiScope.launch {
                try {
                    val models = llmClient.fetchAvailableModels(preset, url, key)
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle("${preset.name} (${models.size} modelos)")
                        .setItems(models.toTypedArray()) { _, which ->
                            modelInput.setText(models[which])
                        }
                        .show()
                } catch (e: Exception) {
                    Toast.makeText(this@MainActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
        content.addView(fetchBtn)

        return ScrollView(this).apply {
            setBackgroundColor(Color.WHITE)
            addView(content)
        }
    }

    // ==================== E-INK UI HELPERS ====================
    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun divider(heightDp: Int): View = View(this).apply {
        setBackgroundColor(Color.BLACK)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(heightDp))
    }

    private fun einkBox(borderWidth: Int = 2, bgColor: Int = Color.WHITE): GradientDrawable =
        GradientDrawable().apply {
            setColor(bgColor)
            setStroke(dp(borderWidth), Color.BLACK)
            cornerRadius = 0f
        }

    private fun styleButton(btn: Button, filled: Boolean) {
        btn.background = einkBox(borderWidth = 2, bgColor = if (filled) Color.BLACK else Color.WHITE)
        btn.setTextColor(if (filled) Color.WHITE else Color.BLACK)
        btn.typeface = Typeface.DEFAULT_BOLD
        btn.stateListAnimator = null
        btn.elevation = 0f
    }

    private fun einkButton(label: String, filled: Boolean, onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            textSize = 13f
            isAllCaps = true
            setPadding(dp(10), dp(8), dp(10), dp(8))
            styleButton(this, filled)
            setOnClickListener { onClick() }
        }

    private fun einkEditText(hintText: String): EditText = EditText(this).apply {
        hint = hintText
        setHintTextColor(Color.DKGRAY)
        setTextColor(Color.BLACK)
        textSize = 14f
        background = einkBox(borderWidth = 1, bgColor = Color.WHITE)
        setPadding(dp(10), dp(8), dp(10), dp(8))
    }

    private fun sectionTitle(title: String): TextView = TextView(this).apply {
        text = title
        textSize = 15f
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(Color.BLACK)
        setPadding(0, dp(6), 0, dp(6))
    }

    private fun fieldLabel(label: String): TextView = TextView(this).apply {
        text = label
        textSize = 13f
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(Color.BLACK)
        setPadding(0, dp(2), 0, dp(2))
    }

    private fun View.setMarginsBottom(bottomDp: Int) {
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { setMargins(0, 0, 0, dp(bottomDp)) }
    }

    private fun requestRuntimePermissions() {
        val perms = mutableListOf(
            Manifest.permission.CAMERA,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        ActivityCompat.requestPermissions(this, perms.toTypedArray(), 101)
    }

    private fun requestBatteryExemption() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:$packageName")
            }
            startActivity(intent)
        } else {
            Toast.makeText(this, "Ya está excluida de optimización de batería", Toast.LENGTH_SHORT).show()
        }
    }
}
