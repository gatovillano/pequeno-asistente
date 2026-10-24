"""
Text-to-Speech (TTS) Engine for Pequeño Asistente.
Supports Kokoro TTS (local Docker) and ElevenLabs (high-fidelity neural audio),
with automatic graceful fallback to Kokoro when ElevenLabs quota or plan is exhausted.
"""
import os
import re
import json
import time
import shutil
import signal
import logging
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger("pequeno_asistente.audio.tts")


def clean_markdown_for_speech(text: str) -> str:
    """Strips markdown formatting, code blocks, and URLs so speech sounds natural."""
    if not text:
        return ""

    # 1. Remove fenced code blocks completely (e.g. ```python ... ```)
    text = re.sub(r'```[\s\S]*?```', ' ', text)

    # 2. Remove inline code backticks
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # 3. Clean markdown links: [label](url) -> label
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # 4. Remove image tags: ![alt](url) -> empty
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', '', text)

    # 5. Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # 5.5 Clean markdown tables: remove separator rows, strip edge pipes, convert cell dividers to commas
    text = re.sub(r'^\s*\|?[:\-|\s]{3,}\|?\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\|\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*\|\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*\|\s*', ', ', text)

    # 6. Remove header markers and blockquotes
    text = re.sub(r'^[#*>\-+]+\s*', '', text, flags=re.MULTILINE)

    # 7. Remove bold/italics markers
    text = re.sub(r'[*_~]', '', text)

    # 8. Normalize newlines to sentence pauses
    text = re.sub(r'\n+', '. ', text)

    # 9. Normalize multiple punctuation and spacing
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'\s*,\s*\.\s*', '. ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def split_text_into_sentences(text: str, max_sentence_len: int = 250) -> List[str]:
    """Splits text into speakable sentence chunks for low-latency streaming TTS."""
    cleaned = clean_markdown_for_speech(text)
    if not cleaned:
        return []

    # Regex split on sentence terminators: . ? ! ¡ ¿ followed by space or end
    raw_parts = re.split(r'([.?!]+(?:\s+|$))', cleaned)
    sentences = []
    current = ""

    for part in raw_parts:
        if not part:
            continue
        current += part
        # If this part ends with punctuation and has a decent length, emit chunk
        if re.search(r'[.?!]+\s*$', current) and len(current.strip()) >= 15:
            sentences.append(current.strip())
            current = ""
        elif len(current) > max_sentence_len:
            # Fallback split if sentence is too long
            sentences.append(current.strip())
            current = ""

    if current.strip():
        sentences.append(current.strip())

    return [s for s in sentences if len(s.strip()) > 1]


def detect_audio_extension(audio_bytes: bytes) -> str:
    """Detects audio format extension from file headers (.mp3, .wav, or .ogg)."""
    if audio_bytes.startswith(b"RIFF"):
        return ".wav"
    if audio_bytes.startswith(b"ID3") or (len(audio_bytes) > 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
        return ".mp3"
    if audio_bytes.startswith(b"OggS"):
        return ".ogg"
    return ".wav"


class KokoroBackend:
    """Client for Kokoro / Unified Streaming TTS Docker container."""

    def __init__(
        self,
        url: str = "http://localhost:8011",
        voice: str = "es-MX-JorgeNeural",
        speed: float = 1.0,
        lang: str = "es",
        enabled: bool = True
    ):
        self.url = url.rstrip("/")
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.enabled = enabled

    def is_available(self, timeout: float = 1.2) -> bool:
        """Checks if the local Kokoro TTS service is responding."""
        try:
            req = urllib.request.Request(f"{self.url}/voices")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_voices(self, timeout: float = 2.0) -> List[str]:
        """Fetches available voices from Kokoro TTS container."""
        try:
            req = urllib.request.Request(f"{self.url}/voices")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("voices", [])
        except Exception as e:
            logger.warning(f"Could not fetch voices from Kokoro TTS: {e}")
            return ["es-MX-JorgeNeural", "ef_dora", "em_alex", "es-MX-DaliaNeural", "es-ES-AlvaroNeural", "af_heart", "am_adam"]

    def synthesize(self, text: str, timeout: float = 25.0) -> Optional[bytes]:
        """Synthesizes text into audio bytes via POST /tts."""
        payload = json.dumps({
            "text": text,
            "voice": self.voice,
            "speed": self.speed,
            "lang": self.lang
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.url}/tts",
            data=payload,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read()
                logger.error(f"Kokoro TTS returned status {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Error calling Kokoro TTS: {e}")
            return None


class ElevenLabsBackend:
    """Client for ElevenLabs Text-to-Speech API."""

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "ErXwobaYiN019PkySvjV",  # Antoni (Multilingual)
        model_id: str = "eleven_multilingual_v2",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        enabled: bool = True
    ):
        self.api_key = api_key.strip()
        self.voice_id = voice_id.strip() if voice_id.strip() else "ErXwobaYiN019PkySvjV"
        self.model_id = model_id.strip() if model_id.strip() else "eleven_multilingual_v2"
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.enabled = enabled

    def is_available(self, timeout: float = 2.0) -> bool:
        """Verifies if the API Key is valid and has active access."""
        if not self.api_key:
            return False
        try:
            req = urllib.request.Request(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": self.api_key}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def synthesize(self, text: str, timeout: float = 25.0) -> Optional[bytes]:
        """Synthesizes text into MP3 audio bytes via ElevenLabs API."""
        if not self.api_key or not self.enabled:
            return None

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        payload = json.dumps({
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost
            }
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg"
            }
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
            logger.error(f"ElevenLabs returned status {resp.status}")
            return None


class UnifiedTTS:
    """
    Unified Text-To-Speech engine supporting Kokoro TTS and ElevenLabs.
    Automatically and seamlessly falls back to Kokoro (e.g. Jorge voice) when ElevenLabs
    quota or subscription limit is reached.
    """

    def __init__(
        self,
        provider: str = "kokoro",
        kokoro_url: str = "http://localhost:8011",
        kokoro_voice: str = "es-MX-JorgeNeural",
        kokoro_speed: float = 1.0,
        kokoro_lang: str = "es",
        elevenlabs_api_key: str = "",
        elevenlabs_voice_id: str = "ErXwobaYiN019PkySvjV",
        elevenlabs_model_id: str = "eleven_multilingual_v2",
        elevenlabs_stability: float = 0.5,
        elevenlabs_similarity_boost: float = 0.75,
        fallback_to_kokoro: bool = True,
        enabled: bool = True,
        # Backward compatibility parameters
        url: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        lang: Optional[str] = None,
    ):
        if url:
            kokoro_url = url
        if voice:
            kokoro_voice = voice
        if speed is not None:
            kokoro_speed = speed
        if lang:
            kokoro_lang = lang

        self.provider = provider  # "kokoro" or "elevenlabs"
        self.fallback_to_kokoro = fallback_to_kokoro
        self.enabled = enabled

        self.last_used_provider: str = provider
        self.last_fallback_occurred: bool = False

        self.kokoro = KokoroBackend(
            url=kokoro_url,
            voice=kokoro_voice,
            speed=kokoro_speed,
            lang=kokoro_lang,
            enabled=enabled
        )

        self.elevenlabs = ElevenLabsBackend(
            api_key=elevenlabs_api_key,
            voice_id=elevenlabs_voice_id,
            model_id=elevenlabs_model_id,
            stability=elevenlabs_stability,
            similarity_boost=elevenlabs_similarity_boost,
            enabled=enabled
        )

        self._is_speaking = False
        self._cancel_flag = False
        self._current_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    # Properties for backward compatibility with existing code
    @property
    def url(self) -> str:
        return self.kokoro.url

    @url.setter
    def url(self, value: str):
        self.kokoro.url = value

    @property
    def voice(self) -> str:
        return self.kokoro.voice

    @voice.setter
    def voice(self, value: str):
        self.kokoro.voice = value

    @property
    def speed(self) -> float:
        return self.kokoro.speed

    @speed.setter
    def speed(self, value: float):
        self.kokoro.speed = value

    @property
    def lang(self) -> str:
        return self.kokoro.lang

    @lang.setter
    def lang(self, value: str):
        self.kokoro.lang = value

    def is_available(self, timeout: float = 1.2) -> bool:
        """Checks availability of current active provider."""
        if self.provider == "elevenlabs":
            if self.elevenlabs.is_available(timeout=timeout):
                return True
            # If ElevenLabs is unavailable but fallback is active, check Kokoro
            if self.fallback_to_kokoro:
                return self.kokoro.is_available(timeout=timeout)
            return False
        return self.kokoro.is_available(timeout=timeout)

    def get_voices(self, timeout: float = 2.0) -> List[str]:
        return self.kokoro.get_voices(timeout=timeout)

    def synthesize(self, text: str, timeout: float = 25.0) -> Optional[bytes]:
        """
        Synthesizes text using active provider.
        Automatically falls back to Kokoro if ElevenLabs fails due to quota or errors.
        """
        if not self.enabled:
            return None

        if self.provider == "elevenlabs":
            # 1. If no API key configured, fallback directly
            if not self.elevenlabs.api_key:
                if self.fallback_to_kokoro:
                    logger.info("ElevenLabs API Key is not set; falling back to Kokoro TTS.")
                    self.last_used_provider = "kokoro"
                    self.last_fallback_occurred = True
                    return self.kokoro.synthesize(text, timeout=timeout)
                return None

            # 2. Attempt ElevenLabs synthesis
            try:
                audio = self.elevenlabs.synthesize(text, timeout=timeout)
                if audio:
                    self.last_used_provider = "elevenlabs"
                    self.last_fallback_occurred = False
                    return audio
            except urllib.error.HTTPError as e:
                # Quota exceeded (401 quota_exceeded, 429 rate limit, 402 payment required, 400 credit limit)
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8")
                except Exception:
                    pass

                self.last_error_message = f"ElevenLabs HTTP {e.code}: {error_body}"
                if e.code == 402 and ("library_voice" in error_body or "paid_plan_required" in error_body):
                    self.last_error_reason = "ElevenLabs no permite voces de la biblioteca en cuentas gratuitas vía API (error 402). Para usar ElevenLabs gratis, selecciona voces estándar como George o Antoni."
                elif e.code == 401:
                    self.last_error_reason = "Error de autenticación o cuota agotada en ElevenLabs (error 401)."
                elif e.code == 429:
                    self.last_error_reason = "Límite de peticiones de ElevenLabs alcanzado (error 429)."
                else:
                    self.last_error_reason = f"Error en ElevenLabs ({e.code})."

                logger.warning(
                    f"{self.last_error_reason} "
                    f"{'Falling back to Kokoro TTS.' if self.fallback_to_kokoro else ''}"
                )
                if self.fallback_to_kokoro:
                    self.last_used_provider = "kokoro"
                    self.last_fallback_occurred = True
                    return self.kokoro.synthesize(text, timeout=timeout)
                return None
            except Exception as e:
                logger.warning(
                    f"Error in ElevenLabs TTS: {e}. "
                    f"{'Falling back to Kokoro TTS.' if self.fallback_to_kokoro else ''}"
                )
                if self.fallback_to_kokoro:
                    self.last_used_provider = "kokoro"
                    self.last_fallback_occurred = True
                    return self.kokoro.synthesize(text, timeout=timeout)
                return None

        # Standard Kokoro synthesis
        self.last_used_provider = "kokoro"
        self.last_fallback_occurred = False
        return self.kokoro.synthesize(text, timeout=timeout)

    def _find_player_command(self, audio_file: str) -> Optional[List[str]]:
        """Finds system player (pw-play, mpv, or ffplay)."""
        if shutil.which("pw-play"):
            return ["pw-play", audio_file]
        if shutil.which("mpv"):
            return ["mpv", "--no-video", "--really-quiet", audio_file]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_file]
        return None

    def stop(self):
        """Immediately stops ongoing speech playback."""
        with self._lock:
            self._cancel_flag = True
            if self._current_proc:
                try:
                    self._current_proc.terminate()
                    self._current_proc.wait(timeout=0.5)
                except Exception:
                    try:
                        self._current_proc.kill()
                    except Exception:
                        pass
                self._current_proc = None
            self._is_speaking = False

    def speak_async(
        self,
        text: str,
        on_start: Optional[Callable[[], None]] = None,
        on_finish: Optional[Callable[[], None]] = None
    ):
        """
        Synthesizes and plays text asynchronously in a background thread.
        Uses sentence pipelining for low-latency playback.
        """
        if not self.enabled:
            if on_finish:
                on_finish()
            return

        self.stop()

        sentences = split_text_into_sentences(text)
        if not sentences:
            if on_finish:
                on_finish()
            return

        def worker():
            with self._lock:
                self._is_speaking = True
                self._cancel_flag = False

            if on_start:
                try:
                    on_start()
                except Exception as e:
                    logger.error(f"Error in on_start callback: {e}")

            logger.info(
                f"Unified TTS speaking {len(sentences)} sentence(s) "
                f"[Provider: {self.provider}, Kokoro Voice: {self.kokoro.voice}, ElevenLabs Voice: {self.elevenlabs.voice_id}]..."
            )

            temp_files: List[str] = []
            try:
                for sent in sentences:
                    if self._cancel_flag:
                        break

                    audio_bytes = self.synthesize(sent)
                    if not audio_bytes or self._cancel_flag:
                        continue

                    # Auto-detect audio format extension (.mp3 or .wav)
                    ext = detect_audio_extension(audio_bytes)
                    tmp = tempfile.NamedTemporaryFile(suffix=ext, prefix="tts_speech_", delete=False)
                    tmp.write(audio_bytes)
                    tmp_name = tmp.name
                    tmp.close()
                    temp_files.append(tmp_name)

                    if self._cancel_flag:
                        break

                    cmd = self._find_player_command(tmp_name)
                    if not cmd:
                        logger.error("No audio player found for TTS (pw-play, mpv, or ffplay).")
                        break

                    with self._lock:
                        if self._cancel_flag:
                            break
                        self._current_proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )

                    # Wait for sentence playback to finish
                    self._current_proc.wait()
                    with self._lock:
                        self._current_proc = None

            except Exception as e:
                logger.error(f"Error in TTS playback worker: {e}")
            finally:
                # Clean up temporary audio files
                for tf in temp_files:
                    if os.path.exists(tf):
                        try:
                            os.unlink(tf)
                        except Exception:
                            pass

                with self._lock:
                    self._is_speaking = False
                    self._current_proc = None

                if on_finish:
                    try:
                        on_finish()
                    except Exception as e:
                        logger.error(f"Error in on_finish callback: {e}")

        t = threading.Thread(target=worker, daemon=True)
        t.start()


# Backwards compatibility alias: KokoroTTS points to UnifiedTTS
KokoroTTS = UnifiedTTS
