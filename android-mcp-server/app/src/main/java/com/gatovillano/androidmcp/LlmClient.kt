package com.gatovillano.androidmcp

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class LlmToolCall(
    val id: String,
    val name: String,
    val argumentsJson: String
)

data class LlmReply(
    val text: String,
    val toolCalls: List<LlmToolCall> = emptyList()
)

class LlmClient {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun fetchAvailableModels(
        provider: ProviderPreset,
        baseUrl: String,
        apiKey: String
    ): List<String> = withContext(Dispatchers.IO) {
        if (provider.isAnthropic) return@withContext provider.suggestedModels
        val endpoint = "${baseUrl.trimEnd('/')}/models"
        val conn = (URL(endpoint).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 8000
            readTimeout = 15000
            if (apiKey.isNotBlank()) {
                setRequestProperty("Authorization", "Bearer $apiKey")
            }
        }
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader(Charsets.UTF_8)?.readText().orEmpty()
        conn.disconnect()
        if (code !in 200..299) {
            throw IllegalStateException("HTTP $code: ${body.take(250)}")
        }
        val root = json.parseToJsonElement(body).jsonObject
        val data = root["data"]?.jsonArray ?: return@withContext provider.suggestedModels
        data.mapNotNull { it.jsonObject["id"]?.jsonPrimitive?.contentOrNull }
            .distinct()
            .sorted()
            .take(150)
    }

    suspend fun chatOpenAiCompatible(
        baseUrl: String,
        apiKey: String,
        model: String,
        messages: JsonArray,
        tools: JsonArray
    ): LlmReply = withContext(Dispatchers.IO) {
        val payload = buildJsonObject {
            put("model", model)
            put("messages", messages)
            if (tools.isNotEmpty()) {
                put("tools", tools)
                put("tool_choice", "auto")
            }
        }
        val endpoint = "${baseUrl.trimEnd('/')}/chat/completions"
        val conn = (URL(endpoint).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 12000
            readTimeout = 60000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            if (apiKey.isNotBlank()) {
                setRequestProperty("Authorization", "Bearer $apiKey")
            }
            if (baseUrl.contains("openrouter.ai")) {
                setRequestProperty("HTTP-Referer", "https://pequeno-asistente.local")
                setRequestProperty("X-Title", "Pequeno Asistente E-Ink")
            }
        }
        OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use {
            it.write(payload.toString())
        }
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader(Charsets.UTF_8)?.readText().orEmpty()
        conn.disconnect()

        if (code !in 200..299) {
            throw IllegalStateException("Error LLM (HTTP $code): ${body.take(400)}")
        }

        val root = json.parseToJsonElement(body).jsonObject
        val msg = root["choices"]?.jsonArray?.firstOrNull()?.jsonObject?.get("message")?.jsonObject
            ?: throw IllegalStateException("Respuesta inesperada del LLM.")

        val contentText = msg["content"]?.jsonPrimitive?.contentOrNull.orEmpty()
        val calls = msg["tool_calls"]?.jsonArray?.mapNotNull { tc ->
            val obj = tc.jsonObject
            val id = obj["id"]?.jsonPrimitive?.contentOrNull ?: "call_${System.nanoTime()}"
            val fn = obj["function"]?.jsonObject ?: return@mapNotNull null
            val name = fn["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            val args = fn["arguments"]?.jsonPrimitive?.contentOrNull ?: "{}"
            LlmToolCall(id = id, name = name, argumentsJson = args)
        } ?: emptyList()

        LlmReply(text = contentText, toolCalls = calls)
    }

    suspend fun chatAnthropic(
        baseUrl: String,
        apiKey: String,
        model: String,
        systemPrompt: String,
        messages: JsonArray,
        tools: JsonArray
    ): LlmReply = withContext(Dispatchers.IO) {
        val payload = buildJsonObject {
            put("model", model)
            put("max_tokens", 2048)
            put("system", systemPrompt)
            put("messages", messages)
            if (tools.isNotEmpty()) {
                put("tools", tools)
            }
        }
        val endpoint = "${baseUrl.trimEnd('/')}/messages"
        val conn = (URL(endpoint).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 12000
            readTimeout = 60000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("x-api-key", apiKey)
            setRequestProperty("anthropic-version", "2023-06-01")
        }
        OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use {
            it.write(payload.toString())
        }
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader(Charsets.UTF_8)?.readText().orEmpty()
        conn.disconnect()

        if (code !in 200..299) {
            throw IllegalStateException("Error Anthropic (HTTP $code): ${body.take(400)}")
        }

        val root = json.parseToJsonElement(body).jsonObject
        val contentArr = root["content"]?.jsonArray ?: buildJsonArray {}
        val texts = mutableListOf<String>()
        val calls = mutableListOf<LlmToolCall>()
        for (block in contentArr) {
            val obj = block.jsonObject
            when (obj["type"]?.jsonPrimitive?.contentOrNull) {
                "text" -> texts.add(obj["text"]?.jsonPrimitive?.contentOrNull.orEmpty())
                "tool_use" -> {
                    val id = obj["id"]?.jsonPrimitive?.contentOrNull ?: "call_${System.nanoTime()}"
                    val name = obj["name"]?.jsonPrimitive?.contentOrNull ?: continue
                    val input = obj["input"]?.toString() ?: "{}"
                    calls.add(LlmToolCall(id = id, name = name, argumentsJson = input))
                }
            }
        }
        LlmReply(text = texts.joinToString("\n"), toolCalls = calls)
    }

    fun openAiToolDef(name: String, description: String, schema: JsonObject): JsonObject = buildJsonObject {
        put("type", "function")
        put("function", buildJsonObject {
            put("name", name)
            put("description", description)
            put("parameters", schema)
        })
    }

    fun anthropicToolDef(name: String, description: String, schema: JsonObject): JsonObject = buildJsonObject {
        put("name", name)
        put("description", description)
        put("input_schema", schema)
    }
}
