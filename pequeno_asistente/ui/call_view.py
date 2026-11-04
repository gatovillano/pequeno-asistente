"""
CallView: Full Voice-to-Voice immersive call interface for Pequeño Asistente.
Features an animated pulsating voice orb, live transcription subtitles,
duration timer, and hands-free call controls.
"""
import time
import math
from typing import Optional, Callable

import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk


class VoiceOrb(Gtk.DrawingArea):
    """
    Animated diffuse Voice Orb drawn with Cairo.
    Features organic fluid shape deformation (morphing blob), soft blur glow,
    zero hard borders, and real-time audio reactivity.
    """

    def __init__(self):
        super().__init__()
        self.set_content_width(220)
        self.set_content_height(220)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self.state = "listening"  # "listening", "user_speaking", "thinking", "assistant_speaking", "muted"
        self.level = 0.0  # 0.0 to 1.0
        self.smoothed_level = 0.0
        self.phase = 0.0
        self.is_light = False

        self.set_draw_func(self._draw)

        # 30 FPS pulse & morph animation
        self._anim_tag = GLib.timeout_add(33, self._on_animate)

    def set_theme(self, is_light: bool):
        self.is_light = bool(is_light)
        self.queue_draw()

    def set_state(self, state: str):
        self.state = state
        self.queue_draw()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, level))

    def _on_animate(self):
        if not self.get_mapped():
            return True

        # Phase advance varies smoothly by state activity
        speed_delta = 0.05
        if self.state == "thinking":
            speed_delta = 0.09
        elif self.state == "user_speaking":
            speed_delta = 0.06 + 0.05 * self.smoothed_level
        elif self.state == "assistant_speaking":
            speed_delta = 0.07
        elif self.state == "muted":
            speed_delta = 0.02

        self.phase += speed_delta
        if self.phase > 2 * math.pi * 100:
            self.phase -= 2 * math.pi * 100

        # Smooth audio level transition
        target = self.level
        self.smoothed_level = self.smoothed_level * 0.72 + target * 0.28

        self.queue_draw()
        return True

    @staticmethod
    def _create_organic_blob_path(cr, center_x, center_y, base_r, harmonics, phase_val, n_pts=48):
        """
        Creates a closed, perfectly smooth C1 spline path with harmonic deformations.
        Uses quadratic-to-cubic Bezier midpoint interpolation to eliminate all sharp vertices.
        """
        pts = []
        for i in range(n_pts):
            angle = (2.0 * math.pi * i) / n_pts
            r = base_r
            for freq, amp, speed, offset in harmonics:
                r += amp * math.sin(freq * angle + phase_val * speed + offset)
            pts.append((center_x + r * math.cos(angle), center_y + r * math.sin(angle)))

        cr.new_path()
        p_last = pts[-1]
        p_first = pts[0]
        mid_prev = ((p_last[0] + p_first[0]) / 2.0, (p_last[1] + p_first[1]) / 2.0)
        cr.move_to(mid_prev[0], mid_prev[1])

        for i in range(n_pts):
            p_curr = pts[i]
            p_next = pts[(i + 1) % n_pts]
            mid_curr = ((p_curr[0] + p_next[0]) / 2.0, (p_curr[1] + p_next[1]) / 2.0)
            cp1_x = mid_prev[0] + (2.0 / 3.0) * (p_curr[0] - mid_prev[0])
            cp1_y = mid_prev[1] + (2.0 / 3.0) * (p_curr[1] - mid_prev[1])
            cp2_x = mid_curr[0] + (2.0 / 3.0) * (p_curr[0] - mid_curr[0])
            cp2_y = mid_curr[1] + (2.0 / 3.0) * (p_curr[1] - mid_curr[1])
            cr.curve_to(cp1_x, cp1_y, cp2_x, cp2_y, mid_curr[0], mid_curr[1])
            mid_prev = mid_curr
        cr.close_path()

    def _draw(self, _area, cr, width, height):
        cx = width / 2.0
        cy = height / 2.0
        base_radius = min(width, height) * 0.26

        # Color schemes & morph dynamics by state (R, G, B)
        if self.state == "listening":
            # Serene Emerald / Soft Cyan diffuse glow
            color_core = (0.20, 0.86, 0.72)
            color_glow = (0.08, 0.62, 0.54)
            color_highlight = (0.70, 1.00, 0.92)
            morph_energy = 0.22
            speed_mult = 1.0
        elif self.state == "user_speaking":
            # Vibrant Electric Cyan / Aquamarine reactive fluid
            color_core = (0.16, 0.92, 0.88)
            color_glow = (0.06, 0.70, 0.65)
            color_highlight = (0.82, 1.00, 0.98)
            morph_energy = 0.40 + 1.35 * self.smoothed_level
            speed_mult = 1.5
        elif self.state == "thinking":
            # Swirling Warm Amber / Molten Gold
            color_core = (0.98, 0.72, 0.18)
            color_glow = (0.85, 0.48, 0.10)
            color_highlight = (1.00, 0.94, 0.68)
            morph_energy = 0.65
            speed_mult = 2.0
        elif self.state == "assistant_speaking":
            # Radiant Ultraviolet / Neon Purple / Magenta
            color_core = (0.68, 0.42, 0.98)
            color_glow = (0.45, 0.22, 0.85)
            color_highlight = (0.92, 0.78, 1.00)
            morph_energy = 0.70 + 0.40 * math.sin(self.phase * 2.2)
            speed_mult = 1.7
        elif self.state == "muted":
            # Muted Twilight Slate / Soft Rose tint
            color_core = (0.55, 0.28, 0.32)
            color_glow = (0.38, 0.18, 0.22)
            color_highlight = (0.75, 0.50, 0.55)
            morph_energy = 0.08
            speed_mult = 0.5
        else:
            color_core = (0.35, 0.40, 0.55)
            color_glow = (0.25, 0.30, 0.45)
            color_highlight = (0.60, 0.65, 0.80)
            morph_energy = 0.15
            speed_mult = 1.0

        p = self.phase * speed_mult

        # Dynamic breathing radius with audio expansion
        dyn_radius = base_radius * (1.0 + 0.06 * math.sin(p * 1.1) + 0.15 * self.smoothed_level)

        # -----------------------------------------------------------------
        # 1. Broad Diffuse Ambient Blur Aura (outer misty halo, zero borders)
        # -----------------------------------------------------------------
        for r_scale, alpha_val in [(1.85, 0.14), (1.50, 0.22), (1.25, 0.35)]:
            cr.save()
            self._create_organic_blob_path(cr, cx, cy, dyn_radius * r_scale, [
                (2, 6.0 * morph_energy, 0.8, 0.0),
                (3, 4.0 * morph_energy, -0.6, 1.4),
            ], p * 0.7)
            pat = cairo.RadialGradient(cx, cy, dyn_radius * 0.15, cx, cy, dyn_radius * r_scale)
            pat.add_color_stop_rgba(0.0, color_glow[0], color_glow[1], color_glow[2], alpha_val)
            pat.add_color_stop_rgba(0.40, color_glow[0], color_glow[1], color_glow[2], alpha_val * 0.6)
            pat.add_color_stop_rgba(0.70, color_glow[0], color_glow[1], color_glow[2], alpha_val * 0.2)
            pat.add_color_stop_rgba(1.0, color_glow[0], color_glow[1], color_glow[2], 0.0)
            cr.set_source(pat)
            cr.fill()
            cr.restore()

        blend_op = cairo.Operator.OVER if self.is_light else cairo.Operator.SCREEN

        # -----------------------------------------------------------------
        # 2. Layer A: Primary Morphing Fluid Body (undulating shape)
        # -----------------------------------------------------------------
        cr.save()
        cr.set_operator(blend_op)
        off_ax = 3.5 * math.cos(p * 0.9)
        off_ay = 3.5 * math.sin(p * 0.8)
        self._create_organic_blob_path(cr, cx + off_ax, cy + off_ay, dyn_radius * 1.05, [
            (2, (8.0 + 9.0 * morph_energy), 1.1, 0.0),
            (3, (6.0 + 7.0 * morph_energy), -1.3, 2.0),
            (4, (3.0 + 5.0 * morph_energy), 1.7, 0.8),
        ], p)
        pat_a = cairo.RadialGradient(cx + off_ax, cy + off_ay, 0.0, cx + off_ax, cy + off_ay, dyn_radius * 1.15)
        pat_a.add_color_stop_rgba(0.0, color_core[0], color_core[1], color_core[2], 0.75)
        pat_a.add_color_stop_rgba(0.35, color_core[0], color_core[1], color_core[2], 0.55)
        pat_a.add_color_stop_rgba(0.65, color_glow[0], color_glow[1], color_glow[2], 0.25)
        pat_a.add_color_stop_rgba(0.85, color_glow[0], color_glow[1], color_glow[2], 0.08)
        pat_a.add_color_stop_rgba(1.0, color_glow[0], color_glow[1], color_glow[2], 0.0)
        cr.set_source(pat_a)
        cr.fill()
        cr.restore()

        # -----------------------------------------------------------------
        # 3. Layer B: Counter-Rotating Secondary Fluid Body (harmonic morph)
        # -----------------------------------------------------------------
        cr.save()
        cr.set_operator(blend_op)
        off_bx = -3.0 * math.sin(p * 1.1)
        off_by = 3.0 * math.cos(p * 1.0)
        self._create_organic_blob_path(cr, cx + off_bx, cy + off_by, dyn_radius * 0.95, [
            (3, (7.0 + 8.0 * morph_energy), 0.9, 1.2),
            (5, (4.0 + 6.0 * morph_energy), -1.5, 3.1),
            (2, (5.0 + 6.0 * morph_energy), 1.2, 2.4),
        ], p * 1.15)
        pat_b = cairo.RadialGradient(cx + off_bx, cy + off_by, 0.0, cx + off_bx, cy + off_by, dyn_radius * 1.10)
        pat_b.add_color_stop_rgba(0.0, color_core[0] * 1.08, color_core[1] * 1.08, color_core[2] * 1.08, 0.65)
        pat_b.add_color_stop_rgba(0.40, color_core[0], color_core[1], color_core[2], 0.40)
        pat_b.add_color_stop_rgba(0.70, color_glow[0], color_glow[1], color_glow[2], 0.15)
        pat_b.add_color_stop_rgba(1.0, color_glow[0], color_glow[1], color_glow[2], 0.0)
        cr.set_source(pat_b)
        cr.fill()
        cr.restore()

        # -----------------------------------------------------------------
        # 4. Layer C: Soft Luminous Glowing Core (diffuse blur highlight)
        # -----------------------------------------------------------------
        cr.save()
        cr.set_operator(blend_op)
        core_r = dyn_radius * 0.58
        off_cx = 2.0 * math.cos(p * 1.4)
        off_cy = 2.0 * math.sin(p * 1.3)
        self._create_organic_blob_path(cr, cx + off_cx, cy + off_cy, core_r, [
            (2, 3.5 * morph_energy, 1.6, 0.5),
            (3, 2.5 * morph_energy, -1.8, 1.7),
        ], p * 1.4)
        pat_core = cairo.RadialGradient(cx + off_cx, cy + off_cy, 0.0, cx + off_cx, cy + off_cy, core_r * 1.25)
        pat_core.add_color_stop_rgba(0.0, color_highlight[0], color_highlight[1], color_highlight[2], 0.85)
        pat_core.add_color_stop_rgba(0.25, color_core[0], color_core[1], color_core[2], 0.60)
        pat_core.add_color_stop_rgba(0.55, color_core[0], color_core[1], color_core[2], 0.28)
        pat_core.add_color_stop_rgba(0.85, color_glow[0], color_glow[1], color_glow[2], 0.06)
        pat_core.add_color_stop_rgba(1.0, color_glow[0], color_glow[1], color_glow[2], 0.0)
        cr.set_source(pat_core)
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

    def set_theme(self, is_light: bool):
        self.orb.set_theme(is_light)
