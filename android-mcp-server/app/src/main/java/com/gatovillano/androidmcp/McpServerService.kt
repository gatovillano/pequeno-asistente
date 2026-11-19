package com.gatovillano.androidmcp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.cio.CIO
import io.ktor.server.engine.ApplicationEngine
import io.ktor.server.engine.embeddedServer
import io.ktor.server.plugins.cors.routing.CORS
import io.ktor.server.request.receiveText
import io.ktor.server.response.respondText
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.routing

class McpServerService : Service() {

    companion object {
        private const val CHANNEL_ID = "android_mcp_server_channel"
        private const val NOTIF_ID = 1001

        @Volatile
        var isRunning: Boolean = false
            private set
    }

    private var server: ApplicationEngine? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createNotificationChannel()
        val port = McpConfig.getPort(this)
        val notification = buildNotification(port)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIF_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            )
        } else {
            startForeground(NOTIF_ID, notification)
        }

        if (server == null) {
            startKtorServer(port)
        }
        return START_STICKY
    }

    private fun startKtorServer(port: Int) {
        val deviceTools = DeviceTools(applicationContext)
        val protocol = McpProtocolHandler(deviceTools)

        server = embeddedServer(CIO, host = "0.0.0.0", port = port) {
            install(CORS) {
                anyHost()
                allowMethod(HttpMethod.Get)
                allowMethod(HttpMethod.Post)
                allowMethod(HttpMethod.Delete)
                allowMethod(HttpMethod.Options)
                allowHeader(HttpHeaders.Authorization)
                allowHeader(HttpHeaders.ContentType)
                allowHeader("Mcp-Session-Id")
                allowHeader("MCP-Protocol-Version")
            }

            routing {
                get("/health") {
                    val status = deviceTools.getDeviceStatus().toString()
                    call.respondText(status, ContentType.Application.Json, HttpStatusCode.OK)
                }

                suspend fun handleMcp(call: io.ktor.server.application.ApplicationCall) {
                    val expectedToken = McpConfig.getOrCreateToken(applicationContext)
                    val authHeader = call.request.headers[HttpHeaders.Authorization].orEmpty().trim()
                    val provided = if (authHeader.startsWith("Bearer ", ignoreCase = true)) {
                        authHeader.substring(7).trim()
                    } else {
                        ""
                    }
                    if (provided != expectedToken) {
                        call.respondText(
                            """{"error":"Unauthorized. Provide Authorization: Bearer <token>"}""",
                            ContentType.Application.Json,
                            HttpStatusCode.Unauthorized
                        )
                        return
                    }

                    val body = call.receiveText()
                    val responseJson = protocol.handleJsonRpc(body)
                    if (responseJson == null) {
                        call.respondText("", ContentType.Application.Json, HttpStatusCode.Accepted)
                    } else {
                        call.respondText(responseJson, ContentType.Application.Json, HttpStatusCode.OK)
                    }
                }

                post("/mcp") { handleMcp(call) }
                post("/mcp/") { handleMcp(call) }
            }
        }.start(wait = false)

        isRunning = true
    }

    override fun onDestroy() {
        isRunning = false
        try {
            server?.stop(500, 1500)
        } catch (_: Exception) {
        }
        server = null
        super.onDestroy()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Android MCP Server",
                NotificationManager.IMPORTANCE_LOW
            )
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(port: Int): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Android MCP Server activo")
            .setContentText("Escuchando Streamable HTTP en puerto $port (/mcp)")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(openIntent)
            .setOngoing(true)
            .build()
    }
}
