package com.gatovillano.androidmcp

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

data class ProviderPreset(
    val id: String,
    val name: String,
    val baseUrl: String,
    val defaultModel: String,
    val suggestedModels: List<String>,
    val isAnthropic: Boolean = false
)

@Serializable
data class RemoteMcpServerConfig(
    val id: String,
    val name: String,
    val url: String,
    val bearerToken: String = "",
    val enabled: Boolean = true
)

object ProviderCatalog {
    val presets: List<ProviderPreset> = listOf(
        ProviderPreset(
            id = "openrouter",
            name = "OpenRouter",
            baseUrl = "https://openrouter.ai/api/v1",
            defaultModel = "google/gemini-2.5-flash",
            suggestedModels = listOf(
                "google/gemini-2.5-flash",
                "anthropic/claude-3.5-sonnet",
                "meta-llama/llama-3.3-70b-instruct",
                "deepseek/deepseek-chat",
                "openai/gpt-4o-mini"
            )
        ),
        ProviderPreset(
            id = "google",
            name = "Google Gemini",
            baseUrl = "https://generativelanguage.googleapis.com/v1beta/openai",
            defaultModel = "gemini-2.5-flash",
            suggestedModels = listOf(
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash"
            )
        ),
        ProviderPreset(
            id = "openai",
            name = "OpenAI",
            baseUrl = "https://api.openai.com/v1",
            defaultModel = "gpt-4o-mini",
            suggestedModels = listOf("gpt-4o-mini", "gpt-4o", "o3-mini")
        ),
        ProviderPreset(
            id = "anthropic",
            name = "Anthropic Claude",
            baseUrl = "https://api.anthropic.com/v1",
            defaultModel = "claude-3-5-sonnet-latest",
            suggestedModels = listOf("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
            isAnthropic = true
        ),
        ProviderPreset(
            id = "ollama",
            name = "Ollama (Local / LAN)",
            baseUrl = "http://192.168.1.150:11434/v1",
            defaultModel = "llama3.2",
            suggestedModels = listOf("llama3.2", "qwen2.5-coder", "mistral")
        ),
        ProviderPreset(
            id = "kilocode",
            name = "KiloCode Gateway",
            baseUrl = "https://api.kilo.ai/api/gateway",
            defaultModel = "kilo/auto",
            suggestedModels = listOf("kilo/auto", "anthropic/claude-sonnet-4", "openai/gpt-4o")
        ),
        ProviderPreset(
            id = "inception",
            name = "Inception Labs",
            baseUrl = "https://api.inceptionlabs.ai/v1",
            defaultModel = "mercury-2",
            suggestedModels = listOf("mercury-2", "mercury-coder-small")
        ),
        ProviderPreset(
            id = "opencode",
            name = "OpenCode Zen",
            baseUrl = "https://opencode.ai/zen/v1",
            defaultModel = "claude-sonnet-4-5",
            suggestedModels = listOf("claude-sonnet-4-5", "gpt-4o")
        ),
        ProviderPreset(
            id = "custom",
            name = "Custom OpenAI-Compatible",
            baseUrl = "https://api.openai.com/v1",
            defaultModel = "gpt-4o-mini",
            suggestedModels = listOf("gpt-4o-mini")
        )
    )

    fun byId(id: String): ProviderPreset = presets.firstOrNull { it.id == id } ?: presets.first()
}

object AssistantPrefs {
    private const val PREFS = "pequeno_asistente_eink_prefs"
    private const val KEY_PROVIDER = "llm_provider"
    private const val KEY_MODEL = "llm_model"
    private const val KEY_LOCAL_TOOLS = "enable_local_android_tools"
    private const val KEY_REMOTE_MCPS = "remote_mcp_servers_json"

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    fun getProviderId(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_PROVIDER, "openrouter") ?: "openrouter"

    fun setProviderId(context: Context, id: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_PROVIDER, id).apply()
    }

    fun getApiKey(context: Context, providerId: String): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString("api_key_$providerId", "") ?: ""

    fun setApiKey(context: Context, providerId: String, key: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString("api_key_$providerId", key.trim()).apply()
    }

    fun getBaseUrl(context: Context, providerId: String): String {
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString("base_url_$providerId", null)
        return if (!saved.isNullOrBlank()) saved else ProviderCatalog.byId(providerId).baseUrl
    }

    fun setBaseUrl(context: Context, providerId: String, url: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString("base_url_$providerId", url.trim().trimEnd('/')).apply()
    }

    fun getModel(context: Context, providerId: String): String {
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString("${KEY_MODEL}_$providerId", null)
        return if (!saved.isNullOrBlank()) saved else ProviderCatalog.byId(providerId).defaultModel
    }

    fun setModel(context: Context, providerId: String, model: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString("${KEY_MODEL}_$providerId", model.trim()).apply()
    }

    fun isLocalAndroidToolsEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_LOCAL_TOOLS, true)

    fun setLocalAndroidToolsEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_LOCAL_TOOLS, enabled).apply()
    }

    fun getRemoteMcps(context: Context): List<RemoteMcpServerConfig> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_REMOTE_MCPS, null) ?: return emptyList()
        return try {
            json.decodeFromString<List<RemoteMcpServerConfig>>(raw)
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun saveRemoteMcps(context: Context, list: List<RemoteMcpServerConfig>) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_REMOTE_MCPS, json.encodeToString(list)).apply()
    }
}
