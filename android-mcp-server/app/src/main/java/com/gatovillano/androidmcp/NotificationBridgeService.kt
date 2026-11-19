package com.gatovillano.androidmcp

import android.app.Notification
import android.service.notification.NotificationListenerService
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class NotificationBridgeService : NotificationListenerService() {

    companion object {
        @Volatile
        var instance: NotificationBridgeService? = null
            private set

        fun isConnected(): Boolean = instance != null
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
    }

    override fun onListenerDisconnected() {
        if (instance === this) {
            instance = null
        }
        super.onListenerDisconnected()
    }

    fun listActiveNotifications(limit: Int = 25): JsonArray {
        val active = try {
            activeNotifications ?: emptyArray()
        } catch (_: Exception) {
            emptyArray()
        }
        return buildJsonArray {
            active.take(limit).forEach { sbn ->
                val extras = sbn.notification?.extras
                val title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
                val text = extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
                add(
                    buildJsonObject {
                        put("key", sbn.key)
                        put("package", sbn.packageName)
                        put("title", title.take(150))
                        put("text", text.take(300))
                        put("post_time_ms", sbn.postTime)
                    }
                )
            }
        }
    }
}
