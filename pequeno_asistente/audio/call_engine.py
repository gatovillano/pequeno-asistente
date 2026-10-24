"""
CallEngine: Real-time Voice-to-Voice loop for Pequeño Asistente.
Continuous hands-free conversation with real-time Voice Activity Detection (VAD),
sentence-pipelined TTS playback, and acoustic echo isolation.
"""
import os
import time
import wave
import signal
import shutil
import logging
import tempfile
import threading
import subprocess
import asyncio
import numpy as np
from typing import Optional, Callable, Dict, Any, List

from pequeno_asistente.audio.transcriber import AudioTranscriber
from pequeno_asistente.audio.tts import UnifiedTTS
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks

logger = logging.getLogger("pequeno_asistente.audio.call")


class CallEngine:
    """
    Manages a continuous hands-free voice-to-voice telephone call session.
    Detects when user starts and finishes speaking, transcribes audio,
    queries the agent, and streams back synthesized speech.
    """

    def __init__(
        self,
        agent: AgentCore,
        transcriber: AudioTranscriber,
        tts: UnifiedTTS,
        async_loop: asyncio.AbstractEventLoop,
        on_state_changed: Optional[Callable[[str, str], None]] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        on_user_transcript: Optional[Callable[[str], None]] = None,
        on_assistant_token: Optional[Callable[[str], None]] = None,
        on_turn_finished: Optional[Callable[[str, str], None]] = None,
        on_call_ended: Optional[Callable[[], None]] = None,
    ):
        self.agent = agent
        self.transcriber = transcriber
        self.tts = tts
        self.async_loop = async_loop

        self.on_state_changed = on_state_changed or (lambda s, d: None)
        self.on_audio_level = on_audio_level or (lambda lvl: None)
        self.on_user_transcript = on_user_transcript or (lambda t: None)
        self.on_assistant_token = on_assistant_token or (lambda t: None)
        self.on_turn_finished = on_turn_finished or (lambda u, a: None)
        self.on_call_ended = on_call_ended or (lambda: None)

        self.is_active = False
        self.is_muted = False
        self.state = "idle"  # "listening", "user_speaking", "thinking", "assistant_speaking", "muted"

        self._record_proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # VAD Parameters (16kHz 16-bit mono = 32000 bytes/sec)
        self.chunk_size = 1024  # 512 samples = 32ms per chunk
        self.sample_rate = 16000
        self.ambient_rms = 400.0
        self.silence_timeout_sec = 0.85
        self.min_speech_duration_sec = 0.40

    def start_call(self):
        """Starts the voice-to-voice call session."""
        with self._lock:
            if self.is_active:
                return
            self.is_active = True
            self._stop_event.clear()
            self._set_state("listening", "Escuchando...")

        self._thread = threading.Thread(target=self._call_loop, daemon=True)
        self._thread.start()
        logger.info("Voice-to-voice CallEngine started.")

    def end_call(self):
        """Ends the call session and releases all resources."""
        with self._lock:
            if not self.is_active:
                return
            self.is_active = False
            self._stop_event.set()

        # Stop recorder process
        if self._record_proc:
            try:
                self._record_proc.terminate()
                self._record_proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._record_proc.kill()
                except Exception:
                    pass
            self._record_proc = None

        # Stop any active TTS playback
        self.tts.stop()

        self._set_state("idle", "Llamada finalizada")
        self.on_call_ended()
        logger.info("Voice-to-voice CallEngine stopped.")

    def toggle_mute(self) -> bool:
        """Toggles microphone mute in call."""
        self.is_muted = not self.is_muted
        if self.is_muted:
            self._set_state("muted", "Micrófono silenciado")
        else:
            self._set_state("listening", "Escuchando...")
        return self.is_muted

    def interrupt_assistant(self):
        """Interrupts assistant speech immediately."""
        self.tts.stop()
        if self.state in ("assistant_speaking", "thinking"):
            self._set_state("listening", "Escuchando...")

    def _set_state(self, state: str, description: str):
        self.state = state
        self.on_state_changed(state, description)

    def _find_stream_recorder(self) -> Optional[List[str]]:
        """Detects CLI command for streaming raw 16kHz s16le PCM to stdout."""
        if shutil.which("pw-record"):
            return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        if shutil.which("ffmpeg"):
            return ["ffmpeg", "-loglevel", "error", "-f", "pulse", "-i", "default", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"]
        if shutil.which("arecord"):
            return ["arecord", "-q", "-r", "16000", "-c", "1", "-f", "S16_LE", "-"]
        return None

    def _call_loop(self):
        """Main continuous audio acquisition and VAD loop."""
        cmd = self._find_stream_recorder()
        if not cmd:
            logger.error("No suitable recorder CLI tool found for call streaming.")
            self.end_call()
            return

        try:
            self._record_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.chunk_size * 4
            )
        except Exception as e:
            logger.error(f"Failed to start call recorder process: {e}")
            self.end_call()
            return

        pre_speech_ring: List[bytes] = []
        ring_max_len = 10  # ~0.3s of pre-speech buffer
        speech_chunks: List[bytes] = []
        silence_chunks_count = 0
        speech_started_time = 0.0

        while not self._stop_event.is_set():
            if not self._record_proc or not self._record_proc.stdout:
                break

            try:
                raw_chunk = self._record_proc.stdout.read(self.chunk_size)
            except Exception:
                break

            if not raw_chunk:
                break

            if len(raw_chunk) < self.chunk_size:
                continue

            # Compute RMS energy
            pcm_data = np.frombuffer(raw_chunk, dtype=np.int16)
            rms = float(np.sqrt(np.mean(pcm_data.astype(np.float32) ** 2)))

            # If muted or in non-listening state (thinking or assistant speaking):
            # We drain chunks and avoid microphone acoustic loopback from interrupting assistant speech.
            if self.is_muted:
                time.sleep(0.01)
                continue

            if self.state in ("thinking", "assistant_speaking"):
                pre_speech_ring.clear()
                speech_chunks.clear()
                # If assistant is speaking, show pulsing activity on visualizer
                if self.state == "assistant_speaking":
                    self.on_audio_level(min(1.0, rms / 2000.0))
                time.sleep(0.01)
                continue

            # Update audio level for visualizer during listening / user speaking
            level = min(1.0, max(0.0, (rms - 120.0) / 2200.0))
            self.on_audio_level(level)

            # Dynamic adaptive threshold calibration
            self.ambient_rms = self.ambient_rms * 0.95 + min(rms, 450.0) * 0.05
            threshold = max(420.0, self.ambient_rms * 2.6)

            # --- State 1: LISTENING ---
            if self.state == "listening":
                if rms > threshold:
                    self._set_state("user_speaking", "Te escucho...")
                    speech_started_time = time.time()
                    speech_chunks = list(pre_speech_ring)
                    speech_chunks.append(raw_chunk)
                    silence_chunks_count = 0
                else:
                    pre_speech_ring.append(raw_chunk)
                    if len(pre_speech_ring) > ring_max_len:
                        pre_speech_ring.pop(0)

            # --- State 2: USER IS SPEAKING ---
            elif self.state == "user_speaking":
                speech_chunks.append(raw_chunk)

                if rms < threshold:
                    silence_chunks_count += 1
                    silence_sec = silence_chunks_count * (self.chunk_size / (self.sample_rate * 2))

                    if silence_sec >= self.silence_timeout_sec:
                        total_speech_sec = time.time() - speech_started_time
                        if total_speech_sec >= self.min_speech_duration_sec:
                            # User turn finished!
                            self._set_state("thinking", "Pensando...")
                            self._handle_user_utterance(list(speech_chunks))
                        else:
                            # Too short to be meaningful speech (accidental noise)
                            self._set_state("listening", "Escuchando...")

                        speech_chunks.clear()
                        silence_chunks_count = 0
                else:
                    silence_chunks_count = 0

        logger.info("Call loop exited cleanly.")

    def _handle_user_utterance(self, chunks: List[bytes]):
        """Transcribes accumulated PCM audio and triggers Agent + TTS generation."""
        def worker():
            # 1. Write PCM chunks to a temporary WAV file
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", prefix="call_utterance_", delete=False)
            tmp_path = tmp.name
            tmp.close()

            try:
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(b"".join(chunks))

                # 2. Transcribe speech
                user_text = self.transcriber.transcribe_file(tmp_path)
            except Exception as e:
                logger.error(f"Error in call transcription: {e}")
                user_text = ""
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

            if not user_text.strip() or self._stop_event.is_set():
                if not self._stop_event.is_set():
                    self._set_state("listening", "Escuchando...")
                return

            logger.info(f"[Call Mode User]: {user_text}")
            self.on_user_transcript(user_text)

            # 3. Stream from AgentCore and speak response
            self._stream_agent_and_speak(user_text)

        threading.Thread(target=worker, daemon=True).start()

    def _stream_agent_and_speak(self, user_text: str):
        """Queries the LLM and plays speech with Unified TTS."""
        assistant_full_text = []

        def on_token(token: str):
            if self._stop_event.is_set():
                return
            assistant_full_text.append(token)
            self.on_assistant_token(token)

        callbacks = AgentCallbacks(
            on_token=on_token,
            on_complete=lambda _final: logger.info("Call turn LLM stream finished."),
            on_error=lambda err: logger.error(f"Agent turn error in call: {err}")
        )

        # Run agent turn on the background asyncio loop
        future = asyncio.run_coroutine_threadsafe(
            self.agent.run_turn(user_text, callbacks),
            self.async_loop
        )

        try:
            future.result(timeout=25.0)
        except Exception as e:
            logger.error(f"Error waiting for agent response in call: {e}")

        full_reply = "".join(assistant_full_text).strip()
        if not full_reply or self._stop_event.is_set():
            if not self._stop_event.is_set():
                self._set_state("listening", "Escuchando...")
            return

        self._set_state("assistant_speaking", "Hablando...")

        # Play full synthesized speech
        playback_done = threading.Event()

        def on_finish_speech():
            playback_done.set()

        self.tts.speak_async(
            full_reply,
            on_start=lambda: None,
            on_finish=on_finish_speech
        )

        # Wait until playback finishes or call is stopped/interrupted
        while not playback_done.is_set() and not self._stop_event.is_set() and self.state == "assistant_speaking":
            playback_done.wait(timeout=0.1)

        # Post turn callback to save message to chat view & thread
        self.on_turn_finished(user_text, full_reply)

        # Brief cooldown period (0.4s) to avoid speaker audio reverberation into mic
        time.sleep(0.4)

        if not self._stop_event.is_set() and self.state != "user_speaking":
            self._set_state("listening", "Escuchando...")
