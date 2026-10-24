"""
Audio recording and transcription package for Pequeño Asistente.
Powered by faster-whisper and Kokoro TTS.
"""
from pequeno_asistente.audio.transcriber import AudioTranscriber
from pequeno_asistente.audio.tts import KokoroTTS, clean_markdown_for_speech, split_text_into_sentences

__all__ = ["AudioTranscriber", "KokoroTTS", "clean_markdown_for_speech", "split_text_into_sentences"]
