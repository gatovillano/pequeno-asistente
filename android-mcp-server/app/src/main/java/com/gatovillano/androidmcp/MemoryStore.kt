package com.gatovillano.androidmcp

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

@Serializable
data class ChatMessage(
    val role: String, // "user", "assistant", "tool_log"
    val content: String,
    val timestampMs: Long = System.currentTimeMillis()
)

class MemoryStore(context: Context) {
    private val memoryDir = File(context.filesDir, "memory").apply { mkdirs() }
    private val userFile = File(memoryDir, "USER.md")
    private val memoryFile = File(memoryDir, "MEMORY.md")
    private val historyFile = File(context.filesDir, "chat_history.json")
    private val json = Json { ignoreUnknownKeys = true; prettyPrint = true }

    init {
        if (!userFile.exists()) {
            userFile.writeText(
                "# Perfil de Usuario\n" +
                    "- Dispositivo: Android E-Ink (Nabuk Carta UHD)\n" +
                    "- Idioma preferido: Español\n" +
                    "- Estilo: Respuestas claras, concisas y fáciles de leer en tinta electrónica.\n"
            )
        }
        if (!memoryFile.exists()) {
            memoryFile.writeText("# Memoria Persistente\n- Aquí se guardan hechos y preferencias importantes.\n")
        }
    }

    fun readUserProfile(): String = try { userFile.readText() } catch (_: Exception) { "" }
    fun writeUserProfile(text: String) { userFile.writeText(text) }

    fun readMemory(): String = try { memoryFile.readText() } catch (_: Exception) { "" }
    fun writeMemory(text: String) { memoryFile.writeText(text) }

    fun loadHistory(): MutableList<ChatMessage> {
        if (!historyFile.exists()) return mutableListOf()
        return try {
            json.decodeFromString<List<ChatMessage>>(historyFile.readText()).toMutableList()
        } catch (_: Exception) {
            mutableListOf()
        }
    }

    fun saveHistory(messages: List<ChatMessage>) {
        try {
            historyFile.writeText(json.encodeToString(messages.takeLast(60)))
        } catch (_: Exception) {
        }
    }

    fun clearHistory() {
        if (historyFile.exists()) historyFile.delete()
    }
}
