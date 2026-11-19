package com.gatovillano.androidmcp

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class RemoteMcpTool(
    val serverId: String,
    val serverName: String,
    val originalName: String,
    val namespacedName: String,
    val description: String,
    val inputSchema: JsonObject
)

class RemoteMcpClient {
    private val json = Json { ignoreUnknownKeys = true }

    private fun postJsonRpc(server: RemoteMcpServerConfig, payload: JsonObject): JsonObject {
        val conn = (URL(server.url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 8000
            readTimeout = 25000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json, text/event-stream")
            if (server.bearerToken.isNotBlank()) {
                setRequestProperty("Authorization", "Bearer ${server.bearerToken.trim()}")
            }
        }
        OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use {
            it.write(payload.toString())
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val body = stream?.bufferedReader(Charsets.UTF_8)?.readText().orEmpty()
        conn.disconnect()

        if (code !in 200..299) {
            throw IllegalStateException("HTTP $code: ${body.take(300)}")
        }
        val cleaned = extractJsonFromPossibleSse(body)
        return json.parseToJsonElement(cleaned).jsonObject
    }

    private fun extractJsonFromPossibleSse(body: String): String {
        val trimmed = body.trim()
        if (trimmed.startsWith("{")) return trimmed
        val dataLine = trimmed.lines().lastOrNull { it.startsWith("data:") }
        return dataLine?.removePrefix("data:")?.trim() ?: trimmed
    }

    suspend fun listTools(server: RemoteMcpServerConfig): List<RemoteMcpTool> = withContext(Dispatchers.IO) {
        val req = buildJsonObject {
            put("jsonrpc", "2.0")
            put("id", 1)
            put("method", "tools/list")
            put("params", buildJsonObject {})
        }
        val resp = postJsonRpc(server, req)
        val result = resp["result"]?.jsonObject ?: return@withContext emptyList()
        val toolsArr: JsonArray = result["tools"]?.jsonArray ?: return@withContext emptyList()
        val safePrefix = server.id.replace(Regex("[^a-zA-Z0-9_]"), "_")

        toolsArr.mapNotNull { el ->
            val obj = el.jsonObject
            val origName = obj["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            val desc = obj["description"]?.jsonPrimitive?.contentOrNull ?: origName
            val schema = obj["inputSchema"] as? JsonObject ?: buildJsonObject {
                put("type", "object")
                put("properties", buildJsonObject {})
            }
            val safeTool = origName.replace(Regex("[^a-zA-Z0-9_]"), "_")
            RemoteMcpTool(
                serverId = server.id,
                serverName = server.name,
                originalName = origName,
                namespacedName = "${safePrefix}__$safeTool".take(64),
                description = "[${server.name}] $desc",
                inputSchema = schema
            )
        }
    }

    suspend fun callTool(
        server: RemoteMcpServerConfig,
        toolName: String,
        arguments: JsonObject
    ): String = withContext(Dispatchers.IO) {
        val req = buildJsonObject {
            put("jsonrpc", "2.0")
            put("id", 2)
            put("method", "tools/call")
            put("params", buildJsonObject {
                put("name", toolName)
                put("arguments", arguments)
            })
        }
        val resp = postJsonRpc(server, req)
        val err = resp["error"]?.jsonObject
        if (err != null) {
            return@withContext "Error MCP: ${err["message"]?.jsonPrimitive?.contentOrNull ?: err.toString()}"
        }
        val content = resp["result"]?.jsonObject?.get("content")?.jsonArray
        if (content != null && content.isNotEmpty()) {
            return@withContext content.joinToString("\n") { item ->
                val obj = item.jsonObject
                when (obj["type"]?.jsonPrimitive?.contentOrNull) {
                    "text" -> obj["text"]?.jsonPrimitive?.contentOrNull.orEmpty()
                    "image" -> "[Imagen recibida (${obj["mimeType"]?.jsonPrimitive?.contentOrNull ?: "image"})]"
                    else -> obj.toString()
                }
            }
        }
        resp["result"]?.toString() ?: "OK"
    }
}
