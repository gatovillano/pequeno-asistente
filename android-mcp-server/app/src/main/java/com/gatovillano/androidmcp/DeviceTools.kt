package com.gatovillano.androidmcp

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.location.LocationManager
import android.media.ImageReader
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.PowerManager
import android.util.Base64
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume

class DeviceTools(private val context: Context) {

    fun getDeviceStatus(): JsonObject {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val batteryPct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val isCharging = bm.isCharging

        val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val networkType = when {
            caps == null -> "offline"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
            else -> "other"
        }

        return buildJsonObject {
            put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
            put("android_version", Build.VERSION.RELEASE)
            put("sdk_int", Build.VERSION.SDK_INT)
            put("battery_percent", batteryPct)
            put("is_charging", isCharging)
            put("screen_interactive", pm.isInteractive)
            put("network", networkType)
            put("accessibility_connected", AndroidAccessibilityBridge.isConnected())
            put("notifications_connected", NotificationBridgeService.isConnected())
        }
    }

    fun listInstalledApps(query: String? = null, limit: Int = 80): JsonArray {
        val pm = context.packageManager
        val launcherIntent = Intent(Intent.ACTION_MAIN, null).apply {
            addCategory(Intent.CATEGORY_LAUNCHER)
        }
        val apps = pm.queryIntentActivities(launcherIntent, 0)
        val q = query?.trim()?.lowercase().orEmpty()

        return buildJsonArray {
            apps.asSequence()
                .map { ri ->
                    val label = ri.loadLabel(pm)?.toString().orEmpty()
                    val pkg = ri.activityInfo.packageName
                    label to pkg
                }
                .distinctBy { it.second }
                .filter { (label, pkg) ->
                    q.isEmpty() || label.lowercase().contains(q) || pkg.lowercase().contains(q)
                }
                .sortedBy { it.first.lowercase() }
                .take(limit)
                .forEach { (label, pkg) ->
                    add(
                        buildJsonObject {
                            put("label", label)
                            put("package", pkg)
                        }
                    )
                }
        }
    }

    fun openApp(packageOrName: String): String {
        val pm = context.packageManager
        val direct = pm.getLaunchIntentForPackage(packageOrName)
        val intent = if (direct != null) {
            direct
        } else {
            val launcherIntent = Intent(Intent.ACTION_MAIN, null).apply {
                addCategory(Intent.CATEGORY_LAUNCHER)
            }
            val match = pm.queryIntentActivities(launcherIntent, 0).firstOrNull { ri ->
                val label = ri.loadLabel(pm)?.toString()?.lowercase().orEmpty()
                label.contains(packageOrName.lowercase()) ||
                    ri.activityInfo.packageName.lowercase().contains(packageOrName.lowercase())
            }
            if (match != null) pm.getLaunchIntentForPackage(match.activityInfo.packageName) else null
        } ?: return "Error: No launchable app found matching '$packageOrName'."

        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return "Launched app '${intent.`package` ?: packageOrName}'."
    }

    fun openUrl(url: String): String {
        return try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
            "Opened URI '$url'."
        } catch (e: Exception) {
            "Error opening URI '$url': ${e.message}"
        }
    }

    fun listSensors(): JsonArray {
        val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        return buildJsonArray {
            sm.getSensorList(Sensor.TYPE_ALL).forEach { s ->
                add(
                    buildJsonObject {
                        put("name", s.name)
                        put("type", s.type)
                        put("string_type", s.stringType ?: "")
                        put("vendor", s.vendor)
                    }
                )
            }
        }
    }

    suspend fun readSensor(sensorName: String): JsonObject {
        val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val sensorType = when (sensorName.lowercase().trim()) {
            "accelerometer", "accel" -> Sensor.TYPE_ACCELEROMETER
            "gyroscope", "gyro" -> Sensor.TYPE_GYROSCOPE
            "light" -> Sensor.TYPE_LIGHT
            "proximity" -> Sensor.TYPE_PROXIMITY
            "magnetic", "compass", "magnetometer" -> Sensor.TYPE_MAGNETIC_FIELD
            "pressure", "barometer" -> Sensor.TYPE_PRESSURE
            "gravity" -> Sensor.TYPE_GRAVITY
            "step_counter", "steps" -> Sensor.TYPE_STEP_COUNTER
            else -> sensorName.toIntOrNull() ?: -1
        }
        val sensor = sm.getDefaultSensor(sensorType)
            ?: return buildJsonObject {
                put("error", "Sensor '$sensorName' is not available on this device.")
            }

        val values = withTimeoutOrNull(2500L) {
            suspendCancellableCoroutine<FloatArray?> { cont ->
                val listener = object : SensorEventListener {
                    override fun onSensorChanged(event: SensorEvent?) {
                        sm.unregisterListener(this)
                        if (cont.isActive) cont.resume(event?.values?.clone())
                    }

                    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
                }
                sm.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_NORMAL)
                cont.invokeOnCancellation { sm.unregisterListener(listener) }
            }
        } ?: return buildJsonObject {
            put("error", "Timed out waiting for reading from '${sensor.name}'.")
        }

        return buildJsonObject {
            put("sensor", sensor.name)
            put("type", sensorName)
            put("values", buildJsonArray { values.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) } })
        }
    }

    fun getLastLocation(): JsonObject {
        val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) {
            return buildJsonObject {
                put("error", "Location permission not granted. Grant it in Android MCP Server app.")
            }
        }
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER)
        val best = providers.mapNotNull { p ->
            try {
                @Suppress("MissingPermission")
                lm.getLastKnownLocation(p)
            } catch (_: Exception) {
                null
            }
        }.maxByOrNull { it.time }

        if (best == null) {
            return buildJsonObject { put("error", "No last known location available yet.") }
        }
        return buildJsonObject {
            put("latitude", best.latitude)
            put("longitude", best.longitude)
            put("accuracy_m", best.accuracy)
            put("provider", best.provider ?: "")
            put("time_ms", best.time)
        }
    }

    suspend fun capturePhotoBase64(lens: String = "back", maxWidth: Int = 720): Result<String> {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            return Result.failure(IllegalStateException("CAMERA permission not granted."))
        }
        val camMgr = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val targetFacing = if (lens.lowercase() == "front") {
            CameraCharacteristics.LENS_FACING_FRONT
        } else {
            CameraCharacteristics.LENS_FACING_BACK
        }
        val cameraId = camMgr.cameraIdList.firstOrNull { id ->
            camMgr.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == targetFacing
        } ?: camMgr.cameraIdList.firstOrNull()
            ?: return Result.failure(IllegalStateException("No camera found on device."))

        val thread = HandlerThread("McpCameraThread").apply { start() }
        val handler = Handler(thread.looper)

        return try {
            val jpegBytes = withTimeoutOrNull(8000L) {
                suspendCancellableCoroutine<ByteArray?> { cont ->
                    val reader = ImageReader.newInstance(640, 480, ImageFormat.JPEG, 2)
                    var openedDevice: CameraDevice? = null

                    fun cleanup() {
                        try { openedDevice?.close() } catch (_: Exception) {}
                        try { reader.close() } catch (_: Exception) {}
                    }

                    reader.setOnImageAvailableListener({ r ->
                        val img = r.acquireLatestImage() ?: return@setOnImageAvailableListener
                        val buffer = img.planes[0].buffer
                        val bytes = ByteArray(buffer.remaining())
                        buffer.get(bytes)
                        img.close()
                        cleanup()
                        if (cont.isActive) cont.resume(bytes)
                    }, handler)

                    cont.invokeOnCancellation { cleanup() }

                    try {
                        @Suppress("MissingPermission")
                        camMgr.openCamera(cameraId, object : CameraDevice.StateCallback() {
                            override fun onOpened(camera: CameraDevice) {
                                openedDevice = camera
                                try {
                                    @Suppress("DEPRECATION")
                                    camera.createCaptureSession(
                                        listOf(reader.surface),
                                        object : CameraCaptureSession.StateCallback() {
                                            override fun onConfigured(session: CameraCaptureSession) {
                                                try {
                                                    val req = camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                                                        addTarget(reader.surface)
                                                    }
                                                    session.capture(req.build(), null, handler)
                                                } catch (_: Exception) {
                                                    cleanup()
                                                    if (cont.isActive) cont.resume(null)
                                                }
                                            }

                                            override fun onConfigureFailed(session: CameraCaptureSession) {
                                                cleanup()
                                                if (cont.isActive) cont.resume(null)
                                            }
                                        },
                                        handler
                                    )
                                } catch (_: Exception) {
                                    cleanup()
                                    if (cont.isActive) cont.resume(null)
                                }
                            }

                            override fun onDisconnected(camera: CameraDevice) {
                                cleanup()
                                if (cont.isActive) cont.resume(null)
                            }

                            override fun onError(camera: CameraDevice, error: Int) {
                                cleanup()
                                if (cont.isActive) cont.resume(null)
                            }
                        }, handler)
                    } catch (_: Exception) {
                        cleanup()
                        if (cont.isActive) cont.resume(null)
                    }
                }
            } ?: return Result.failure(IllegalStateException("Camera capture timed out or failed."))

            val bmp = BitmapFactory.decodeByteArray(jpegBytes, 0, jpegBytes.size)
            val finalBytes = if (bmp != null && bmp.width > maxWidth) {
                val h = (bmp.height * (maxWidth.toFloat() / bmp.width)).toInt().coerceAtLeast(1)
                val scaled = Bitmap.createScaledBitmap(bmp, maxWidth, h, true)
                val out = ByteArrayOutputStream()
                scaled.compress(Bitmap.CompressFormat.JPEG, 75, out)
                bmp.recycle()
                scaled.recycle()
                out.toByteArray()
            } else {
                jpegBytes
            }
            Result.success(Base64.encodeToString(finalBytes, Base64.NO_WRAP))
        } finally {
            thread.quitSafely()
        }
    }
}
