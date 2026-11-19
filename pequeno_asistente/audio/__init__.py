"""
Audio recording and transcription package for Pequeño Asistente.
Powered by faster-whisper and Kokoro TTS.
"""
from pequeno_asistente.audio.transcriber import AudioTranscriber
from pequeno_asistente.audio.tts import KokoroTTS, clean_markdown_for_speech, split_text_into_sentences
from pequeno_asistente.audio.wake_word import WakeWordDetector

__all__ = ["AudioTranscriber", "KokoroTTS", "WakeWordDetector", "clean_markdown_for_speech", "split_text_into_sentences"]
