package com.gatovillano.androidmcp

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AndroidAccessibilityBridge : AccessibilityService() {

    companion object {
        @Volatile
        var instance: AndroidAccessibilityBridge? = null
            private set

        fun isConnected(): Boolean = instance != null
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // On-demand UI tree inspection; no background polling overhead.
    }

    override fun onInterrupt() {
    }

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    fun getUiTree(maxNodes: Int = 120): JsonObject {
        val root = rootInActiveWindow ?: return buildJsonObject {
            put("error", "No active window available (screen may be locked or off).")
        }
        val nodes = mutableListOf<JsonObject>()
        collectNodes(root, nodes, maxNodes)
        return buildJsonObject {
            put("package", root.packageName?.toString() ?: "")
            put("node_count", nodes.size)
            put("nodes", JsonArray(nodes))
        }
    }

    private fun collectNodes(node: AccessibilityNodeInfo, out: MutableList<JsonObject>, maxNodes: Int) {
        if (out.size >= maxNodes) return

        val text = node.text?.toString()?.trim().orEmpty()
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        val resId = node.viewIdResourceName?.substringAfterLast("/")?.trim().orEmpty()
        val useful = text.isNotEmpty() || desc.isNotEmpty() || node.isClickable || node.isEditable || node.isScrollable

        if (useful && node.isVisibleToUser) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (rect.width() > 0 && rect.height() > 0) {
                out.add(
                    buildJsonObject {
                        if (text.isNotEmpty()) put("text", text.take(200))
                        if (desc.isNotEmpty()) put("desc", desc.take(200))
                        if (resId.isNotEmpty()) put("id", resId)
                        put("class", node.className?.toString()?.substringAfterLast(".") ?: "")
                        if (node.isClickable) put("clickable", true)
                        if (node.isEditable) put("editable", true)
                        if (node.isScrollable) put("scrollable", true)
                        put("cx", rect.centerX())
                        put("cy", rect.centerY())
                    }
                )
            }
        }

        for (i in 0 until node.childCount) {
            if (out.size >= maxNodes) break
            val child = node.getChild(i) ?: continue
            collectNodes(child, out, maxNodes)
        }
    }

    fun tapByTextOrCoords(query: String?, x: Int?, y: Int?): String {
        if (!query.isNullOrBlank()) {
            val root = rootInActiveWindow ?: return "Error: No active window."
            val target = findNodeByQuery(root, query.lowercase())
                ?: return "Error: No UI node matching '$query' found."

            var clickable: AccessibilityNodeInfo? = target
            while (clickable != null && !clickable.isClickable) {
                clickable = clickable.parent
            }
            if (clickable != null && clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return "Clicked node matching '$query'."
            }
            val rect = Rect()
            target.getBoundsInScreen(rect)
            return dispatchTap(rect.centerX().toFloat(), rect.centerY().toFloat())
        }

        if (x != null && y != null) {
            return dispatchTap(x.toFloat(), y.toFloat())
        }
        return "Error: Provide either 'text' or both 'x' and 'y' coordinates."
    }

    private fun findNodeByQuery(node: AccessibilityNodeInfo, q: String): AccessibilityNodeInfo? {
        val text = node.text?.toString()?.lowercase().orEmpty()
        val desc = node.contentDescription?.toString()?.lowercase().orEmpty()
        if (text.contains(q) || desc.contains(q)) {
            return node
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val match = findNodeByQuery(child, q)
            if (match != null) return match
        }
        return null
    }

    fun typeText(text: String, targetQuery: String? = null): String {
        val root = rootInActiveWindow ?: return "Error: No active window."
        val target = if (!targetQuery.isNullOrBlank()) {
            findNodeByQuery(root, targetQuery.lowercase())
        } else {
            root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: findFirstEditable(root)
        } ?: return "Error: No editable input field found."

        target.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        return if (ok) "Typed text into field." else "Error: Failed to set text on target field."
    }

    private fun findFirstEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable && node.isVisibleToUser) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val match = findFirstEditable(child)
            if (match != null) return match
        }
        return null
    }

    fun scroll(direction: String): String {
        val dm = resources.displayMetrics
        val cx = dm.widthPixels / 2f
        val cy = dm.heightPixels / 2f
        val dx = dm.widthPixels * 0.35f
        val dy = dm.heightPixels * 0.30f

        val (startX, startY, endX, endY) = when (direction.lowercase()) {
            "down" -> listOf(cx, cy + dy, cx, cy - dy)
            "up" -> listOf(cx, cy - dy, cx, cy + dy)
            "left" -> listOf(cx + dx, cy, cx - dx, cy)
            "right" -> listOf(cx - dx, cy, cx + dx, cy)
            else -> return "Error: Invalid direction '$direction'. Use up, down, left, or right."
        }
        return dispatchSwipe(startX, startY, endX, endY, 300L)
    }

    fun performGlobal(action: String): String {
        val code = when (action.lowercase()) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            else -> return "Error: Unknown global action '$action'. Use back, home, recents, notifications, or quick_settings."
        }
        val ok = performGlobalAction(code)
        return if (ok) "Performed global action '$action'." else "Error: Global action '$action' failed."
    }

    private fun dispatchTap(x: Float, y: Float): String {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, 60L)
        return executeGesture(GestureDescription.Builder().addStroke(stroke).build(), "Tapped ($x, $y).")
    }

    private fun dispatchSwipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): String {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs)
        return executeGesture(GestureDescription.Builder().addStroke(stroke).build(), "Swiped ($x1,$y1)->($x2,$y2).")
    }

    private fun executeGesture(gesture: GestureDescription, successMsg: String): String {
        val latch = CountDownLatch(1)
        var completed = false
        val started = dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                completed = true
                latch.countDown()
            }

            override fun onCancelled(gestureDescription: GestureDescription?) {
                latch.countDown()
            }
        }, null)
        if (!started) return "Error: Gesture dispatch was rejected by Android."
        latch.await(2, TimeUnit.SECONDS)
        return if (completed) successMsg else "Gesture dispatched (timed out waiting for callback)."
    }
}
