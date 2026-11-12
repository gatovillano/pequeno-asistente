"""
Media, Document, Image & Audio Processor for Pequeño Asistente.
Provides native capabilities to:
- Detect if the active LLM model is multimodal (supports vision / image inputs and native audio inputs).
- Automatically detect file/image/audio/document paths in user messages.
- Read, resize, inspect, and Base64-encode images (PNG, JPEG, WEBP, GIF, BMP, SVG, TIFF) for multimodal models.
- Read, inspect, and Base64-encode audio files (WAV, MP3, OGG, OPUS, FLAC, M4A, AAC, WEBM, AIFF) for direct multimodal audio processing by LLMs.
- Read and extract structured text, tables, and metadata from documents (PDF, DOCX, ODT, XLSX, CSV, PPTX, EPUB, HTML, Markdown, JSON, source code, etc.).
"""
import os
import re
import csv
import io
import json
import wave
import base64
import shutil
import struct
import zipfile
import logging
import mimetypes
import subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("pequeno_asistente.media")

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".tiff", ".tif", ".ico", ".avif"
}

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac",
    ".webm", ".wma", ".aiff", ".aif", ".amr", ".3gp", ".mpga", ".mpeg"
}

DOCUMENT_EXTENSIONS = {
    # Office & PDF
    ".pdf", ".docx", ".doc", ".odt", ".rtf",
    ".xlsx", ".xls", ".ods", ".csv", ".tsv",
    ".pptx", ".ppt", ".odp", ".epub",
    # Text, Markup & Config
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".xml", ".html", ".htm", ".log",
    # Source Code
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".kt", ".swift",
    ".sql", ".css", ".scss", ".lua", ".rb", ".php", ".r", ".pl"
}


def format_file_size(size_bytes: int) -> str:
    """Formats byte size into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def resolve_local_path(raw_path: str) -> Path:
    """Normalizes a local path or file:// URI into a resolved Path object."""
    cleaned = raw_path.strip()
    if cleaned.startswith("file://"):
        from urllib.parse import unquote, urlparse
        parsed = urlparse(cleaned)
        cleaned = unquote(parsed.path)
    # Strip surrounding quotes if present
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()
    return Path(cleaned).expanduser().resolve()


def is_image_file(path: str | Path) -> bool:
    """Checks if the path has a supported image extension or image MIME type."""
    try:
        p = resolve_local_path(str(path)) if isinstance(path, str) else path
        ext = p.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            return True
        mime, _ = mimetypes.guess_type(str(p))
        return bool(mime and mime.startswith("image/"))
    except Exception:
        return False


def is_audio_file(path: str | Path) -> bool:
    """Checks if the path has a supported audio extension or audio MIME type."""
    try:
        p = resolve_local_path(str(path)) if isinstance(path, str) else path
        ext = p.suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            return True
        mime, _ = mimetypes.guess_type(str(p))
        return bool(mime and mime.startswith("audio/"))
    except Exception:
        return False


def is_document_file(path: str | Path) -> bool:
    """Checks if the path is a supported document or text/code file."""
    try:
        p = resolve_local_path(str(path)) if isinstance(path, str) else path
        if is_image_file(p) or is_audio_file(p):
            return False
        ext = p.suffix.lower()
        if ext in DOCUMENT_EXTENSIONS:
            return True
        mime, _ = mimetypes.guess_type(str(p))
        if mime and (mime.startswith("text/") or mime in ("application/pdf", "application/json", "application/xml")):
            return True
        # Check if small readable UTF-8 text file without extension
        if p.exists() and p.is_file() and p.stat().st_size <= 5 * 1024 * 1024:
            with open(p, "rb") as f:
                sample = f.read(1024)
            if b"\x00" not in sample:
                return True
        return False
    except Exception:
        return False


def is_model_multimodal(model_name: str, provider: Optional[str] = None) -> bool:
    """
    Determines whether the specified LLM model supports native multimodal vision/image inputs.
    Combines explicit provider/model family heuristics with LiteLLM's vision registry.
    """
    if not model_name:
        return False

    m_lower = model_name.lower().strip()
    prov_lower = (provider or "").lower().strip()

    if "/" in m_lower and not prov_lower:
        prov_lower = m_lower.split("/", 1)[0]

    # Known text-only models (even if provider supports vision on other models)
    text_only_keywords = (
        "llama-3.3-70b", "llama-3.1-", "llama3.1", "llama3.3",
        "deepseek-chat", "deepseek-r1", "deepseek-coder", "deepseek-v3",
        "qwen2.5-coder", "qwen-2.5-coder", "codestral",
        "o1-mini", "o3-mini", "gpt-3.5", "mistral-7b", "mixtral-8x"
    )
    if any(k in m_lower for k in text_only_keywords):
        return False

    # 1. Antigravity & Google Gemini models (Gemini 1.5, 2.0, 2.5, 3.x, 4.x, Pro, Flash, Claude in Antigravity)
    if prov_lower in ("antigravity", "google", "gemini") or m_lower.startswith(("antigravity/", "gemini/")):
        return True

    # 2. Multimodal model families across OpenAI, Anthropic, OpenRouter, KiloCode, OpenCode, Ollama
    vision_families = (
        "gemini-", "gemini/", "gemini",
        "gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o",
        "claude-3", "claude-3.5", "claude-3-5", "claude-3.7", "claude-3-7",
        "claude-4", "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
        "pixtral", "llava", "bakllava", "moondream", "minicpm-v", "minicpmv",
        "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen2.5vl", "qwenvl",
        "llama-3.2-11b-vision", "llama-3.2-90b-vision", "llama3.2-vision", "llama-4",
        "gemma-3", "gemma3", "internvl", "molmo", "yi-vision", "glm-4v",
        "granite3.2-vision", "kilo/auto", "phi-3-vision", "phi-3.5-vision", "phi-4",
        "deepseek-vl", "deepseek-vl2", "mistral-small-2409", "ministral"
    )
    if any(vf in m_lower for vf in vision_families):
        return True

    # Generic vision/multimodal keywords in model identifier
    if any(vk in m_lower for vk in ("-vl", "_vl", "/vl", "vision", "visual", "multimodal", "omni")):
        return True

    # 3. OpenAI o1 / o3 / o4 full models support vision
    pure_name = m_lower.split("/")[-1]
    if pure_name in ("o1", "o3", "o4", "o4-mini") or pure_name.startswith(("o1-202", "o3-202", "o4-")):
        return True

    # 4. Fallback to litellm.supports_vision
    try:
        import litellm
        if litellm.supports_vision(model=model_name):
            return True
        stripped_name = m_lower.split("/", 1)[1] if m_lower.startswith(("vercel/", "openrouter/", "kilocode/", "opencode/")) else m_lower
        if litellm.supports_vision(model=stripped_name):
            return True
        if "/" in model_name and litellm.supports_vision(model=pure_name):
            return True
    except Exception:
        pass

    return False


def is_model_audio_multimodal(model_name: str, provider: Optional[str] = None) -> bool:
    """
    Determines whether the specified LLM model supports native multimodal audio input directly
    (i.e. listening to raw audio via `inlineData` or `input_audio` content blocks).
    """
    if not model_name:
        return False

    m_lower = model_name.lower().strip()
    prov_lower = (provider or "").lower().strip()

    if "/" in m_lower and not prov_lower:
        prov_lower = m_lower.split("/", 1)[0]

    # Known text-only or vision-only models that do not accept native audio input
    non_audio_keywords = (
        "llama-3.3-70b", "llama-3.1-", "llama3.1", "llama3.3", "llama-3.2-",
        "deepseek-chat", "deepseek-r1", "deepseek-coder", "deepseek-v3",
        "qwen2.5-coder", "qwen-2.5-coder", "codestral",
        "o1-mini", "o3-mini", "gpt-3.5", "mistral-7b", "mixtral-8x",
        "claude-3", "claude-3.5", "claude-3-5", "claude-3.7", "claude-3-7",
        "claude-4", "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
        "pixtral", "llava", "bakllava", "moondream"
    )
    if any(k in m_lower for k in non_audio_keywords):
        return False

    # 1. Google Gemini models (1.5, 2.0, 2.5, 3.x, 4.x, Pro, Flash) across Antigravity, Google, OpenRouter, OpenCode, etc.
    if "gemini" in m_lower or "gemma-3n" in m_lower or "gemma3n" in m_lower:
        return True

    if prov_lower in ("antigravity", "google", "gemini") or m_lower.startswith(("antigravity/", "gemini/")):
        return True

    # 2. Native audio & omni model families across OpenAI, Qwen, Mistral, OpenRouter, KiloCode
    audio_families = (
        "gpt-4o-audio", "gpt-4o-mini-audio", "gpt-4o-realtime", "gpt-audio",
        "gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5", "chatgpt-4o",
        "qwen-audio", "qwen2-audio", "qwen2.5-omni", "qwen-omni",
        "voxtral", "ultravox", "step-audio", "glm-4-voice", "minicpm-o",
        "kilo/auto"
    )
    if any(af in m_lower for af in audio_families):
        return True

    # 3. Fallback to litellm.supports_audio_input
    pure_name = m_lower.split("/")[-1]
    try:
        import litellm
        if hasattr(litellm, "supports_audio_input"):
            if litellm.supports_audio_input(model=model_name):
                return True
            if "/" in model_name and litellm.supports_audio_input(model=pure_name):
                return True
    except Exception:
        pass

    return False


def extract_file_paths_from_text(text: str) -> List[str]:
    """
    Scans a user message for local file paths (file://..., /..., ~/...) that exist on disk
    and correspond to supported images, audio files, or documents.
    """
    if not text or not text.strip():
        return []

    found_paths: List[str] = []
    seen = set()

    # 1. Match quoted paths or file:// URIs or ~ / absolute paths
    patterns = [
        r'file://[^\s"\'<>]+',
        r'"((?:~|/)[^"\n]+)"',
        r"'((?:~|/)[^'\n]+)'",
        r'`((?:~|/)[^`\n]+)`',
        r'(?:^|\s)((?:~|/)(?:[^\s"\'`<>()\[\]]+|\\ )+)'
    ]

    for pat in patterns:
        for match in re.finditer(pat, text):
            candidate = match.group(1) if match.lastindex else match.group(0)
            candidate = candidate.strip().rstrip(".,;:!?)]>")
            if not candidate:
                continue
            try:
                resolved = resolve_local_path(candidate)
                if resolved.exists() and resolved.is_file():
                    s_path = str(resolved)
                    if s_path not in seen and (
                        is_image_file(resolved) or is_audio_file(resolved) or is_document_file(resolved)
                    ):
                        seen.add(s_path)
                        found_paths.append(s_path)
            except Exception:
                continue

    return found_paths


def _parse_image_dimensions_fallback(data: bytes) -> Tuple[int, int, str]:
    """Pure-Python header parser fallback for PNG, JPEG, GIF, WEBP, BMP dimensions."""
    if len(data) < 24:
        return 0, 0, "unknown"

    # PNG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h), "PNG"

    # GIF
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return int(w), int(h), "GIF"

    # BMP
    if data.startswith(b"BM") and len(data) >= 26:
        w, h = struct.unpack("<ii", data[18:26])
        return abs(int(w)), abs(int(h)), "BMP"

    # WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk_type = data[12:16]
        if chunk_type == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return int(w & 0x3FFF), int(h & 0x3FFF), "WEBP"
        elif chunk_type == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21:25]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return int(w), int(h), "WEBP"

    # JPEG
    if data.startswith(b"\xff\xd8"):
        idx = 2
        n = len(data)
        while idx < n - 9:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker == 0xFF:
                idx += 1
                continue
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[idx + 5:idx + 9])
                return int(w), int(h), "JPEG"
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            seg_len = struct.unpack(">H", data[idx + 2:idx + 4])[0]
            if seg_len < 2:
                break
            idx += 2 + seg_len
        return 0, 0, "JPEG"

    return 0, 0, "unknown"


def process_image(
    path: str | Path,
    max_dimension: int = 1568,
    include_base64: bool = True,
    extract_ocr: bool = False
) -> Dict[str, Any]:
    """
    Reads an image file from disk, extracts dimensions & format metadata,
    optionally downscales if larger than max_dimension (using GdkPixbuf),
    and encodes to a Base64 data URL suitable for multimodal LLMs.
    """
    p = resolve_local_path(str(path))
    if not p.exists() or not p.is_file():
        return {
            "status": "error",
            "error": f"El archivo de imagen no existe: {p}",
            "path": str(p)
        }

    size_bytes = p.stat().st_size
    ext = p.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(p))
    if not mime_type or not mime_type.startswith("image/"):
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".svg": "image/svg+xml",
            ".tiff": "image/tiff",
            ".tif": "image/tiff"
        }
        mime_type = mime_map.get(ext, "image/png")

    raw_bytes = p.read_bytes()
    width, height, fmt_name = _parse_image_dimensions_fallback(raw_bytes)
    if fmt_name == "unknown":
        fmt_name = ext.lstrip(".").upper() or "IMAGE"

    encoded_bytes = raw_bytes
    encoded_mime = mime_type
    was_resized = False

    # Use GdkPixbuf to accurately read dimensions and resize large images for LLM vision efficiency
    if ext != ".svg":
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(p))
            if pixbuf:
                width = pixbuf.get_width()
                height = pixbuf.get_height()
                if max_dimension and max(width, height) > max_dimension:
                    if width >= height:
                        new_w = max_dimension
                        new_h = max(1, int(height * (max_dimension / width)))
                    else:
                        new_h = max_dimension
                        new_w = max(1, int(width * (max_dimension / height)))
                    scaled = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
                    if scaled:
                        save_fmt = "jpeg" if ext in (".jpg", ".jpeg") else "png"
                        ok, buf = scaled.save_to_bufferv(save_fmt, [], [])
                        if ok and buf:
                            encoded_bytes = bytes(buf)
                            encoded_mime = "image/jpeg" if save_fmt == "jpeg" else "image/png"
                            was_resized = True
                elif ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    # Convert uncommon formats (BMP, TIFF, ICO) to PNG for universal LLM support
                    ok, buf = pixbuf.save_to_bufferv("png", [], [])
                    if ok and buf:
                        encoded_bytes = bytes(buf)
                        encoded_mime = "image/png"
        except Exception as e:
            logger.debug(f"GdkPixbuf processing fallback for {p}: {e}")

    result: Dict[str, Any] = {
        "status": "ok",
        "category": "image",
        "path": str(p),
        "filename": p.name,
        "format": fmt_name,
        "mime_type": encoded_mime,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "size_human": format_file_size(size_bytes),
        "was_resized": was_resized
    }

    if extract_ocr:
        ocr_text = _extract_image_ocr(p)
        if ocr_text:
            result["ocr_text"] = ocr_text

    if include_base64:
        b64_str = base64.b64encode(encoded_bytes).decode("ascii")
        result["base64"] = b64_str
        result["data_url"] = f"data:{encoded_mime};base64,{b64_str}"

    return result


def _extract_image_ocr(image_path: Path) -> str:
    """Attempts OCR text extraction if tesseract is installed, or reads SVG text nodes."""
    if image_path.suffix.lower() == ".svg":
        try:
            tree = ET.parse(str(image_path))
            texts = [t.strip() for t in tree.getroot().itertext() if t and t.strip()]
            return "\n".join(texts)
        except Exception:
            return ""

    tesseract_bin = shutil.which("tesseract")
    if tesseract_bin:
        try:
            proc = subprocess.run(
                [tesseract_bin, str(image_path), "stdout", "--oem", "1", "-l", "spa+eng"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if proc.returncode != 0:
                proc = subprocess.run(
                    [tesseract_bin, str(image_path), "stdout"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
    return ""


def format_duration(seconds: float) -> str:
    """Formats duration in seconds into a human-readable string (e.g., '01:24' or '4.2s')."""
    if not seconds or seconds <= 0:
        return ""
    if seconds < 10.0:
        return f"{seconds:.1f}s"
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _parse_audio_metadata(path: Path) -> Dict[str, Any]:
    """
    Extracts technical audio metadata (duration, sample_rate, channels, codec/format)
    from a local audio file using Python's `wave` module or `ffprobe` if available.
    """
    ext = path.suffix.lower()
    meta: Dict[str, Any] = {
        "duration_seconds": 0.0,
        "duration_human": "",
        "sample_rate": 0,
        "channels": 0,
    }

    # 1. Native fast WAV parser via standard library `wave`
    if ext == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                frames = wf.getnframes()
                duration = (frames / float(sample_rate)) if sample_rate > 0 else 0.0
                if channels > 0 and sample_rate > 0:
                    meta["channels"] = int(channels)
                    meta["sample_rate"] = int(sample_rate)
                    meta["duration_seconds"] = round(duration, 2)
                    meta["duration_human"] = format_duration(duration)
                    return meta
        except Exception:
            pass

    # 2. ffprobe metadata inspection for any audio format (MP3, OGG, OPUS, FLAC, M4A, AAC, WAV, etc.)
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        try:
            proc = subprocess.run(
                [
                    ffprobe_bin,
                    "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=sample_rate,channels,duration,codec_name:format=duration",
                    "-of", "json",
                    str(path)
                ],
                capture_output=True,
                text=True,
                timeout=8
            )
            if proc.returncode == 0 and proc.stdout.strip():
                probe_data = json.loads(proc.stdout)
                streams = probe_data.get("streams") or []
                fmt_obj = probe_data.get("format") or {}
                dur = 0.0
                if streams:
                    s0 = streams[0]
                    if s0.get("sample_rate"):
                        meta["sample_rate"] = int(s0["sample_rate"])
                    if s0.get("channels"):
                        meta["channels"] = int(s0["channels"])
                    if s0.get("duration"):
                        try:
                            dur = float(s0["duration"])
                        except Exception:
                            pass
                if not dur and fmt_obj.get("duration"):
                    try:
                        dur = float(fmt_obj["duration"])
                    except Exception:
                        pass
                if dur > 0:
                    meta["duration_seconds"] = round(dur, 2)
                    meta["duration_human"] = format_duration(dur)
        except Exception as e:
            logger.debug(f"ffprobe metadata extraction fallback for {path}: {e}")

    return meta


def process_audio(
    path: str | Path,
    include_base64: bool = True,
    target_provider: Optional[str] = None,
    target_model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Reads an audio file from disk, extracts technical metadata (duration, sample rate,
    channels, MIME type, format), optionally normalizes uncommon formats via ffmpeg when
    required by the target provider, and Base64-encodes the audio payload for direct
    multimodal LLM audio comprehension (`input_audio` / `inlineData`).
    """
    p = resolve_local_path(str(path))
    if not p.exists() or not p.is_file():
        return {
            "status": "error",
            "error": f"El archivo de audio no existe: {p}",
            "path": str(p)
        }

    size_bytes = p.stat().st_size
    ext = p.suffix.lower()

    mime_map = {
        ".wav": ("audio/wav", "wav", "WAV"),
        ".mp3": ("audio/mp3", "mp3", "MP3"),
        ".mpga": ("audio/mpeg", "mp3", "MP3"),
        ".mpeg": ("audio/mpeg", "mp3", "MP3"),
        ".ogg": ("audio/ogg", "ogg", "OGG"),
        ".oga": ("audio/ogg", "ogg", "OGG"),
        ".opus": ("audio/ogg", "ogg", "OPUS"),
        ".flac": ("audio/flac", "flac", "FLAC"),
        ".m4a": ("audio/mp4", "m4a", "M4A"),
        ".aac": ("audio/aac", "aac", "AAC"),
        ".webm": ("audio/webm", "webm", "WEBM"),
        ".aiff": ("audio/aiff", "aiff", "AIFF"),
        ".aif": ("audio/aiff", "aiff", "AIFF"),
        ".wma": ("audio/x-ms-wma", "wma", "WMA"),
        ".amr": ("audio/amr", "amr", "AMR"),
        ".3gp": ("audio/3gpp", "3gp", "3GP"),
    }

    if ext in mime_map:
        mime_type, audio_fmt, fmt_label = mime_map[ext]
    else:
        guessed_mime, _ = mimetypes.guess_type(str(p))
        mime_type = guessed_mime if (guessed_mime and guessed_mime.startswith("audio/")) else "audio/wav"
        audio_fmt = ext.lstrip(".") or "wav"
        fmt_label = audio_fmt.upper()

    audio_meta = _parse_audio_metadata(p)
    logger.info(
        f"Procesando archivo de audio: '{p.name}' ({format_file_size(size_bytes)}, "
        f"formato={fmt_label}, duración={audio_meta.get('duration_human') or 'N/A'}, "
        f"sample_rate={audio_meta.get('sample_rate')}Hz, canales={audio_meta.get('channels')})"
    )

    encoded_bytes: Optional[bytes] = None
    encoded_mime = mime_type
    encoded_format = audio_fmt
    was_converted = False

    # Determine if provider requires strict WAV/MP3 conversion or if file is too large for inline base64 upload
    # (e.g. > 4 MB uncompressed WAV/AIFF/FLAC mixes that would exceed HTTP inline timeouts)
    prov_lower = (target_provider or "").lower()
    mod_lower = (target_model or "").lower()
    is_gemini_target = (
        "gemini" in mod_lower
        or prov_lower in ("antigravity", "google", "gemini")
        or mod_lower.startswith(("antigravity/", "gemini/"))
    )

    needs_conversion = False
    is_large_audio = size_bytes > (4 * 1024 * 1024) or (
        ext in (".wav", ".aiff", ".aif", ".flac") and size_bytes > (2 * 1024 * 1024)
    )
    if is_large_audio:
        needs_conversion = True
    elif ext in (".wma", ".amr", ".3gp"):
        needs_conversion = True
    elif not is_gemini_target and audio_fmt not in ("wav", "mp3"):
        # OpenAI / generic OpenAI-compatible `input_audio` endpoints require 'wav' or 'mp3'
        needs_conversion = True

    if needs_conversion and include_base64:
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            import time as _time
            t0 = _time.time()
            logger.info(
                f"Optimizando audio '{p.name}' ({format_file_size(size_bytes)}) con ffmpeg "
                "para transmisión multimodal ultrarrápida..."
            )
            try:
                # 1. If targeting Gemini/Antigravity, Opus/OGG provides the highest fidelity at minimal size in ~1.2s
                if is_gemini_target:
                    proc_opus = subprocess.run(
                        [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", str(p),
                            "-vn",
                            "-c:a", "libopus", "-b:a", "64k",
                            "-f", "ogg", "pipe:1"
                        ],
                        capture_output=True,
                        timeout=15
                    )
                    if proc_opus.returncode == 0 and proc_opus.stdout:
                        encoded_bytes = proc_opus.stdout
                        encoded_mime = "audio/ogg"
                        encoded_format = "ogg"
                        was_converted = True
                        elapsed = _time.time() - t0
                        logger.info(
                            f"Audio optimizado en {elapsed:.2f}s: {format_file_size(size_bytes)} -> "
                            f"{format_file_size(len(encoded_bytes))} (Opus/OGG 64k)"
                        )

                # 2. If not Gemini or Opus unavailable, use high-speed CBR MP3 128k (universal across OpenAI and Gemini)
                if not was_converted:
                    proc_mp3 = subprocess.run(
                        [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", str(p),
                            "-vn", "-ar", "24000", "-ac", "2",
                            "-c:a", "libmp3lame", "-b:a", "128k",
                            "-f", "mp3", "pipe:1"
                        ],
                        capture_output=True,
                        timeout=15
                    )
                    if proc_mp3.returncode == 0 and proc_mp3.stdout:
                        encoded_bytes = proc_mp3.stdout
                        encoded_mime = "audio/mp3"
                        encoded_format = "mp3"
                        was_converted = True
                        elapsed = _time.time() - t0
                        logger.info(
                            f"Audio optimizado en {elapsed:.2f}s: {format_file_size(size_bytes)} -> "
                            f"{format_file_size(len(encoded_bytes))} (MP3 128k 24kHz estéreo)"
                        )

                # 3. Ultimate ultra-fast fallback: WAV 16kHz mono (takes ~0.1s in RAM)
                if not was_converted:
                    proc_wav = subprocess.run(
                        [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", str(p),
                            "-vn", "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le",
                            "-f", "wav", "pipe:1"
                        ],
                        capture_output=True,
                        timeout=15
                    )
                    if proc_wav.returncode == 0 and proc_wav.stdout:
                        encoded_bytes = proc_wav.stdout
                        encoded_mime = "audio/wav"
                        encoded_format = "wav"
                        was_converted = True
                        elapsed = _time.time() - t0
                        logger.info(
                            f"Audio normalizado en {elapsed:.2f}s: {format_file_size(size_bytes)} -> "
                            f"{format_file_size(len(encoded_bytes))} (WAV 16kHz mono)"
                        )
            except Exception as e:
                logger.warning(f"ffmpeg audio optimization fallback for {p}: {e}")

    if encoded_bytes is None:
        encoded_bytes = p.read_bytes()

    result: Dict[str, Any] = {
        "status": "ok",
        "category": "audio",
        "path": str(p),
        "filename": p.name,
        "name": p.name,
        "format": fmt_label,
        "audio_format": encoded_format,
        "mime_type": encoded_mime,
        "duration_seconds": audio_meta.get("duration_seconds", 0.0),
        "duration_human": audio_meta.get("duration_human", ""),
        "sample_rate": audio_meta.get("sample_rate", 0),
        "channels": audio_meta.get("channels", 0),
        "size_bytes": size_bytes,
        "size_human": format_file_size(size_bytes),
        "encoded_size_bytes": len(encoded_bytes),
        "encoded_size_human": format_file_size(len(encoded_bytes)),
        "was_converted": was_converted
    }

    if include_base64:
        b64_str = base64.b64encode(encoded_bytes).decode("ascii")
        result["base64"] = b64_str
        result["data_url"] = f"data:{encoded_mime};base64,{b64_str}"
        result["input_audio"] = {
            "data": b64_str,
            "format": encoded_format,
            "mime_type": encoded_mime
        }

    return result


class _SimpleHTMLTextExtractor(HTMLParser):
    """Lightweight HTML to readable Markdown/text converter."""
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._in_ignored = False

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript", "head"):
            self._in_ignored = True
        elif t in ("p", "div", "section", "article", "tr", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")
            if t == "li":
                self.parts.append("- ")
            elif t.startswith("h") and len(t) == 2 and t[1].isdigit():
                self.parts.append("#" * int(t[1]) + " ")
        elif t in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ("script", "style", "noscript", "head"):
            self._in_ignored = False
        elif t in ("p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"):
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._in_ignored and data:
            self.parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _extract_pdf(path: Path, start_page: int = 1, end_page: Optional[int] = None) -> Tuple[str, Dict[str, Any]]:
    """Extracts text and metadata from a PDF file using pdftotext (Poppler) or pypdf fallback."""
    meta: Dict[str, Any] = {"format": "PDF"}
    total_pages = 0

    # 1. Try pdfinfo for page count
    pdfinfo_bin = shutil.which("pdfinfo")
    if pdfinfo_bin:
        try:
            info_proc = subprocess.run([pdfinfo_bin, str(path)], capture_output=True, text=True, timeout=8)
            if info_proc.returncode == 0:
                for line in info_proc.stdout.splitlines():
                    if line.lower().startswith("pages:"):
                        total_pages = int(line.split(":", 1)[1].strip())
                    elif line.lower().startswith("title:"):
                        val = line.split(":", 1)[1].strip()
                        if val:
                            meta["title"] = val
        except Exception:
            pass

    if not total_pages:
        try:
            raw_pdf = path.read_bytes()
            matches = re.findall(rb"/Type\s*/Page\b", raw_pdf)
            if matches:
                total_pages = len(matches)
        except Exception:
            pass

    if total_pages:
        meta["total_pages"] = total_pages

    # 2. Extract text with pdftotext
    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        cmd = [pdftotext_bin, "-layout"]
        if start_page and start_page > 1:
            cmd.extend(["-f", str(start_page)])
        if end_page and end_page >= start_page:
            cmd.extend(["-l", str(end_page)])
        cmd.extend([str(path), "-"])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                text = proc.stdout
                # Split by form feed \x0c to label pages
                pages = text.split("\x0c")
                formatted_pages = []
                p_num = max(1, start_page)
                for p_content in pages:
                    cleaned_p = p_content.strip()
                    if cleaned_p:
                        formatted_pages.append(f"--- [Página {p_num}] ---\n{cleaned_p}")
                    p_num += 1
                return "\n\n".join(formatted_pages), meta
        except Exception as e:
            logger.warning(f"pdftotext failed on {path}: {e}")

    # 3. Fallback: pypdf / PyPDF2 if installed
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            reader = mod.PdfReader(str(path))
            num_p = len(reader.pages)
            meta["total_pages"] = num_p
            s_idx = max(0, start_page - 1)
            e_idx = min(num_p, end_page) if end_page else num_p
            out = []
            for idx in range(s_idx, e_idx):
                ptxt = (reader.pages[idx].extract_text() or "").strip()
                if ptxt:
                    out.append(f"--- [Página {idx + 1}] ---\n{ptxt}")
            return "\n\n".join(out), meta
        except ImportError:
            continue
        except Exception as e:
            logger.debug(f"{mod_name} fallback error: {e}")

    return "", meta


def _extract_docx(path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extracts paragraphs, headings, and tables from a .docx file using zipfile + XML."""
    meta: Dict[str, Any] = {"format": "DOCX"}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("Invalid DOCX archive: missing word/document.xml")
            xml_bytes = zf.read("word/document.xml")

        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        body = root.find("w:body", ns)
        if body is None:
            body = root

        blocks: List[str] = []
        table_count = 0

        for child in body:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                # Check paragraph style for headings
                style_val = ""
                pPr = child.find("w:pPr", ns)
                if pPr is not None:
                    pStyle = pPr.find("w:pStyle", ns)
                    if pStyle is not None:
                        style_val = pStyle.attrib.get(f"{{{ns['w']}}}val", "")
                texts = [t.text for t in child.findall(".//w:t", ns) if t.text]
                line = "".join(texts).strip()
                if line:
                    if style_val.lower().startswith("heading"):
                        lvl_digits = "".join(c for c in style_val if c.isdigit())
                        lvl = int(lvl_digits) if lvl_digits else 2
                        blocks.append(f"{'#' * min(6, max(1, lvl))} {line}")
                    else:
                        blocks.append(line)
            elif tag == "tbl":
                table_count += 1
                rows_md = []
                for r_idx, row in enumerate(child.findall("w:tr", ns)):
                    cells = []
                    for cell in row.findall("w:tc", ns):
                        c_texts = [t.text for t in cell.findall(".//w:t", ns) if t.text]
                        cells.append(" ".join("".join(c_texts).split()).replace("|", "\\|"))
                    if any(cells):
                        rows_md.append("| " + " | ".join(cells) + " |")
                        if r_idx == 0:
                            rows_md.append("| " + " | ".join(["---"] * len(cells)) + " |")
                if rows_md:
                    blocks.append("\n".join(rows_md))

        meta["tables_count"] = table_count
        return "\n\n".join(blocks), meta
    except Exception as e:
        # Fallback to pandoc if available
        pandoc_bin = shutil.which("pandoc")
        if pandoc_bin:
            try:
                proc = subprocess.run([pandoc_bin, str(path), "-t", "markdown"], capture_output=True, text=True, timeout=15)
                if proc.returncode == 0:
                    return proc.stdout.strip(), meta
            except Exception:
                pass
        raise RuntimeError(f"Error al extraer DOCX ({path.name}): {e}")


def _extract_odt(path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extracts text from OpenDocument Text (.odt)."""
    meta: Dict[str, Any] = {"format": "ODT"}
    pandoc_bin = shutil.which("pandoc")
    if pandoc_bin:
        try:
            proc = subprocess.run([pandoc_bin, str(path), "-t", "markdown"], capture_output=True, text=True, timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip(), meta
        except Exception:
            pass

    with zipfile.ZipFile(path, "r") as zf:
        xml_bytes = zf.read("content.xml")
    root = ET.fromstring(xml_bytes)
    lines = [t.strip() for t in root.itertext() if t and t.strip()]
    return "\n".join(lines), meta


def _extract_xlsx(path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extracts sheets and cells from an Excel (.xlsx) workbook as Markdown tables."""
    meta: Dict[str, Any] = {"format": "XLSX"}
    with zipfile.ZipFile(path, "r") as zf:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.iter():
                if si.tag.endswith("}si") or si.tag == "si":
                    parts = [t.text for t in si.iter() if (t.tag.endswith("}t") or t.tag == "t") and t.text]
                    shared_strings.append("".join(parts))

        # Sheet names from workbook.xml
        sheet_names: List[str] = []
        if "xl/workbook.xml" in zf.namelist():
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            for elem in wb_root.iter():
                if elem.tag.endswith("}sheet") or elem.tag == "sheet":
                    name = elem.attrib.get("name")
                    if name:
                        sheet_names.append(name)

        sheet_files = sorted(
            [n for n in zf.namelist() if re.match(r"^xl/worksheets/sheet\d+\.xml$", n)],
            key=lambda s: int(re.findall(r"\d+", s)[0])
        )
        meta["sheets_count"] = len(sheet_files)
        meta["sheets"] = sheet_names or [Path(sf).stem for sf in sheet_files]

        output_sections: List[str] = []
        for idx, sf in enumerate(sheet_files):
            s_title = sheet_names[idx] if idx < len(sheet_names) else f"Hoja {idx + 1}"
            s_root = ET.fromstring(zf.read(sf))
            rows_data: List[List[str]] = []
            max_cols = 0

            for row_elem in s_root.iter():
                if not (row_elem.tag.endswith("}row") or row_elem.tag == "row"):
                    continue
                row_cells: List[str] = []
                for c_elem in row_elem:
                    if not (c_elem.tag.endswith("}c") or c_elem.tag == "c"):
                        continue
                    c_type = c_elem.attrib.get("t", "")
                    val_text = ""
                    for child in c_elem:
                        if child.tag.endswith("}v") or child.tag == "v":
                            val_text = child.text or ""
                        elif child.tag.endswith("}is") or child.tag == "is":
                            val_text = "".join(t.text or "" for t in child.iter() if t.text)
                    if c_type == "s" and val_text.isdigit():
                        s_idx = int(val_text)
                        if 0 <= s_idx < len(shared_strings):
                            val_text = shared_strings[s_idx]
                    row_cells.append(val_text.strip().replace("\n", " ").replace("|", "\\|"))
                if any(row_cells):
                    max_cols = max(max_cols, len(row_cells))
                    rows_data.append(row_cells)

            if rows_data and max_cols > 0:
                md_lines = [f"### Hoja: {s_title} ({len(rows_data)} filas)"]
                for r_i, r_vals in enumerate(rows_data[:250]):
                    padded = r_vals + [""] * (max_cols - len(r_vals))
                    md_lines.append("| " + " | ".join(padded) + " |")
                    if r_i == 0:
                        md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
                if len(rows_data) > 250:
                    md_lines.append(f"\n*(... {len(rows_data) - 250} filas adicionales omitidas)*")
                output_sections.append("\n".join(md_lines))

        return "\n\n".join(output_sections), meta


def _extract_pptx(path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extracts slide text from a PowerPoint (.pptx) presentation."""
    meta: Dict[str, Any] = {"format": "PPTX"}
    with zipfile.ZipFile(path, "r") as zf:
        slide_files = sorted(
            [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
            key=lambda s: int(re.findall(r"\d+", s)[0])
        )
        meta["slides_count"] = len(slide_files)
        slides_out: List[str] = []
        for idx, sf in enumerate(slide_files, start=1):
            s_root = ET.fromstring(zf.read(sf))
            texts = [t.text.strip() for t in s_root.iter() if (t.tag.endswith("}t") or t.tag == "t") and t.text and t.text.strip()]
            if texts:
                slides_out.append(f"--- [Diapositiva {idx}] ---\n" + "\n".join(texts))
        return "\n\n".join(slides_out), meta


def _extract_csv(path: Path, delimiter: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Reads a CSV/TSV file and formats it as a Markdown table."""
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    sep = delimiter or ("\t" if path.suffix.lower() == ".tsv" else ",")
    reader = list(csv.reader(io.StringIO(raw_text), delimiter=sep))
    meta: Dict[str, Any] = {
        "format": "TSV" if sep == "\t" else "CSV",
        "total_rows": len(reader),
        "columns": reader[0] if reader else []
    }
    if not reader:
        return "", meta

    max_cols = max(len(r) for r in reader[:250]) if reader else 0
    md_lines: List[str] = []
    for r_i, row in enumerate(reader[:250]):
        padded = [c.strip().replace("\n", " ").replace("|", "\\|") for c in row] + [""] * (max_cols - len(row))
        md_lines.append("| " + " | ".join(padded) + " |")
        if r_i == 0:
            md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    if len(reader) > 250:
        md_lines.append(f"\n*(... {len(reader) - 250} filas adicionales omitidas de {len(reader)} totales)*")

    return "\n".join(md_lines), meta


def _extract_epub(path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extracts text from an EPUB document."""
    meta: Dict[str, Any] = {"format": "EPUB"}
    pandoc_bin = shutil.which("pandoc")
    if pandoc_bin:
        try:
            proc = subprocess.run([pandoc_bin, str(path), "-t", "markdown"], capture_output=True, text=True, timeout=20)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip(), meta
        except Exception:
            pass

    chapters: List[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        html_files = [n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        for hf in html_files:
            parser = _SimpleHTMLTextExtractor()
            parser.feed(zf.read(hf).decode("utf-8", errors="replace"))
            txt = parser.get_text()
            if txt:
                chapters.append(txt)
    return "\n\n".join(chapters), meta


def process_document(
    path: str | Path,
    start_page: int = 1,
    end_page: Optional[int] = None,
    max_chars: int = 30000,
    search_query: Optional[str] = None
) -> Dict[str, Any]:
    """
    Reads and extracts structured text, tables, and metadata from a local document.
    Supports PDF, DOCX, ODT, XLSX, CSV, TSV, PPTX, EPUB, HTML, RTF, JSON, YAML, Markdown, TXT, and source code.
    """
    p = resolve_local_path(str(path))
    if not p.exists() or not p.is_file():
        return {
            "status": "error",
            "error": f"El documento no existe en la ruta especificada: {p}",
            "path": str(p)
        }

    size_bytes = p.stat().st_size
    ext = p.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(p))

    try:
        if ext == ".pdf":
            extracted_text, doc_meta = _extract_pdf(p, start_page=start_page, end_page=end_page)
        elif ext == ".docx":
            extracted_text, doc_meta = _extract_docx(p)
        elif ext == ".odt":
            extracted_text, doc_meta = _extract_odt(p)
        elif ext == ".xlsx":
            extracted_text, doc_meta = _extract_xlsx(p)
        elif ext == ".pptx":
            extracted_text, doc_meta = _extract_pptx(p)
        elif ext in (".csv", ".tsv"):
            extracted_text, doc_meta = _extract_csv(p)
        elif ext == ".epub":
            extracted_text, doc_meta = _extract_epub(p)
        elif ext in (".html", ".htm"):
            raw_html = p.read_text(encoding="utf-8", errors="replace")
            parser = _SimpleHTMLTextExtractor()
            parser.feed(raw_html)
            extracted_text = parser.get_text()
            doc_meta = {"format": "HTML"}
        elif ext == ".rtf":
            pandoc_bin = shutil.which("pandoc")
            if pandoc_bin:
                proc = subprocess.run([pandoc_bin, str(p), "-t", "plain"], capture_output=True, text=True, timeout=15)
                extracted_text = proc.stdout.strip() if proc.returncode == 0 else p.read_text(encoding="utf-8", errors="replace")
            else:
                extracted_text = p.read_text(encoding="utf-8", errors="replace")
            doc_meta = {"format": "RTF"}
        else:
            # Plain text, Markdown, JSON, YAML, or Source Code
            try:
                extracted_text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                extracted_text = p.read_text(encoding="latin-1", errors="replace")
            doc_meta = {
                "format": ext.lstrip(".").upper() or "TEXT",
                "total_lines": extracted_text.count("\n") + 1
            }
    except Exception as e:
        logger.error(f"Error processing document {p}: {e}", exc_info=True)
        return {
            "status": "error",
            "error": f"Error al leer el documento '{p.name}': {str(e)}",
            "path": str(p),
            "filename": p.name
        }

    total_chars = len(extracted_text)

    # Apply search_query filter if requested
    search_matches_count = None
    if search_query and search_query.strip():
        q_lower = search_query.strip().lower()
        lines = extracted_text.splitlines()
        matched_windows: List[str] = []
        used_indices = set()
        matches_found = 0
        for i, line in enumerate(lines):
            if q_lower in line.lower():
                matches_found += 1
                win_start = max(0, i - 2)
                win_end = min(len(lines), i + 3)
                snippet_lines = []
                for idx in range(win_start, win_end):
                    if idx not in used_indices:
                        used_indices.add(idx)
                        snippet_lines.append(f"L{idx + 1}: {lines[idx]}")
                if snippet_lines:
                    matched_windows.append("\n".join(snippet_lines))
        search_matches_count = matches_found
        if matched_windows:
            extracted_text = (
                f"[Búsqueda de '{search_query}' en {p.name}: {matches_found} coincidencias encontradas]\n\n"
                + "\n---\n".join(matched_windows)
            )
        else:
            extracted_text = f"[No se encontraron coincidencias para '{search_query}' en {p.name}]"

    truncated = False
    if max_chars and max_chars > 0 and len(extracted_text) > max_chars:
        extracted_text = (
            extracted_text[:max_chars]
            + f"\n\n... [Contenido truncado a {max_chars} de {total_chars} caracteres totales. "
            f"Puedes usar media__read_document con start_page/end_page o search_query para consultar secciones específicas.]"
        )
        truncated = True

    res: Dict[str, Any] = {
        "status": "ok",
        "category": "document",
        "path": str(p),
        "filename": p.name,
        "extension": ext,
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "size_human": format_file_size(size_bytes),
        "total_chars": total_chars,
        "truncated": truncated,
        **doc_meta,
        "content": extracted_text
    }
    if search_matches_count is not None:
        res["search_matches"] = search_matches_count
    return res


def inspect_file(path: str | Path) -> Dict[str, Any]:
    """
    Inspects any local file and returns metadata about its type, size, format,
    and whether it can be processed as an image or document.
    """
    p = resolve_local_path(str(path))
    if not p.exists():
        return {
            "status": "error",
            "exists": False,
            "path": str(p),
            "error": f"El archivo no existe: {p}"
        }
    if p.is_dir():
        items = list(p.iterdir())
        return {
            "status": "ok",
            "exists": True,
            "is_directory": True,
            "path": str(p),
            "filename": p.name,
            "items_count": len(items),
            "sample_items": [x.name for x in items[:30]]
        }

    size_bytes = p.stat().st_size
    ext = p.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(p))

    if is_image_file(p):
        img_meta = process_image(p, include_base64=False, extract_ocr=False)
        img_meta["exists"] = True
        img_meta["is_image"] = True
        img_meta["is_audio"] = False
        img_meta["is_document"] = False
        return img_meta

    if is_audio_file(p):
        aud_meta = process_audio(p, include_base64=False)
        aud_meta["exists"] = True
        aud_meta["is_image"] = False
        aud_meta["is_audio"] = True
        aud_meta["is_document"] = False
        return aud_meta

    if is_document_file(p):
        info: Dict[str, Any] = {
            "status": "ok",
            "exists": True,
            "is_image": False,
            "is_audio": False,
            "is_document": True,
            "category": "document",
            "path": str(p),
            "filename": p.name,
            "extension": ext,
            "mime_type": mime_type or "text/plain",
            "size_bytes": size_bytes,
            "size_human": format_file_size(size_bytes)
        }
        if ext == ".pdf":
            _, pdf_meta = _extract_pdf(p, start_page=1, end_page=1)
            info.update(pdf_meta)
        return info

    return {
        "status": "ok",
        "exists": True,
        "is_image": False,
        "is_audio": False,
        "is_document": False,
        "category": "other",
        "path": str(p),
        "filename": p.name,
        "extension": ext,
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "size_human": format_file_size(size_bytes)
    }
