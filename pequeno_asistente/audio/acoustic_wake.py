"""
Acoustic Neural-Embedding Wake-Word & Instant-Command Matcher.
Uses openwakeword's ONNX melspectrogram + speech embedding models (no ASR/transcription)
to match enrolled voice samples directly from audio via normalized DTW cosine similarity.
"""
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("pequeno_asistente.acoustic_wake")

DEFAULT_TEMPLATES_DIR = Path.home() / ".config" / "pequeno-asistente" / "wake_templates"


def _l2_normalize_frames(frames: np.ndarray) -> np.ndarray:
    """L2-normalizes each 96-dim embedding row."""
    if frames.size == 0:
        return frames
    norms = np.linalg.norm(frames, axis=-1, keepdims=True)
    norms = np.where(norms > 1e-8, norms, 1.0)
    return frames / norms


def trim_silence_pcm(pcm: np.ndarray, sample_rate: int = 16000, rms_threshold: float = 220.0) -> np.ndarray:
    """Trims leading and trailing silence from 16-bit mono PCM using 40ms frames."""
    if pcm.size == 0:
        return pcm
    frame_len = max(1, int(sample_rate * 0.04))
    n_frames = len(pcm) // frame_len
    if n_frames == 0:
        return pcm
    active = []
    for i in range(n_frames):
        seg = pcm[i * frame_len:(i + 1) * frame_len].astype(np.float32)
        rms = float(np.sqrt(np.mean(seg * seg)))
        if rms >= rms_threshold:
            active.append(i)
    if not active:
        return np.array([], dtype=np.int16)
    start_idx = max(0, (active[0] - 2) * frame_len)
    end_idx = min(len(pcm), (active[-1] + 3) * frame_len)
    return pcm[start_idx:end_idx]


def dtw_cosine_similarity(query: np.ndarray, template: np.ndarray) -> float:
    """
    Computes length-normalized DTW cosine similarity in [0, 1] between two
    L2-normalized embedding sequences of shape (Q, D) and (T, D).
    """
    q_len, t_len = len(query), len(template)
    if q_len == 0 or t_len == 0:
        return 0.0
    # Cost matrix: 1 - cosine_similarity
    sim_mat = np.clip(query @ template.T, -1.0, 1.0)
    cost_mat = 1.0 - sim_mat

    dp = np.full((q_len + 1, t_len + 1), np.inf, dtype=np.float32)
    steps = np.zeros((q_len + 1, t_len + 1), dtype=np.int32)
    dp[0, 0] = 0.0

    for i in range(1, q_len + 1):
        for j in range(1, t_len + 1):
            c = float(cost_mat[i - 1, j - 1])
            prev_vals = (dp[i - 1, j - 1], dp[i - 1, j], dp[i, j - 1])
            prev_steps = (steps[i - 1, j - 1], steps[i - 1, j], steps[i, j - 1])
            best_idx = 0
            if prev_vals[1] < prev_vals[best_idx]:
                best_idx = 1
            if prev_vals[2] < prev_vals[best_idx]:
                best_idx = 2
            dp[i, j] = c + prev_vals[best_idx]
            steps[i, j] = prev_steps[best_idx] + 1

    path_len = max(1, int(steps[q_len, t_len]))
    avg_cost = float(dp[q_len, t_len]) / path_len
    return float(max(0.0, min(1.0, 1.0 - avg_cost)))


class AcousticWakeMatcher:
    """
    Real-time acoustic keyword spotter using openwakeword's AudioFeatures
    (melspectrogram.onnx + embedding_model.onnx) and enrolled voice templates.
    """

    def __init__(
        self,
        templates_dir: Optional[Path] = None,
        threshold: float = 0.56,
        sample_rate: int = 16000,
    ):
        self.templates_dir = Path(templates_dir) if templates_dir else DEFAULT_TEMPLATES_DIR
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.templates: Dict[str, List[np.ndarray]] = {}
        self._features = None
        self._vad = None
        self._bg_dir: Optional[np.ndarray] = None
        self._pcm_buf = bytearray()
        self._step_bytes = 1280 * 2  # 80ms of 16-bit mono 16kHz PCM
        self._frames_since_reset = 0
        self._last_match_time: Dict[str, float] = {}
        self.load_templates()

    def _ensure_features(self):
        if self._features is None:
            from openwakeword.utils import AudioFeatures
            self._features = AudioFeatures()
            try:
                from openwakeword.vad import VAD
                self._vad = VAD()
            except Exception as e:
                logger.warning(f"Silero VAD unavailable: {e}")
                self._vad = None
            try:
                sil = self._features.embed_clips(np.zeros((1, self.sample_rate * 2), dtype=np.int16))[0]
                sil_norm = _l2_normalize_frames(sil.astype(np.float32)).mean(axis=0, keepdims=True)
                n = float(np.linalg.norm(sil_norm))
                self._bg_dir = (sil_norm / n) if n > 1e-8 else None
            except Exception:
                self._bg_dir = None
        return self._features

    def _center_frames(self, frames: np.ndarray) -> np.ndarray:
        """Removes the shared silence/background direction before L2 normalization."""
        normed = _l2_normalize_frames(frames.astype(np.float32))
        if self._bg_dir is None or normed.size == 0:
            return normed
        proj = (normed @ self._bg_dir.T) * self._bg_dir
        return _l2_normalize_frames(normed - proj)

    def has_templates(self, label: Optional[str] = None) -> bool:
        if label is not None:
            return bool(self.templates.get(label))
        return any(bool(v) for v in self.templates.values())

    def template_counts(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self.templates.items()}

    def load_templates(self) -> Dict[str, int]:
        self.templates.clear()
        if not self.templates_dir.exists():
            return {}
        for npz_path in sorted(self.templates_dir.glob("*.npz")):
            label = npz_path.stem.replace("_", " ")
            try:
                data = np.load(npz_path, allow_pickle=False)
                clips = [data[k] for k in sorted(data.files) if data[k].ndim == 2 and len(data[k]) > 0]
                if clips:
                    self.templates[label] = clips
            except Exception as e:
                logger.warning(f"Failed to load acoustic template {npz_path}: {e}")
        return self.template_counts()

    def save_label_templates(self, label: str, embeddings_list: List[np.ndarray]):
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "_".join(label.lower().strip().split())
        out_path = self.templates_dir / f"{safe_name}.npz"
        payload = {f"sample_{i}": arr.astype(np.float32) for i, arr in enumerate(embeddings_list)}
        np.savez(out_path, **payload)
        self.templates[label] = [arr.astype(np.float32) for arr in embeddings_list]

    def clear_templates(self, label: Optional[str] = None):
        if label is not None:
            self.templates.pop(label, None)
            safe_name = "_".join(label.lower().strip().split())
            p = self.templates_dir / f"{safe_name}.npz"
            if p.exists():
                p.unlink()
            return
        self.templates.clear()
        if self.templates_dir.exists():
            for p in self.templates_dir.glob("*.npz"):
                p.unlink()

    def embed_pcm(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        """Converts a 16-bit mono PCM clip into normalized (T, 96) neural speech embeddings."""
        trimmed = trim_silence_pcm(pcm, sample_rate=self.sample_rate)
        if len(trimmed) < int(self.sample_rate * 0.35):
            return None
        af = self._ensure_features()
        # Pad slightly so AudioFeatures produces at least 1+ embedding frames
        min_samples = max(len(trimmed), int(self.sample_rate * 1.2))
        padded = np.zeros(min_samples, dtype=np.int16)
        padded[:len(trimmed)] = trimmed
        emb = af.embed_clips(padded[None, :])[0]  # (T, 96)
        # Keep only frames covering the trimmed audio duration (~80ms per frame)
        n_active = max(2, int(np.ceil(len(trimmed) / 1280.0)))
        emb = emb[:min(len(emb), n_active)]
        return _l2_normalize_frames(emb.astype(np.float32))

    def enroll_pcm(self, label: str, pcm: np.ndarray) -> int:
        """Extracts and appends an acoustic template for `label`. Returns total sample count."""
        emb = self.embed_pcm(pcm)
        if emb is None or len(emb) < 2:
            raise ValueError("No se detectó suficiente voz en la grabación.")
        existing = list(self.templates.get(label, []))
        existing.append(emb)
        self.save_label_templates(label, existing)
        return len(existing)

    def reset_stream(self):
        self._pcm_buf.clear()
        self._frames_since_reset = 0
        if self._features is not None:
            self._features.feature_buffer[:] = 0
            self._features.raw_data_buffer.clear()
        if self._vad is not None:
            try:
                self._vad.reset_states()
            except Exception:
                pass

    def process_pcm_chunk(
        self,
        chunk: bytes,
        allowed_labels: Optional[List[str]] = None,
        min_rms: float = 220.0,
    ) -> Optional[Tuple[str, float]]:
        """
        Feeds raw 16-bit mono PCM bytes and returns (matched_label, score) if an enrolled
        acoustic template matches active speech above threshold.
        """
        if not self.templates:
            return None
        self._pcm_buf.extend(chunk)
        if len(self._pcm_buf) < self._step_bytes:
            return None

        af = self._ensure_features()
        step_had_speech = False
        while len(self._pcm_buf) >= self._step_bytes:
            frame_bytes = bytes(self._pcm_buf[:self._step_bytes])
            del self._pcm_buf[:self._step_bytes]
            samples = np.frombuffer(frame_bytes, dtype=np.int16)
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            vad_ok = True
            if self._vad is not None:
                try:
                    self._vad(samples)
                    vad_score = float(self._vad.prediction_buffer[-1]) if self._vad.prediction_buffer else 0.0
                    vad_ok = vad_score >= 0.45
                except Exception:
                    vad_ok = True
            if rms >= min_rms and vad_ok:
                step_had_speech = True
            af(samples)
            self._frames_since_reset += 1

        if not step_had_speech:
            return None

        avail_frames = min(len(af.feature_buffer), self._frames_since_reset)
        if avail_frames < 2:
            return None

        buf = self._center_frames(af.feature_buffer[-avail_frames:])
        now = time.monotonic()
        best_match: Optional[Tuple[str, float]] = None

        labels = allowed_labels if allowed_labels is not None else list(self.templates.keys())
        for label in labels:
            tmpl_list = self.templates.get(label, [])
            if not tmpl_list:
                continue
            if now - self._last_match_time.get(label, 0.0) < 2.5:
                continue
            for tmpl in tmpl_list:
                centered_tmpl = self._center_frames(tmpl)
                t_len = len(centered_tmpl)
                if len(buf) < t_len:
                    continue
                for w_len in (t_len, max(2, int(round(t_len * 0.85))), min(len(buf), int(round(t_len * 1.15)))):
                    if w_len > len(buf):
                        continue
                    window = buf[-w_len:]
                    score = dtw_cosine_similarity(window, centered_tmpl)
                    if score >= self.threshold:
                        if best_match is None or score > best_match[1]:
                            best_match = (label, score)

        if best_match is not None:
            self._last_match_time[best_match[0]] = now
            self.reset_stream()
        return best_match


def record_pcm_clip(duration_seconds: float = 2.5, sample_rate: int = 16000) -> np.ndarray:
    """Records `duration_seconds` of 16kHz mono PCM using the system recorder."""
    from pequeno_asistente.audio.wake_word import find_raw_pcm_recorder_command
    cmd = find_raw_pcm_recorder_command(sample_rate)
    if not cmd:
        raise RuntimeError("No se encontró herramienta de grabación (ffmpeg, arecord o pw-record).")
    target_bytes = int(sample_rate * 2 * duration_seconds)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    collected = bytearray()
    try:
        while len(collected) < target_bytes and proc.stdout is not None:
            chunk = proc.stdout.read(min(3200, target_bytes - len(collected)))
            if not chunk:
                break
            collected.extend(chunk)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()
    usable = len(collected) - (len(collected) % 2)
    return np.frombuffer(bytes(collected[:usable]), dtype=np.int16).copy()
