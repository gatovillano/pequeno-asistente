"""
Strict offline wake-word detector using Vosk (full Spanish vocabulary + word confidence)
and system audio capture (ffmpeg / arecord / pw-record).
"""
import array
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.request
import wave
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("pequeno_asistente.wake_word")

DEFAULT_MODEL_NAME = "vosk-model-small-es-0.42"
DEFAULT_MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL_NAME}.zip"
DEFAULT_MODELS_DIR = Path.home() / ".config" / "pequeno-asistente" / "models"


def find_raw_pcm_recorder_command(sample_rate: int = 16000) -> Optional[List[str]]:
    """Returns a command that streams 16-bit signed little-endian mono PCM to stdout."""
    if shutil.which("ffmpeg"):
        return [
            "ffmpeg", "-loglevel", "error",
            "-f", "pulse", "-i", "default",
            "-ar", str(sample_rate), "-ac", "1",
            "-f", "s16le", "-"
        ]
    if shutil.which("arecord"):
        return [
            "arecord", "-q",
            "-r", str(sample_rate), "-c", "1",
            "-f", "S16_LE", "-t", "raw", "-"
        ]
    if shutil.which("pw-record"):
        return [
            "pw-record", "--format=s16",
            f"--rate={sample_rate}", "--channels=1", "-"
        ]
    return None


def parse_wake_phrases(phrase_text: str) -> List[str]:
    """Parses comma-separated wake phrases and adds common 'ok'/'okay' alias."""
    raw_items = [p.strip().lower() for p in (phrase_text or "").split(",") if p.strip()]
    if not raw_items:
        raw_items = ["okay asistente"]

    phrases: List[str] = []
    for item in raw_items:
        if item not in phrases:
            phrases.append(item)
        if item.startswith("okay ") or item.startswith("ok "):
            suffix = item.split(" ", 1)[1]
            for prefix in ("okay ", "ok ", "okey "):
                alias = prefix + suffix
                if alias not in phrases:
                    phrases.append(alias)
    return phrases


def _normalize_ok_variants(text: str) -> str:
    """Normalizes common Spanish/English ASR variants of 'ok'/'okay' to 'ok'."""
    t = re.sub(r"\bo\s*\.?\s*k\s*\.?(?=\s|$|[,.:;!?])", "ok", text, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:oquei|ogei|o\s+gei|o\s+key|o\s+que|o\s+qué)\b", "ok", t, flags=re.IGNORECASE)
    t = re.sub(r"\bok\s+ya\s+existente\b", "ok asistente", t, flags=re.IGNORECASE)
    return t


def strip_wake_prefix(text: str, phrases: List[str]) -> str:
    """Removes a leading wake-word phrase and punctuation from a free-form transcript."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    # Normalize dotted/spaced O.K. variants at the start before matching
    normalized_head = re.sub(
        r"^\s*(?:o\s*\.\s*k\s*\.?|o\s+k|oquei|ogei|o\s+gei|o\s+key|o\s+qu[eé])\b",
        "ok",
        cleaned,
        flags=re.IGNORECASE,
    )
    normalized_head = re.sub(
        r"^\s*ok\s+ya\s+existente\b",
        "ok asistente",
        normalized_head,
        flags=re.IGNORECASE,
    )
    lowered = normalized_head.lower()
    for p in sorted(phrases, key=len, reverse=True):
        pattern = r"^\s*" + r"[\s,.:;!?]*".join(re.escape(w) for w in p.split()) + r"[\s,.:;!?-]*"
        m = re.match(pattern, lowered)
        if m:
            cleaned = normalized_head[m.end():].strip()
            return cleaned
    # Also strip orphaned trailing wake word (e.g. 'asistente ...' if 'ok' was clipped)
    for p in phrases:
        parts = p.split()
        if len(parts) >= 2:
            tail_word = parts[-1]
            m = re.match(r"^\s*" + re.escape(tail_word) + r"\b[\s,.:;!?-]*", lowered)
            if m:
                return normalized_head[m.end():].strip()
    return cleaned


def normalize_command_text(text: str) -> str:
    """Normalizes ASR command text (lowercase, no accents/punctuation, no leading 'o '/'y ')."""
    raw = (text or "").lower().strip()
    no_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(ch)
    )
    cleaned = re.sub(r"[.,!?¡¿:;\"'()\-]+", " ", no_accents)
    norm = " ".join(cleaned.split())
    norm = _normalize_ok_variants(norm)
    for filler in ("o ", "y ", "a ", "eh "):
        if norm.startswith(filler) and len(norm[len(filler):].split()) >= 2:
            norm = norm[len(filler):].strip()
            break
    return norm


def _pcm_rms(chunk: bytes) -> float:
    """Computes RMS energy of 16-bit signed little-endian PCM bytes."""
    if len(chunk) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return math.sqrt(total / len(samples))


def _write_pcm_wav(chunks: List[bytes], sample_rate: int = 16000) -> str:
    """Writes raw 16-bit mono PCM chunks to a temporary WAV file and returns its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", prefix="asistente_cmd_", delete=False)
    wav_path = tmp.name
    tmp.close()
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(chunks))
    return wav_path


def ensure_vosk_model(model_dir: Optional[Path] = None) -> Optional[Path]:
    """Ensures the small Spanish Vosk model exists locally, downloading it if needed."""
    target_dir = model_dir or (DEFAULT_MODELS_DIR / DEFAULT_MODEL_NAME)
    if target_dir.exists() and (target_dir / "conf").exists():
        return target_dir

    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    zip_path = parent / f"{DEFAULT_MODEL_NAME}.zip"

    try:
        logger.info(f"Downloading Vosk wake-word model from {DEFAULT_MODEL_URL}...")
        urllib.request.urlretrieve(DEFAULT_MODEL_URL, str(zip_path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(parent)
        logger.info(f"Vosk model ready at {target_dir}")
        return target_dir if target_dir.exists() else None
    except Exception as e:
        logger.error(f"Failed to download/extract Vosk model: {e}")
        return None
    finally:
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass


DEFAULT_DISTRACTORS = [
    "hola", "bueno", "entonces", "mira", "oye", "como", "pero", "este", "esto",
    "vale", "claro", "gracias", "así", "ahora", "aquí", "sí", "no", "yo", "tú",
    "el", "la", "los", "las", "un", "una", "que", "de", "en", "por", "para",
    "con", "sin", "más", "muy", "bien", "mal", "todo", "nada", "algo", "hacer",
    "tener", "poder", "decir", "ver", "ir", "estar", "ser", "haber", "quiero",
    "creo", "puedo", "tengo", "vamos", "mientras", "porque", "cuando", "donde",
]


class WakeWordDetector:
    """
    Strict background wake-word detector powered by full-vocabulary Vosk ASR
    with word-confidence filtering, follow-up commands, and free-form dispatch.
    """

    def __init__(
        self,
        on_detected: Callable[[], None],
        phrase: str = "okay asistente",
        commands: Optional[Dict[str, Callable[[], None]]] = None,
        on_freeform_audio: Optional[Callable[[str], None]] = None,
        on_freeform_text: Optional[Callable[[str], None]] = None,
        on_listening_ended: Optional[Callable[[], None]] = None,
        verify_wake_audio: Optional[Callable[[str], Any]] = None,
        verify_on_partial: bool = True,
        command_window_seconds: float = 6.0,
        should_pause: Optional[Callable[[], bool]] = None,
        model_dir: Optional[Path] = None,
        sample_rate: int = 16000,
        cooldown_seconds: float = 2.5,
        speech_rms_threshold: float = 300.0,
        min_word_confidence: float = 0.65,
        partial_streak_required: int = 3,
        acoustic_matcher: Optional[Any] = None,
        use_acoustic: bool = True,
    ):
        self.on_detected = on_detected
        self.phrase = phrase
        self.phrases = parse_wake_phrases(phrase)
        self.commands: Dict[str, Callable[[], None]] = {
            " ".join(k.lower().strip().split()): v
            for k, v in (commands or {}).items()
            if k.strip()
        }
        self.on_freeform_audio = on_freeform_audio
        self.on_freeform_text = on_freeform_text
        self.on_listening_ended = on_listening_ended
        self.verify_wake_audio = verify_wake_audio
        self.verify_on_partial = verify_on_partial
        self.command_window_seconds = command_window_seconds
        self.should_pause = should_pause
        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self.cooldown_seconds = cooldown_seconds
        self.speech_rms_threshold = speech_rms_threshold
        self.min_word_confidence = min_word_confidence
        self.partial_streak_required = partial_streak_required
        self.use_acoustic = use_acoustic
        self.acoustic_matcher = acoustic_matcher
        if self.acoustic_matcher is None and self.use_acoustic:
            try:
                from pequeno_asistente.audio.acoustic_wake import AcousticWakeMatcher
                self.acoustic_matcher = AcousticWakeMatcher(sample_rate=sample_rate)
            except Exception as e:
                logger.warning(f"Acoustic wake matcher unavailable: {e}")
                self.acoustic_matcher = None

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._last_trigger_time = 0.0
        self._last_verify_time = 0.0
        self._command_window_until = 0.0
        self._wake_triggered_in_utterance = False
        self._partial_wake_streak = 0
        self._utterance_chunks: List[bytes] = []
        self._recent_chunks: List[bytes] = []
        self._capturing_freeform = False
        self._freeform_chunks: List[bytes] = []
        self._freeform_has_speech = False
        self._freeform_silence_chunks = 0
        self._freeform_grace_chunks = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def update_phrase(self, phrase: str):
        """Updates the wake phrase and restarts the listener if running."""
        new_phrases = parse_wake_phrases(phrase)
        if phrase == self.phrase and new_phrases == self.phrases:
            return
        self.phrase = phrase
        self.phrases = new_phrases
        if self._running:
            self.stop()
            self.start()

    def start(self) -> bool:
        """Starts background wake-word listening."""
        with self._lock:
            if self._running:
                return True
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._listen_loop,
                name="wake-word-detector",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self):
        """Stops background wake-word listening and terminates the capture process."""
        with self._lock:
            self._running = False
            self._stop_event.set()
            self._terminate_process()
            thread = self._thread
            self._thread = None

        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _terminate_process(self):
        proc = self._process
        self._process = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _build_grammar(self) -> str:
        """Builds Vosk constrained grammar JSON with wake phrases, commands, and Spanish distractors."""
        terms: List[str] = []
        for item in self.phrases + list(self.commands.keys()) + DEFAULT_DISTRACTORS:
            if item not in terms:
                terms.append(item)
        terms.append("[unk]")
        return json.dumps(terms, ensure_ascii=False)

    def _matched_wake_phrase(self, text: str) -> Optional[str]:
        """Returns the matched wake phrase using whole-word boundaries, or None."""
        norm = " ".join((text or "").lower().strip().split())
        if not norm:
            return None
        norm = _normalize_ok_variants(norm)
        for p in sorted(self.phrases, key=len, reverse=True):
            if re.search(r"(?:^|\s)" + re.escape(p) + r"(?:\s|$)", norm):
                return p
        return None

    def _match_text(self, text: str) -> bool:
        """Returns True if any configured wake phrase appears as whole words."""
        return self._matched_wake_phrase(text) is not None

    def _check_word_confidence(self, words: Optional[List[Dict[str, Any]]], phrase: str) -> bool:
        """Verifies Vosk per-word confidence for the matched wake phrase."""
        if not words:
            return True
        target_words = phrase.lower().split()
        if not target_words:
            return False
        word_list = [(w.get("word", "").lower(), float(w.get("conf", 1.0))) for w in words]
        n = len(target_words)
        for i in range(len(word_list) - n + 1):
            if [word_list[i + j][0] for j in range(n)] == target_words:
                confs = [word_list[i + j][1] for j in range(n)]
                avg_conf = sum(confs) / len(confs)
                return avg_conf >= self.min_word_confidence and min(confs) >= 0.35
        return False

    def _match_command(self, text: str) -> Optional[str]:
        """Returns the matching command phrase if normalized text is an exact command match."""
        norm = normalize_command_text(text)
        if not norm or not self.commands:
            return None
        remainder = normalize_command_text(strip_wake_prefix(norm, self.phrases))
        candidates = [norm, remainder]
        for base in (norm, remainder):
            if base.endswith(" por favor"):
                candidates.append(base[:-len(" por favor")].strip())
        for cmd in sorted(self.commands.keys(), key=len, reverse=True):
            cmd_norm = normalize_command_text(cmd)
            if cmd_norm in candidates:
                return cmd
        return None

    def reload_acoustic_templates(self):
        """Reloads enrolled acoustic wake-word and command templates from disk."""
        if self.acoustic_matcher is not None:
            counts = self.acoustic_matcher.load_templates()
            self.acoustic_matcher.reset_stream()
            logger.info(f"Reloaded acoustic wake templates: {counts}")

    def update_commands(self, commands: Dict[str, Callable[[], None]]):
        """Updates the registered instant/custom voice command callbacks."""
        self.commands = {
            " ".join(k.lower().strip().split()): v
            for k, v in (commands or {}).items()
            if k.strip()
        }

    def _process_acoustic_chunk(self, data: bytes, recognizer) -> bool:
        """
        Runs acoustic neural-embedding matching when templates are enrolled.
        Returns True if acoustic mode handled wake detection (bypassing Vosk/Whisper wake).
        """
        if not self.acoustic_matcher or not self.acoustic_matcher.has_templates("wake"):
            return False

        now = time.monotonic()
        in_window = self._capturing_freeform or (now <= self._command_window_until)
        if in_window:
            cmd_labels = [lbl for lbl in self.acoustic_matcher.templates.keys() if lbl != "wake" and lbl in self.commands]
            if cmd_labels:
                match = self.acoustic_matcher.process_pcm_chunk(data, allowed_labels=cmd_labels)
                if match:
                    matched_label, score = match
                    self._last_trigger_time = now
                    self._command_window_until = 0.0
                    self._wake_triggered_in_utterance = False
                    self._reset_freeform_capture()
                    self.acoustic_matcher.reset_stream()
                    if recognizer is not None:
                        try:
                            recognizer.Reset()
                        except Exception:
                            pass
                    logger.info(f"Acoustic command matched '{matched_label}' (score={score:.3f})")
                    self.commands[matched_label]()
            return True

        match = self.acoustic_matcher.process_pcm_chunk(data, allowed_labels=["wake"])
        if match:
            _, score = match
            logger.info(f"Acoustic wake matched 'wake' (score={score:.3f})")
            if self._maybe_trigger(recognizer, reset_recognizer=True, open_freeform=True):
                self._freeform_grace_chunks = 4  # skip ~400ms trailing tail of wake phrase
        return True

    def _extract_payload(self, raw_json: str) -> Dict[str, Any]:
        if not raw_json:
            return {}
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            return {}

    def _process_recognizer_json(self, raw_json: str, field: str) -> bool:
        """Parses Vosk JSON output and checks if a wake phrase matched."""
        payload = self._extract_payload(raw_json)
        return self._match_text(payload.get(field, "") or "")

    def _reset_freeform_capture(self):
        self._capturing_freeform = False
        self._freeform_chunks = []
        self._freeform_has_speech = False
        self._freeform_silence_chunks = 0
        self._freeform_grace_chunks = 0

    def _dispatch_wav_async(self, chunks: List[bytes]):
        if not self.on_freeform_audio or not chunks:
            return
        try:
            wav_path = _write_pcm_wav(chunks, self.sample_rate)
            threading.Thread(
                target=self.on_freeform_audio,
                args=(wav_path,),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"Failed to dispatch free-form voice order: {e}")

    def _process_freeform_chunk(self, chunk: bytes, recognizer):
        """Buffers post-wake audio and dispatches WAV when speech ends, or cancels on silence."""
        if not self._capturing_freeform or not self.on_freeform_audio:
            return
        if self._freeform_grace_chunks > 0:
            self._freeform_grace_chunks -= 1
            return
        self._freeform_chunks.append(chunk)
        if _pcm_rms(chunk) >= self.speech_rms_threshold:
            self._freeform_has_speech = True
            self._freeform_silence_chunks = 0
        else:
            self._freeform_silence_chunks += 1

        if not self._freeform_has_speech and self._freeform_silence_chunks >= 35:
            self._reset_freeform_capture()
            if self.on_listening_ended:
                try:
                    self.on_listening_ended()
                except Exception:
                    pass
            return

        if self._freeform_has_speech and (
            self._freeform_silence_chunks >= 10 or len(self._freeform_chunks) >= 100
        ):
            chunks = self._freeform_chunks
            self._reset_freeform_capture()
            self._command_window_until = 0.0
            try:
                recognizer.Reset()
            except Exception:
                pass
            self._dispatch_wav_async(chunks)

    def _maybe_trigger(self, recognizer, reset_recognizer: bool = False, open_freeform: bool = True) -> bool:
        """Triggers wake callback if cooldown has elapsed, opening command window."""
        now = time.monotonic()
        if now - self._last_trigger_time < self.cooldown_seconds:
            return False
        self._last_trigger_time = now
        self._command_window_until = now + self.command_window_seconds
        self._wake_triggered_in_utterance = True
        if open_freeform and self.on_freeform_audio is not None and not self._capturing_freeform:
            self._capturing_freeform = True
            self._freeform_chunks = []
            self._freeform_has_speech = False
            self._freeform_silence_chunks = 0
        if reset_recognizer and recognizer is not None:
            try:
                recognizer.Reset()
            except Exception:
                pass
        logger.info(f"Wake word detected ({self.phrases[0]})")
        try:
            self.on_detected()
        except Exception as e:
            logger.error(f"Error in wake-word callback: {e}")
        return True

    def _has_sufficient_speech(self, chunks: List[bytes]) -> bool:
        """Returns True if audio chunks contain non-silent signal."""
        return bool(chunks) and any(_pcm_rms(c) > 0.0 for c in chunks)

    def _verify_and_trigger_async(
        self,
        chunks: List[bytes],
        recognizer,
        open_freeform: bool,
        inline_chunks: Optional[List[bytes]] = None,
        inline_command: Optional[str] = None,
    ):
        """Verifies candidate audio in a background thread before triggering wake action."""
        if not self._has_sufficient_speech(chunks):
            return
        try:
            wav_path = _write_pcm_wav(chunks, self.sample_rate)
        except Exception as e:
            logger.error(f"Failed to write wake verification WAV: {e}")
            return

        def _worker():
            try:
                verdict = self.verify_wake_audio(wav_path) if self.verify_wake_audio else False
                if isinstance(verdict, tuple):
                    ok, transcript = bool(verdict[0]), (verdict[1] or "")
                else:
                    ok, transcript = bool(verdict), ""

                if ok:
                    resolved_cmd = inline_command or (self._match_command(transcript) if transcript else None)
                    if resolved_cmd and resolved_cmd in self.commands:
                        self._last_trigger_time = time.monotonic()
                        self._command_window_until = 0.0
                        self._wake_triggered_in_utterance = False
                        self._reset_freeform_capture()
                        if recognizer is not None:
                            try:
                                recognizer.Reset()
                            except Exception:
                                pass
                        logger.info(f"Verified inline voice command: {resolved_cmd}")
                        self.commands[resolved_cmd]()
                        return

                    remainder_text = strip_wake_prefix(transcript, self.phrases) if transcript else ""
                    if len(remainder_text) >= 2 and self.on_freeform_text is not None:
                        self._maybe_trigger(recognizer, reset_recognizer=False, open_freeform=False)
                        self._command_window_until = 0.0
                        self._reset_freeform_capture()
                        self.on_freeform_text(remainder_text)
                        return

                    self._maybe_trigger(recognizer, reset_recognizer=False, open_freeform=open_freeform)
                    if inline_chunks:
                        self._command_window_until = 0.0
                        self._reset_freeform_capture()
                        self._dispatch_wav_async(inline_chunks)
                else:
                    self._reset_freeform_capture()
            except Exception as e:
                self._reset_freeform_capture()
                logger.error(f"Wake verification failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_transcript(
        self,
        text: str,
        recognizer,
        is_final: bool = False,
        words: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Handles wake-word (on partial or final) and follow-up commands (on final only).
        """
        utterance_chunks = list(self._utterance_chunks) if is_final else []
        if is_final:
            self._utterance_chunks.clear()

        if not text:
            if is_final:
                self._wake_triggered_in_utterance = False
                self._partial_wake_streak = 0
            return False

        now = time.monotonic()
        matched_wake = self._matched_wake_phrase(text)
        if is_final:
            has_wake = matched_wake is not None and self._check_word_confidence(words, matched_wake)
            self._partial_wake_streak = 0
        else:
            if matched_wake is not None:
                self._partial_wake_streak += 1
            else:
                self._partial_wake_streak = 0
            has_wake = matched_wake is not None and self._partial_wake_streak >= self.partial_streak_required

        matched_cmd = self._match_command(text) if is_final else None
        remainder = strip_wake_prefix(text, self.phrases) if (is_final and has_wake) else ""

        triggered = False
        if has_wake and not self._wake_triggered_in_utterance:
            has_inline_freeform = bool(remainder) and matched_cmd is None and self.on_freeform_audio is not None
            if self.verify_wake_audio is not None:
                if not is_final and not self.verify_on_partial:
                    return False
                verify_chunks = list(utterance_chunks) if (is_final and utterance_chunks) else list(self._recent_chunks)
                can_verify = (is_final or now - self._last_verify_time >= 1.5) and bool(verify_chunks)
                if can_verify:
                    self._last_verify_time = now
                    self._verify_and_trigger_async(
                        verify_chunks,
                        recognizer,
                        open_freeform=(not has_inline_freeform and matched_cmd is None),
                        inline_chunks=utterance_chunks if has_inline_freeform else None,
                        inline_command=matched_cmd,
                    )
                if is_final:
                    self._wake_triggered_in_utterance = False
                    self._partial_wake_streak = 0
                return False
            triggered = self._maybe_trigger(
                recognizer,
                reset_recognizer=False,
                open_freeform=not has_inline_freeform,
            )
            if has_inline_freeform and utterance_chunks:
                self._command_window_until = 0.0
                self._wake_triggered_in_utterance = False
                self._reset_freeform_capture()
                self._dispatch_wav_async(utterance_chunks)
                return True

        if matched_cmd and (has_wake or now <= self._command_window_until):
            self._command_window_until = 0.0
            self._wake_triggered_in_utterance = False
            self._partial_wake_streak = 0
            self._reset_freeform_capture()
            try:
                recognizer.Reset()
            except Exception:
                pass
            logger.info(f"Voice command detected: {matched_cmd}")
            try:
                self.commands[matched_cmd]()
            except Exception as e:
                logger.error(f"Error in voice command callback '{matched_cmd}': {e}")
            return True

        if is_final:
            self._wake_triggered_in_utterance = False
            self._partial_wake_streak = 0

        return triggered

    def _listen_loop(self):
        try:
            import vosk
            vosk.SetLogLevel(-1)
        except Exception as e:
            logger.error(f"Vosk is not available: {e}")
            self._running = False
            return

        model_path = ensure_vosk_model(self.model_dir)
        if not model_path or self._stop_event.is_set():
            self._running = False
            return

        try:
            model = vosk.Model(str(model_path))
            grammar = self._build_grammar()
            recognizer = vosk.KaldiRecognizer(model, self.sample_rate, grammar)
            recognizer.SetWords(True)
        except Exception as e:
            logger.error(f"Failed to initialize Vosk wake-word recognizer: {e}")
            self._running = False
            return

        cmd = find_raw_pcm_recorder_command(self.sample_rate)
        if not cmd:
            logger.error("No raw PCM audio capture tool found (ffmpeg, arecord, pw-record).")
            self._running = False
            return

        chunk_bytes = int(self.sample_rate * 2 * 0.1)  # 100ms of 16-bit mono PCM

        while not self._stop_event.is_set():
            if self.should_pause and self.should_pause():
                self._terminate_process()
                self._utterance_chunks.clear()
                self._recent_chunks.clear()
                self._reset_freeform_capture()
                try:
                    recognizer.Reset()
                except Exception:
                    pass
                time.sleep(0.25)
                continue

            if self._process is None or self._process.poll() is not None:
                try:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"Wake-word listener active (strict full-vocab, {cmd[0]}), phrases={self.phrases}")
                except Exception as e:
                    logger.error(f"Failed to start audio capture for wake word: {e}")
                    time.sleep(2.0)
                    continue

            stdout = self._process.stdout
            if stdout is None:
                self._terminate_process()
                time.sleep(0.5)
                continue

            data = stdout.read(chunk_bytes)
            if not data:
                self._terminate_process()
                time.sleep(0.3)
                continue

            if self.should_pause and self.should_pause():
                continue

            self._utterance_chunks.append(data)
            if len(self._utterance_chunks) > 120:
                self._utterance_chunks.pop(0)
            self._recent_chunks.append(data)
            if len(self._recent_chunks) > 40:
                self._recent_chunks.pop(0)

            if not self._process_acoustic_chunk(data, recognizer):
                if recognizer.AcceptWaveform(data):
                    payload = self._extract_payload(recognizer.Result())
                    self._handle_transcript(
                        payload.get("text", "") or "",
                        recognizer,
                        is_final=True,
                        words=payload.get("result"),
                    )
                else:
                    payload = self._extract_payload(recognizer.PartialResult())
                    self._handle_transcript(
                        payload.get("partial", "") or "",
                        recognizer,
                        is_final=False,
                    )

            self._process_freeform_chunk(data, recognizer)

        self._terminate_process()
