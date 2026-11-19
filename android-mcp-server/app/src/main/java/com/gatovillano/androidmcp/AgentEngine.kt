package com.gatovillano.androidmcp

import android.content.Context
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class AgentEngine(
    private val context: Context,
    private val memoryStore: MemoryStore
) {
    private val deviceTools = DeviceTools(context)
    private val localProtocol = McpProtocolHandler(deviceTools)
    private val remoteClient = RemoteMcpClient()
    private val llmClient = LlmClient()
    private val json = Json { ignoreUnknownKeys = true }

    private data class BoundTool(
        val name: String,
        val description: String,
        val schema: JsonObject,
        val isLocal: Boolean,
        val localName: String = "",
        val remoteServer: RemoteMcpServerConfig? = null,
        val remoteName: String = ""
    )

    suspend fun runTurn(
        history: List<ChatMessage>,
        onStatus: (String) -> Unit,
        onToolLog: (String) -> Unit
    ): String {
        val providerId = AssistantPrefs.getProviderId(context)
        val preset = ProviderCatalog.byId(providerId)
        val baseUrl = AssistantPrefs.getBaseUrl(context, providerId)
        val apiKey = AssistantPrefs.getApiKey(context, providerId)
        val model = AssistantPrefs.getModel(context, providerId)

        if (apiKey.isBlank() && providerId != "ollama" && providerId != "custom") {
            return "Configura tu API Key para ${preset.name} en la pestaña 'Modelos'."
        }

        // 1. Gather local + remote MCP tools
        val boundTools = mutableMapOf<String, BoundTool>()
        if (AssistantPrefs.isLocalAndroidToolsEnabled(context)) {
            val localDefs = localProtocol.getToolDefinitions()
            for (el in localDefs) {
                val obj = el.jsonObject
                val origName = obj["name"]?.jsonPrimitive?.contentOrNull ?: continue
                val desc = obj["description"]?.jsonPrimitive?.contentOrNull ?: origName
                val schema = obj["inputSchema"] as? JsonObject ?: buildJsonObject {
                    put("type", "object")
                    put("properties", buildJsonObject {})
                }
                val nsName = "android__$origName"
                boundTools[nsName] = BoundTool(
                    name = nsName,
                    description = "[Android Local] $desc",
                    schema = schema,
                    isLocal = true,
                    localName = origName
                )
            }
        }

        val remoteServers = AssistantPrefs.getRemoteMcps(context).filter { it.enabled && it.url.isNotBlank() }
        for (srv in remoteServers) {
            onStatus("Consultando MCP ${srv.name}...")
            try {
                val tools = remoteClient.listTools(srv)
                // Keep tool count sane for mobile/LLM limits (cap 80 per remote server)
                for (t in tools.take(80)) {
                    boundTools[t.namespacedName] = BoundTool(
                        name = t.namespacedName,
                        description = t.description,
                        schema = t.inputSchema,
                        isLocal = false,
                        remoteServer = srv,
                        remoteName = t.originalName
                    )
                }
            } catch (e: Exception) {
                onToolLog("[MCP ${srv.name}] Sin conexión: ${e.message}")
            }
        }

        val systemPrompt = buildSystemPrompt(boundTools.size, remoteServers.map { it.name })

        return if (preset.isAnthropic) {
            runAnthropicLoop(baseUrl, apiKey, model, systemPrompt, history, boundTools, onStatus, onToolLog)
        } else {
            runOpenAiLoop(baseUrl, apiKey, model, systemPrompt, history, boundTools, onStatus, onToolLog)
        }
    }

    private fun buildSystemPrompt(toolCount: Int, remoteNames: List<String>): String = buildString {
        appendLine("Eres Pequeño Asistente E-Ink, un agente nativo que opera directamente sobre Android.")
        appendLine("Responde en texto claro y conciso pensado para pantalla de tinta electrónica (sin emojis excesivos ni tablas anchas).")
        appendLine("Tienes $toolCount herramientas disponibles (locales de Android y MCP remotos: ${remoteNames.joinToString().ifEmpty { "ninguno" }}).")
        appendLine("Usa las herramientas cuando el usuario te pida acciones en el teléfono o en su PC Linux.")
        val userMd = memoryStore.readUserProfile().trim()
        if (userMd.isNotBlank()) {
            appendLine("\n--- USER.md ---\n$userMd")
        }
        val memMd = memoryStore.readMemory().trim()
        if (memMd.isNotBlank()) {
            appendLine("\n--- MEMORY.md ---\n$memMd")
        }
    }

    private suspend fun executeBoundTool(tool: BoundTool?, rawArgs: String): String {
        if (tool == null) return "Error: Herramienta no encontrada."
        val argsObj = try {
            json.parseToJsonElement(rawArgs.ifBlank { "{}" }).jsonObject
        } catch (_: Exception) {
            buildJsonObject {}
        }

        return if (tool.isLocal) {
            val rpcReq = buildJsonObject {
                put("jsonrpc", "2.0")
                put("id", 1)
                put("method", "tools/call")
                put("params", buildJsonObject {
                    put("name", tool.localName)
                    put("arguments", argsObj)
                })
            }
            val rawResp = localProtocol.handleJsonRpc(rpcReq.toString()) ?: return "OK"
            try {
                val parsed = json.parseToJsonElement(rawResp).jsonObject
                val content = parsed["result"]?.jsonObject?.get("content")?.jsonArray
                content?.joinToString("\n") { c ->
                    val co = c.jsonObject
                    when (co["type"]?.jsonPrimitive?.contentOrNull) {
                        "text" -> co["text"]?.jsonPrimitive?.contentOrNull.orEmpty()
                        "image" -> "[Foto capturada correctamente (${co["mimeType"]?.jsonPrimitive?.contentOrNull})]"
                        else -> co.toString()
                    }
                } ?: rawResp
            } catch (_: Exception) {
                rawResp
            }
        } else {
            val srv = tool.remoteServer ?: return "Error: Servidor remoto no configurado."
            try {
                remoteClient.callTool(srv, tool.remoteName, argsObj)
            } catch (e: Exception) {
                "Error llamando a ${srv.name}/${tool.remoteName}: ${e.message}"
            }
        }
    }

    private suspend fun runOpenAiLoop(
        baseUrl: String,
        apiKey: String,
        model: String,
        systemPrompt: String,
        history: List<ChatMessage>,
        boundTools: Map<String, BoundTool>,
        onStatus: (String) -> Unit,
        onToolLog: (String) -> Unit
    ): String {
        val messages = mutableListOf<JsonObject>()
        messages.add(buildJsonObject {
            put("role", "system")
            put("content", systemPrompt)
        })
        history.filter { it.role == "user" || it.role == "assistant" }.takeLast(16).forEach { msg ->
            messages.add(buildJsonObject {
                put("role", msg.role)
                put("content", msg.content)
            })
        }

        val toolsArr = buildJsonArray {
            boundTools.values.take(120).forEach { t ->
                add(llmClient.openAiToolDef(t.name, t.description, t.schema))
            }
        }

        for (step in 1..8) {
            onStatus(if (step == 1) "Pensando ($model)..." else "Analizando resultado ($step)...")
            val reply = llmClient.chatOpenAiCompatible(
                baseUrl = baseUrl,
                apiKey = apiKey,
                model = model,
                messages = JsonArray(messages),
                tools = toolsArr
            )

            if (reply.toolCalls.isEmpty()) {
                return reply.text.ifBlank { "(Sin respuesta de texto)" }
            }

            messages.add(buildJsonObject {
                put("role", "assistant")
                put("content", reply.text)
                put("tool_calls", buildJsonArray {
                    reply.toolCalls.forEach { tc ->
                        add(buildJsonObject {
                            put("id", tc.id)
                            put("type", "function")
                            put("function", buildJsonObject {
                                put("name", tc.name)
                                put("arguments", tc.argumentsJson)
                            })
                        })
                    }
                })
            })

            for (tc in reply.toolCalls) {
                onStatus("Ejecutando ${tc.name}...")
                val toolOutput = executeBoundTool(boundTools[tc.name], tc.argumentsJson)
                onToolLog("⚙ ${tc.name} → ${toolOutput.take(180)}")
                messages.add(buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", tc.id)
                    put("content", toolOutput.take(4000))
                })
            }
        }
        return "Se alcanzó el límite de pasos de herramientas para este turno."
    }

    private suspend fun runAnthropicLoop(
        baseUrl: String,
        apiKey: String,
        model: String,
        systemPrompt: String,
        history: List<ChatMessage>,
        boundTools: Map<String, BoundTool>,
        onStatus: (String) -> Unit,
        onToolLog: (String) -> Unit
    ): String {
        val messages = mutableListOf<JsonObject>()
        history.filter { it.role == "user" || it.role == "assistant" }.takeLast(16).forEach { msg ->
            messages.add(buildJsonObject {
                put("role", msg.role)
                put("content", msg.content)
            })
        }

        val toolsArr = buildJsonArray {
            boundTools.values.take(120).forEach { t ->
                add(llmClient.anthropicToolDef(t.name, t.description, t.schema))
            }
        }

        for (step in 1..8) {
            onStatus(if (step == 1) "Pensando ($model)..." else "Analizando resultado ($step)...")
            val reply = llmClient.chatAnthropic(
                baseUrl = baseUrl,
                apiKey = apiKey,
                model = model,
                systemPrompt = systemPrompt,
                messages = JsonArray(messages),
                tools = toolsArr
            )
            if (reply.toolCalls.isEmpty()) {
                return reply.text.ifBlank { "(Sin respuesta de texto)" }
            }

            messages.add(buildJsonObject {
                put("role", "assistant")
                put("content", buildJsonArray {
                    if (reply.text.isNotBlank()) {
                        add(buildJsonObject {
                            put("type", "text")
                            put("text", reply.text)
                        })
                    }
                    reply.toolCalls.forEach { tc ->
                        val inputObj = try {
                            json.parseToJsonElement(tc.argumentsJson).jsonObject
                        } catch (_: Exception) {
                            buildJsonObject {}
                        }
                        add(buildJsonObject {
                            put("type", "tool_use")
                            put("id", tc.id)
                            put("name", tc.name)
                            put("input", inputObj)
                        })
                    }
                })
            })

            val toolResults = buildJsonArray {
                for (tc in reply.toolCalls) {
                    onStatus("Ejecutando ${tc.name}...")
                    val out = executeBoundTool(boundTools[tc.name], tc.argumentsJson)
                    onToolLog("⚙ ${tc.name} → ${out.take(180)}")
                    add(buildJsonObject {
                        put("type", "tool_result")
                        put("tool_use_id", tc.id)
                        put("content", out.take(4000))
                    })
                }
            }
            messages.add(buildJsonObject {
                put("role", "user")
                put("content", toolResults)
            })
        }
        return "Se alcanzó el límite de pasos de herramientas para este turno."
    }
}
