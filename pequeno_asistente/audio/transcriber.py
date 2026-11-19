"""
Audio recording and transcription engine using faster-whisper.
Designed for Linux (Wayland, Hyprland, GNOME) supporting pw-record, ffmpeg, and arecord.
"""
import os
import wave
import array
import signal
import shutil
import logging
import tempfile
import threading
import subprocess
from typing import Optional, Callable, Dict, Any

# Ensure PyAV compatibility across all versions (av>=14 removed metadata_errors param)
try:
    import av
    if hasattr(av, "open"):
        _orig_av_open = av.open
        def _safe_av_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return _orig_av_open(*args, **kwargs)
        av.open = _safe_av_open
except Exception:
    pass

logger = logging.getLogger("pequeno_asistente.audio")


class AudioTranscriber:
    """Manages audio recording from microphone and faster-whisper transcription."""

    _cached_model = None
    _cached_model_key = None
    _model_lock = threading.Lock()

    DEFAULT_ES_PROMPT = (
        "Okay asistente. Comandos de voz en español: nuevo chat, abrir configuración, "
        "ocultar ventana, abrir archivos."
    )

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cuda",
        compute_type: str = "float16",
        language: Optional[str] = "es",
        beam_size: int = 5,
        initial_prompt: Optional[str] = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt

        self._process: Optional[subprocess.Popen] = None
        self._current_wav_path: Optional[str] = None
        self._is_recording = False
        self._is_transcribing = False
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_transcribing(self) -> bool:
        return self._is_transcribing

    @classmethod
    def find_recorder_command(cls, output_wav: str) -> Optional[list]:
        """Detects available recording CLI utility on the system."""
        # 1. PipeWire standard recorder (preferred on modern Linux/GNOME/Wayland)
        if shutil.which("pw-record"):
            return ["pw-record", "--rate", "16000", "--channels", "1", output_wav]

        # 2. FFmpeg with PulseAudio/PipeWire pulse emulation
        if shutil.which("ffmpeg"):
            return [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "pulse", "-i", "default",
                "-ar", "16000", "-ac", "1",
                output_wav
            ]

        # 3. ALSA arecord fallback
        if shutil.which("arecord"):
            return ["arecord", "-q", "-r", "16000", "-c", "1", "-f", "S16_LE", output_wav]

        return None

    def start_recording(self) -> bool:
        """Starts capturing audio from the default microphone to a temporary WAV file."""
        with self._lock:
            if self._is_recording:
                logger.warning("Recording is already in progress.")
                return False

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", prefix="asistente_rec_", delete=False)
            self._current_wav_path = tmp.name
            tmp.close()

            cmd = self.find_recorder_command(self._current_wav_path)
            if not cmd:
                logger.error("No suitable audio recording command found (pw-record, ffmpeg, or arecord).")
                if os.path.exists(self._current_wav_path):
                    try:
                        os.unlink(self._current_wav_path)
                    except Exception:
                        pass
                self._current_wav_path = None
                return False

            try:
                logger.info(f"Starting audio capture using: {' '.join(cmd[:2])} -> {self._current_wav_path}")
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self._is_recording = True
                return True
            except Exception as e:
                logger.error(f"Failed to start recording process: {e}")
                if self._current_wav_path and os.path.exists(self._current_wav_path):
                    try:
                        os.unlink(self._current_wav_path)
                    except Exception:
                        pass
                self._current_wav_path = None
                self._process = None
                self._is_recording = False
                return False

    def stop_recording(self) -> Optional[str]:
        """Stops the recording process and returns the WAV file path if valid."""
        with self._lock:
            if not self._is_recording or not self._process:
                return None

            proc = self._process
            wav_path = self._current_wav_path
            self._is_recording = False
            self._process = None

        try:
            # Send SIGINT so recorder properly writes WAV header and closes
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except Exception as e:
            logger.warning(f"Error terminating recording process: {e}")

        if wav_path and os.path.exists(wav_path):
            file_size = os.path.getsize(wav_path)
            if file_size > 1000:
                return wav_path
            else:
                logger.info(f"Recorded audio file too small ({file_size} bytes). Discarding.")
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

        return None

    def _get_whisper_model(self):
        """Loads and caches the faster-whisper WhisperModel instance with CPU fallback."""
        model_key = f"{self.model_size}:{self.device}:{self.compute_type}"
        with self._model_lock:
            if AudioTranscriber._cached_model is not None and AudioTranscriber._cached_model_key == model_key:
                return AudioTranscriber._cached_model

            from faster_whisper import WhisperModel
            try:
                logger.info(
                    f"Loading faster-whisper model '{self.model_size}' "
                    f"(device={self.device}, compute_type={self.compute_type})..."
                )
                model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=4
                )
            except Exception as e:
                if self.device != "cpu":
                    logger.warning(
                        f"Failed to load faster-whisper on device='{self.device}' ({e}); "
                        "falling back to device='cpu', compute_type='int8'."
                    )
                    fallback_key = f"{self.model_size}:cpu:int8"
                    if AudioTranscriber._cached_model is not None and AudioTranscriber._cached_model_key == fallback_key:
                        return AudioTranscriber._cached_model
                    model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=4
                    )
                    model_key = fallback_key
                else:
                    raise

            AudioTranscriber._cached_model = model
            AudioTranscriber._cached_model_key = model_key
            logger.info("faster-whisper model loaded successfully.")
            return model

    @staticmethod
    def _normalize_wav_in_place(wav_path: str, target_peak: int = 24000) -> None:
        """Normalizes quiet 16-bit PCM WAV files in place to improve Whisper accuracy."""
        try:
            with wave.open(wav_path, "rb") as rf:
                params = rf.getparams()
                if params.sampwidth != 2 or params.nchannels != 1:
                    return
                frames = rf.readframes(params.nframes)
            if not frames:
                return
            samples = array.array("h")
            samples.frombytes(frames)
            peak = max((abs(s) for s in samples), default=0)
            # Boost only quiet recordings (avoid amplifying pure silence/noise)
            if 300 < peak < 16000:
                gain = min(target_peak / peak, 8.0)
                for i in range(len(samples)):
                    val = int(samples[i] * gain)
                    if val > 32767:
                        val = 32767
                    elif val < -32768:
                        val = -32768
                    samples[i] = val
                with wave.open(wav_path, "wb") as wf:
                    wf.setparams(params)
                    wf.writeframes(samples.tobytes())
        except Exception as e:
            logger.debug(f"Skipping WAV normalization: {e}")

    def transcribe_file(self, wav_path: str) -> str:
        """Transcribes a WAV file into text using faster-whisper and removes the temporary file."""
        try:
            self._normalize_wav_in_place(wav_path)
            model = self._get_whisper_model()
            kwargs: Dict[str, Any] = {
                "beam_size": self.beam_size,
                "vad_filter": True,
                "vad_parameters": {
                    "min_silence_duration_ms": 400,
                    "speech_pad_ms": 250,
                },
                "condition_on_previous_text": False,
            }
            if self.language:
                kwargs["language"] = self.language
            prompt = self.initial_prompt
            if prompt is None and self.language == "es":
                prompt = self.DEFAULT_ES_PROMPT
            if prompt:
                kwargs["initial_prompt"] = prompt

            segments, info = model.transcribe(wav_path, **kwargs)
            transcript_parts = []
            for seg in segments:
                text = seg.text.strip()
                if text:
                    transcript_parts.append(text)

            result = " ".join(transcript_parts).strip()
            logger.info(f"Transcription complete (detected lang: {info.language}, prob: {info.language_probability:.2f}): '{result}'")
            return result
        finally:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

    def stop_and_transcribe_async(self, callback: Callable[[Optional[str], Optional[str]], None]):
        """
        Stops recording and runs transcription in a background thread.
        Calls callback(transcript_text, error_message).
        """
        wav_path = self.stop_recording()
        if not wav_path:
            callback(None, None)
            return

        self._is_transcribing = True

        def worker():
            try:
                text = self.transcribe_file(wav_path)
                self._is_transcribing = False
                callback(text, None)
            except Exception as e:
                logger.error(f"Error during audio transcription: {e}")
                self._is_transcribing = False
                callback(None, str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
