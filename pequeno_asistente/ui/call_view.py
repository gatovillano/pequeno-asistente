"""
CallView: Full Voice-to-Voice immersive call interface for Pequeño Asistente.
Features an animated pulsating voice orb, live transcription subtitles,
duration timer, and hands-free call controls.
"""
import time
import math
from typing import Optional, Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk


class VoiceOrb(Gtk.DrawingArea):
    """
    Animated pulsating Voice Orb drawn with Cairo.
    Reacts dynamically to real-time audio levels and call states.
    """

    def __init__(self):
        super().__init__()
        self.set_content_width(180)
        self.set_content_height(180)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self.state = "listening"  # "listening", "user_speaking", "thinking", "assistant_speaking", "muted"
        self.level = 0.0  # 0.0 to 1.0
        self.smoothed_level = 0.0
        self.phase = 0.0

        self.set_draw_func(self._draw)

        # 30 FPS pulse animation
        self._anim_tag = GLib.timeout_add(33, self._on_animate)

    def set_state(self, state: str):
        self.state = state
        self.queue_draw()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, level))

    def _on_animate(self):
        self.phase += 0.08
        if self.phase > 2 * math.pi:
            self.phase -= 2 * math.pi

        # Smooth audio level transition
        target = self.level
        self.smoothed_level = self.smoothed_level * 0.75 + target * 0.25

        self.queue_draw()
        return True

    def _draw(self, _area, cr, width, height):
        cx = width / 2.0
        cy = height / 2.0
        base_radius = min(width, height) * 0.28

        # Color schemes by state (R, G, B)
        if self.state == "listening":
            # Calm Emerald / Cyan
            color_core = (0.23, 0.78, 0.65)
            color_glow = (0.15, 0.65, 0.55)
            pulse_amp = 0.06 * math.sin(self.phase * 1.5)
        elif self.state == "user_speaking":
            # Active Neon Cyan / Green
            color_core = (0.20, 0.90, 0.85)
            color_glow = (0.10, 0.75, 0.70)
            pulse_amp = 0.22 * self.smoothed_level + 0.08 * math.sin(self.phase * 3.0)
        elif self.state == "thinking":
            # Amber / Warm Gold
            color_core = (0.95, 0.70, 0.20)
            color_glow = (0.85, 0.55, 0.15)
            pulse_amp = 0.12 * math.sin(self.phase * 4.0)
        elif self.state == "assistant_speaking":
            # Radiant Purple / Indigo / Magenta
            color_core = (0.65, 0.45, 0.95)
            color_glow = (0.50, 0.30, 0.85)
            pulse_amp = 0.18 * math.sin(self.phase * 2.5) + 0.05
        elif self.state == "muted":
            # Muted Charcoal / Red tint
            color_core = (0.60, 0.30, 0.35)
            color_glow = (0.45, 0.20, 0.25)
            pulse_amp = 0.0
        else:
            color_core = (0.35, 0.40, 0.55)
            color_glow = (0.25, 0.30, 0.45)
            pulse_amp = 0.0

        dynamic_radius = base_radius * (1.0 + pulse_amp)

        # 1. Outer ambient glow ring
        cr.save()
        cr.set_source_rgba(color_glow[0], color_glow[1], color_glow[2], 0.18 + 0.12 * pulse_amp)
        cr.arc(cx, cy, dynamic_radius * 1.55, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 2. Mid energy ring
        cr.save()
        cr.set_source_rgba(color_glow[0], color_glow[1], color_glow[2], 0.35)
        cr.arc(cx, cy, dynamic_radius * 1.25, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 3. Inner dense glowing core
        cr.save()
        cr.set_source_rgba(color_core[0], color_core[1], color_core[2], 0.92)
        cr.arc(cx, cy, dynamic_radius, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 4. Center highlight specular spot
        cr.save()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.40)
        cr.arc(cx - dynamic_radius * 0.25, cy - dynamic_radius * 0.25, dynamic_radius * 0.35, 0, 2 * math.pi)
        cr.fill()
        cr.restore()


class CallView(Gtk.Box):
    """
    Dedicated view for continuous Voice-to-Voice calls.
    """

    def __init__(
        self,
        on_end_call_requested: Callable[[], None],
        on_mute_toggled: Callable[[], bool],
        on_interrupt_requested: Callable[[], None]
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.on_end_call_requested = on_end_call_requested
        self.on_mute_toggled = on_mute_toggled
        self.on_interrupt_requested = on_interrupt_requested

        self.add_css_class("call-screen")
        self.set_vexpand(True)
        self.set_hexpand(True)

        self._start_time = 0.0
        self._timer_tag: Optional[int] = None

        # -------------------------------------------------------------
        # TOP HEADER: Call badge & Duration Timer
        # -------------------------------------------------------------
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_bar.add_css_class("call-top-bar")
        top_bar.set_halign(Gtk.Align.CENTER)
        top_bar.set_margin_top(16)

        self.status_badge = Gtk.Label(label="🟢 Escuchando...")
        self.status_badge.add_css_class("call-status-badge")
        top_bar.append(self.status_badge)

        self.timer_label = Gtk.Label(label="00:00")
        self.timer_label.add_css_class("call-timer-label")
        top_bar.append(self.timer_label)

        self.append(top_bar)

        # -------------------------------------------------------------
        # CENTER: Animated Voice Orb
        # -------------------------------------------------------------
        orb_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        orb_container.set_valign(Gtk.Align.CENTER)
        orb_container.set_vexpand(True)
        orb_container.set_margin_top(12)
        orb_container.set_margin_bottom(8)

        self.orb = VoiceOrb()
        orb_container.append(self.orb)
        self.append(orb_container)

        # -------------------------------------------------------------
        # SUBTITLES AREA: Live speech & response
        # -------------------------------------------------------------
        subtitles_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        subtitles_box.add_css_class("call-subtitles-container")
        subtitles_box.set_halign(Gtk.Align.CENTER)
        subtitles_box.set_valign(Gtk.Align.CENTER)
        subtitles_box.set_margin_start(24)
        subtitles_box.set_margin_end(24)

        self.user_sub_label = Gtk.Label(label="", wrap=True)
        self.user_sub_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.user_sub_label.set_max_width_chars(38)
        self.user_sub_label.set_justify(Gtk.Justification.CENTER)
        self.user_sub_label.add_css_class("call-user-subtitle")
        self.user_sub_label.set_visible(False)
        subtitles_box.append(self.user_sub_label)

        self.asst_sub_label = Gtk.Label(label="", wrap=True)
        self.asst_sub_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.asst_sub_label.set_max_width_chars(38)
        self.asst_sub_label.set_justify(Gtk.Justification.CENTER)
        self.asst_sub_label.add_css_class("call-asst-subtitle")
        self.asst_sub_label.set_visible(False)
        subtitles_box.append(self.asst_sub_label)

        self.append(subtitles_box)

        # -------------------------------------------------------------
        # BOTTOM CONTROLS BAR: Mute, Interrupt, Hang up
        # -------------------------------------------------------------
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        controls_box.add_css_class("call-controls-bar")
        controls_box.set_halign(Gtk.Align.CENTER)
        controls_box.set_valign(Gtk.Align.END)
        controls_box.set_margin_bottom(22)

        # 1. Mute button
        self.mute_btn = Gtk.Button(icon_name="audio-input-microphone-symbolic")
        self.mute_btn.add_css_class("call-control-btn")
        self.mute_btn.set_tooltip_text("Silenciar / Reactivar micrófono")
        self.mute_btn.connect("clicked", self._on_mute_clicked)
        controls_box.append(self.mute_btn)

        # 2. Interrupt speech button
        self.interrupt_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self.interrupt_btn.add_css_class("call-control-btn")
        self.interrupt_btn.set_tooltip_text("Interrumpir al asistente")
        self.interrupt_btn.connect("clicked", lambda _b: self.on_interrupt_requested())
        controls_box.append(self.interrupt_btn)

        # 3. End Call button (Large red capsule)
        self.end_call_btn = Gtk.Button()
        self.end_call_btn.add_css_class("call-end-btn")
        self.end_call_btn.set_tooltip_text("Finalizar llamada (Colgar)")

        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_icon = Gtk.Image.new_from_icon_name("call-stop-symbolic")
        end_lbl = Gtk.Label(label="<b>Finalizar</b>", use_markup=True)
        end_box.append(end_icon)
        end_box.append(end_lbl)
        self.end_call_btn.set_child(end_box)
        self.end_call_btn.connect("clicked", lambda _b: self.on_end_call_requested())
        controls_box.append(self.end_call_btn)

        self.append(controls_box)

    def start_session(self):
        """Starts timer and resets state when entering call mode."""
        self._start_time = time.time()
        self.timer_label.set_text("00:00")
        self.reset_transcript()
        self.set_state("listening", "Escuchando...")

        if self._timer_tag:
            GLib.source_remove(self._timer_tag)
        self._timer_tag = GLib.timeout_add(1000, self._on_timer_tick)

    def stop_session(self):
        """Cleans up timer when leaving call mode."""
        if self._timer_tag:
            GLib.source_remove(self._timer_tag)
            self._timer_tag = None

    def _on_timer_tick(self):
        elapsed = int(time.time() - self._start_time)
        mins = elapsed // 60
        secs = elapsed % 60
        self.timer_label.set_text(f"{mins:02d}:{secs:02d}")
        return True

    def _on_mute_clicked(self, _btn):
        is_muted = self.on_mute_toggled()
        if is_muted:
            self.mute_btn.set_icon_name("microphone-disabled-symbolic")
            self.mute_btn.add_css_class("call-btn-active-mute")
        else:
            self.mute_btn.set_icon_name("audio-input-microphone-symbolic")
            self.mute_btn.remove_css_class("call-btn-active-mute")

    def set_state(self, state: str, desc: str):
        """Updates UI status and voice orb appearance."""
        self.orb.set_state(state)

        if state in ("listening", "user_speaking"):
            self.status_badge.set_text("🟢 " + desc)
            self.status_badge.remove_css_class("call-badge-thinking")
            self.status_badge.remove_css_class("call-badge-speaking")
            self.status_badge.add_css_class("call-badge-listening")
        elif state == "thinking":
            self.status_badge.set_text("🟡 " + desc)
            self.status_badge.remove_css_class("call-badge-listening")
            self.status_badge.remove_css_class("call-badge-speaking")
            self.status_badge.add_css_class("call-badge-thinking")
        elif state == "assistant_speaking":
            self.status_badge.set_text("🟣 " + desc)
            self.status_badge.remove_css_class("call-badge-listening")
            self.status_badge.remove_css_class("call-badge-thinking")
            self.status_badge.add_css_class("call-badge-speaking")
        elif state == "muted":
            self.status_badge.set_text("🔴 Silenciado")
            self.status_badge.remove_css_class("call-badge-listening")
            self.status_badge.remove_css_class("call-badge-thinking")
            self.status_badge.remove_css_class("call-badge-speaking")

    def set_audio_level(self, level: float):
        self.orb.set_level(level)

    def set_user_text(self, text: str):
        if text.strip():
            self.user_sub_label.set_text(f"“{text}”")
            self.user_sub_label.set_visible(True)
            self.asst_sub_label.set_visible(False)
            self.asst_sub_label.set_text("")

    def append_assistant_token(self, token: str):
        curr = self.asst_sub_label.get_text()
        self.asst_sub_label.set_text(curr + token)
        self.asst_sub_label.set_visible(True)

    def reset_transcript(self):
        self.user_sub_label.set_text("")
        self.user_sub_label.set_visible(False)
        self.asst_sub_label.set_text("")
        self.asst_sub_label.set_visible(False)
