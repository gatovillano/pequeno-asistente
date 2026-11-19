package com.gatovillano.androidmcp

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class McpProtocolHandler(private val deviceTools: DeviceTools) {

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    suspend fun handleJsonRpc(rawBody: String): String? {
        val req = try {
            json.parseToJsonElement(rawBody).jsonObject
        } catch (e: Exception) {
            return errorResponse(JsonNull, -32700, "Parse error: ${e.message}").toString()
        }

        val id: JsonElement = req["id"] ?: JsonNull
        val method = req["method"]?.jsonPrimitive?.contentOrNull
            ?: return errorResponse(id, -32600, "Missing method").toString()
        val params = req["params"] as? JsonObject ?: buildJsonObject {}

        // JSON-RPC notifications have no id and expect no body
        if (method.startsWith("notifications/")) {
            return null
        }

        val response = when (method) {
            "initialize" -> okResponse(
                id,
                buildJsonObject {
                    put("protocolVersion", "2025-03-26")
                    put("capabilities", buildJsonObject {
                        put("tools", buildJsonObject { put("listChanged", false) })
                    })
                    put("serverInfo", buildJsonObject {
                        put("name", "pequeno-asistente-android-mcp")
                        put("version", "1.0.0")
                    })
                }
            )
            "ping" -> okResponse(id, buildJsonObject {})
            "tools/list" -> okResponse(id, buildJsonObject { put("tools", getToolDefinitions()) })
            "tools/call" -> handleToolCall(id, params)
            else -> errorResponse(id, -32601, "Method not found: $method")
        }
        return response.toString()
    }

    private suspend fun handleToolCall(id: JsonElement, params: JsonObject): JsonObject {
        val name = params["name"]?.jsonPrimitive?.contentOrNull
            ?: return errorResponse(id, -32602, "Missing tool name")
        val args = params["arguments"] as? JsonObject ?: buildJsonObject {}

        return try {
            val resultObj = when (name) {
                "device_status" -> textResult(deviceTools.getDeviceStatus().toString())
                "apps_list" -> textResult(
                    deviceTools.listInstalledApps(
                        query = args["query"]?.jsonPrimitive?.contentOrNull,
                        limit = args["limit"]?.jsonPrimitive?.intOrNull ?: 80
                    ).toString()
                )
                "apps_open" -> {
                    val target = args["app"]?.jsonPrimitive?.contentOrNull
                        ?: args["package_name"]?.jsonPrimitive?.contentOrNull
                        ?: return okResponse(id, textResult("Error: Missing 'app' or 'package_name' argument.", isError = true))
                    val out = deviceTools.openApp(target)
                    textResult(out, isError = out.startsWith("Error:"))
                }
                "intent_open_url" -> {
                    val url = args["url"]?.jsonPrimitive?.contentOrNull
                        ?: return okResponse(id, textResult("Error: Missing 'url' argument.", isError = true))
                    val out = deviceTools.openUrl(url)
                    textResult(out, isError = out.startsWith("Error"))
                }
                "sensors_list" -> textResult(deviceTools.listSensors().toString())
                "sensors_read" -> {
                    val sensorType = args["sensor"]?.jsonPrimitive?.contentOrNull ?: "accelerometer"
                    textResult(deviceTools.readSensor(sensorType).toString())
                }
                "location_get" -> textResult(deviceTools.getLastLocation().toString())
                "camera_capture" -> {
                    val lens = args["lens"]?.jsonPrimitive?.contentOrNull ?: "back"
                    val capture = deviceTools.capturePhotoBase64(lens = lens)
                    if (capture.isSuccess) {
                        buildJsonObject {
                            put("isError", false)
                            put("content", buildJsonArray {
                                add(buildJsonObject {
                                    put("type", "image")
                                    put("data", capture.getOrThrow())
                                    put("mimeType", "image/jpeg")
                                })
                            })
                        }
                    } else {
                        textResult("Error: ${capture.exceptionOrNull()?.message}", isError = true)
                    }
                }
                "notifications_list" -> {
                    val svc = NotificationBridgeService.instance
                    if (svc == null) {
                        textResult("Error: Notification Access is not enabled in Android Settings.", isError = true)
                    } else {
                        val limit = args["limit"]?.jsonPrimitive?.intOrNull ?: 25
                        textResult(svc.listActiveNotifications(limit).toString())
                    }
                }
                "ui_get_tree" -> withAccessibility { svc ->
                    val maxNodes = args["max_nodes"]?.jsonPrimitive?.intOrNull ?: 120
                    textResult(svc.getUiTree(maxNodes).toString())
                }
                "ui_tap" -> withAccessibility { svc ->
                    val text = args["text"]?.jsonPrimitive?.contentOrNull
                    val x = args["x"]?.jsonPrimitive?.intOrNull
                    val y = args["y"]?.jsonPrimitive?.intOrNull
                    val out = svc.tapByTextOrCoords(text, x, y)
                    textResult(out, isError = out.startsWith("Error:"))
                }
                "ui_type" -> withAccessibility { svc ->
                    val text = args["text"]?.jsonPrimitive?.contentOrNull
                        ?: return@withAccessibility textResult("Error: Missing 'text' argument.", isError = true)
                    val target = args["target"]?.jsonPrimitive?.contentOrNull
                    val out = svc.typeText(text, target)
                    textResult(out, isError = out.startsWith("Error:"))
                }
                "ui_scroll" -> withAccessibility { svc ->
                    val dir = args["direction"]?.jsonPrimitive?.contentOrNull ?: "down"
                    val out = svc.scroll(dir)
                    textResult(out, isError = out.startsWith("Error:"))
                }
                "ui_global_action" -> withAccessibility { svc ->
                    val action = args["action"]?.jsonPrimitive?.contentOrNull ?: "home"
                    val out = svc.performGlobal(action)
                    textResult(out, isError = out.startsWith("Error:"))
                }
                else -> textResult("Error: Unknown Android tool '$name'.", isError = true)
            }
            okResponse(id, resultObj)
        } catch (e: Exception) {
            okResponse(id, textResult("Error executing '$name': ${e.message}", isError = true))
        }
    }

    private fun withAccessibility(block: (AndroidAccessibilityBridge) -> JsonObject): JsonObject {
        val svc = AndroidAccessibilityBridge.instance
            ?: return textResult(
                "Error: Android Accessibility Service is not enabled. Enable 'Pequeño Asistente UI Control' in Android Accessibility Settings.",
                isError = true
            )
        return block(svc)
    }

    private fun textResult(text: String, isError: Boolean = false): JsonObject = buildJsonObject {
        put("isError", isError)
        put("content", buildJsonArray {
            add(buildJsonObject {
                put("type", "text")
                put("text", text)
            })
        })
    }

    private fun okResponse(id: JsonElement, result: JsonObject): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0")
        put("id", id)
        put("result", result)
    }

    private fun errorResponse(id: JsonElement, code: Int, message: String): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0")
        put("id", id)
        put("error", buildJsonObject {
            put("code", code)
            put("message", message)
        })
    }

    fun getToolDefinitions(): JsonArray = buildJsonArray {
        add(tool("device_status", "Returns battery level, charging state, network type, screen state, and permission status."))
        add(tool("apps_list", "Lists launchable installed Android apps.", mapOf(
            "query" to ("string" to "Optional substring filter by app name or package"),
            "limit" to ("integer" to "Maximum apps to return (default 80)")
        )))
        add(tool("apps_open", "Opens an installed Android app by package name or app display name.", mapOf(
            "app" to ("string" to "Package name (e.g. com.whatsapp) or app name (e.g. WhatsApp)")
        ), required = listOf("app")))
        add(tool("intent_open_url", "Opens a URL or Android deep link (https://, tel:, geo:, mailto:).", mapOf(
            "url" to ("string" to "URI to open")
        ), required = listOf("url")))
        add(tool("sensors_list", "Lists all available hardware sensors on the Android device."))
        add(tool("sensors_read", "Reads a live snapshot from an Android hardware sensor.", mapOf(
            "sensor" to ("string" to "One of: accelerometer, gyroscope, light, proximity, magnetic, pressure, gravity, step_counter")
        ), required = listOf("sensor")))
        add(tool("location_get", "Returns the device's last known GPS/network coordinates."))
        add(tool("camera_capture", "Captures a JPEG photo from the back or front camera and returns it as an image.", mapOf(
            "lens" to ("string" to "'back' (default) or 'front'")
        )))
        add(tool("notifications_list", "Lists currently active Android notifications.", mapOf(
            "limit" to ("integer" to "Maximum notifications to return (default 25)")
        )))
        add(tool("ui_get_tree", "Inspects the live Android UI hierarchy of the foreground app via AccessibilityService.", mapOf(
            "max_nodes" to ("integer" to "Maximum UI nodes to return (default 120)")
        )))
        add(tool("ui_tap", "Taps a UI element matching text/description or exact screen (x, y) coordinates.", mapOf(
            "text" to ("string" to "Visible text or content description to click"),
            "x" to ("integer" to "X screen coordinate (optional if text provided)"),
            "y" to ("integer" to "Y screen coordinate (optional if text provided)")
        )))
        add(tool("ui_type", "Types text into the focused or matching editable field.", mapOf(
            "text" to ("string" to "Text to enter"),
            "target" to ("string" to "Optional label/hint of the input field")
        ), required = listOf("text")))
        add(tool("ui_scroll", "Performs a swipe/scroll gesture on the active screen.", mapOf(
            "direction" to ("string" to "One of: up, down, left, right")
        ), required = listOf("direction")))
        add(tool("ui_global_action", "Performs an Android global navigation action.", mapOf(
            "action" to ("string" to "One of: back, home, recents, notifications, quick_settings")
        ), required = listOf("action")))
    }

    private fun tool(
        name: String,
        description: String,
        props: Map<String, Pair<String, String>> = emptyMap(),
        required: List<String> = emptyList()
    ): JsonObject = buildJsonObject {
        put("name", name)
        put("description", description)
        put("inputSchema", buildJsonObject {
            put("type", "object")
            put("properties", buildJsonObject {
                props.forEach { (k, v) ->
                    put(k, buildJsonObject {
                        put("type", v.first)
                        put("description", v.second)
                    })
                }
            })
            if (required.isNotEmpty()) {
                put("required", buildJsonArray { required.forEach { add(JsonPrimitive(it)) } })
            }
        })
    }
}
