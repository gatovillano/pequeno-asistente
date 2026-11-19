import json
import os
import struct
import time
import unittest
from unittest.mock import MagicMock

from pequeno_asistente.audio.wake_word import WakeWordDetector, parse_wake_phrases, strip_wake_prefix
from pequeno_asistente.config import DEFAULT_CONFIG


class TestWakeWord(unittest.TestCase):
    def test_parse_wake_phrases_adds_ok_alias(self):
        expected = ["okay asistente", "ok asistente", "okey asistente"]
        self.assertEqual(parse_wake_phrases("okay asistente"), expected)
        self.assertEqual(
            parse_wake_phrases("ok asistente"),
            ["ok asistente", "okay asistente", "okey asistente"],
        )

    def test_parse_wake_phrases_empty_fallback(self):
        self.assertEqual(
            parse_wake_phrases("   "),
            ["okay asistente", "ok asistente", "okey asistente"],
        )

    def test_parse_wake_phrases_multiple(self):
        self.assertEqual(
            parse_wake_phrases("okay asistente, hola gato"),
            ["okay asistente", "ok asistente", "okey asistente", "hola gato"],
        )

    def test_match_and_json_processing(self):
        cb = MagicMock()
        det = WakeWordDetector(on_detected=cb, phrase="okay asistente")

        self.assertTrue(det._process_recognizer_json(json.dumps({"partial": "okay asistente"}), "partial"))
        self.assertTrue(det._process_recognizer_json(json.dumps({"text": "ok asistente"}), "text"))
        self.assertFalse(det._process_recognizer_json(json.dumps({"text": "[unk]"}), "text"))
        self.assertFalse(det._process_recognizer_json("not-json", "text"))

    def test_cooldown_prevents_double_trigger(self):
        cb = MagicMock()
        det = WakeWordDetector(on_detected=cb, phrase="okay asistente", cooldown_seconds=5.0)
        rec = MagicMock()

        self.assertTrue(det._maybe_trigger(rec))
        self.assertFalse(det._maybe_trigger(rec))
        cb.assert_called_once()
        rec.Reset.assert_not_called()

    def test_default_config_has_wake_word_keys(self):
        audio = DEFAULT_CONFIG["audio"]
        self.assertIn("wake_word_enabled", audio)
        self.assertIn("wake_word_phrase", audio)
        self.assertEqual(audio["wake_word_phrase"], "okay asistente")

    def test_command_ignored_without_wake_word(self):
        wake_cb = MagicMock()
        new_chat_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"nuevo chat": new_chat_cb, "abre nuevo chat": new_chat_cb},
        )
        rec = MagicMock()

        self.assertFalse(det._handle_transcript("nuevo chat", rec))
        new_chat_cb.assert_not_called()
        wake_cb.assert_not_called()

    def test_command_triggers_within_window_after_wake_word(self):
        wake_cb = MagicMock()
        new_chat_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"nuevo chat": new_chat_cb, "abre nuevo chat": new_chat_cb},
            command_window_seconds=6.0,
        )
        rec = MagicMock()

        self.assertTrue(det._handle_transcript("okay asistente", rec, is_final=True))
        wake_cb.assert_called_once()
        new_chat_cb.assert_not_called()

        # Partial command should not fire prematurely
        self.assertFalse(det._handle_transcript("abre nuevo chat", rec, is_final=False))
        new_chat_cb.assert_not_called()

        # Final command triggers
        self.assertTrue(det._handle_transcript("abre nuevo chat", rec, is_final=True))
        new_chat_cb.assert_called_once()

        # Window closes after command executes, so a second command alone does not fire
        self.assertFalse(det._handle_transcript("nuevo chat", rec, is_final=True))
        new_chat_cb.assert_called_once()

    def test_combined_wake_and_command_triggers_both(self):
        wake_cb = MagicMock()
        new_chat_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"nuevo chat": new_chat_cb},
        )
        rec = MagicMock()

        self.assertTrue(det._handle_transcript("okay asistente nuevo chat", rec, is_final=True))
        wake_cb.assert_called_once()
        new_chat_cb.assert_called_once()

    def test_partial_wake_requires_streak_and_rejects_low_confidence(self):
        wake_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            partial_streak_required=3,
            min_word_confidence=0.65,
        )
        rec = MagicMock()

        # 1st and 2nd partials do not trigger (prevents single-frame flicker)
        self.assertFalse(det._handle_transcript("okay asistente", rec, is_final=False))
        self.assertFalse(det._handle_transcript("okay asistente", rec, is_final=False))
        wake_cb.assert_not_called()

        # 3rd consecutive partial triggers once
        self.assertTrue(det._handle_transcript("okay asistente", rec, is_final=False))
        wake_cb.assert_called_once()

        # Final in same utterance does not double-trigger
        self.assertFalse(det._handle_transcript("okay asistente", rec, is_final=True))
        wake_cb.assert_called_once()

        # Low-confidence final result is rejected
        det._last_trigger_time = 0.0
        low_conf = [{"word": "okay", "conf": 0.30}, {"word": "asistente", "conf": 0.60}]
        self.assertFalse(det._handle_transcript("okay asistente", rec, is_final=True, words=low_conf))
        wake_cb.assert_called_once()

    def test_grammar_includes_spanish_distractors(self):
        det = WakeWordDetector(on_detected=MagicMock(), phrase="okay asistente")
        grammar = json.loads(det._build_grammar())
        self.assertIn("okay asistente", grammar)
        self.assertIn("hola", grammar)
        self.assertIn("[unk]", grammar)

    def test_verify_wake_audio_rejects_false_positive_and_accepts_real(self):
        wake_cb = MagicMock()
        verdict = [False]
        verified_paths = []

        def verifier(path: str) -> bool:
            verified_paths.append(path)
            if os.path.exists(path):
                os.unlink(path)
            return verdict[0]

        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            partial_streak_required=1,
            verify_wake_audio=verifier,
        )
        rec = MagicMock()
        det._recent_chunks.append(b"\x01\x00" * 1600)

        # Rejected candidate must not trigger on_detected
        det._handle_transcript("okay asistente", rec, is_final=False)
        for _ in range(20):
            if verified_paths:
                break
            time.sleep(0.02)
        self.assertEqual(len(verified_paths), 1)
        wake_cb.assert_not_called()

        # Confirmed candidate triggers on_detected
        verdict[0] = True
        det._last_verify_time = 0.0
        det._wake_triggered_in_utterance = False
        det._handle_transcript("okay asistente", rec, is_final=True)
        for _ in range(20):
            if len(verified_paths) >= 2 and wake_cb.called:
                break
            time.sleep(0.02)
        wake_cb.assert_called_once()

    def test_inline_freeform_dispatches_utterance_immediately(self):
        wake_cb = MagicMock()
        dispatched = []
        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            on_freeform_audio=lambda p: dispatched.append(p),
        )
        rec = MagicMock()
        det._utterance_chunks.append(b"\x01\x00" * 1600)

        self.assertTrue(det._handle_transcript("okay asistente abre bitwig studio", rec, is_final=True))
        wake_cb.assert_called_once()
        for _ in range(20):
            if dispatched:
                break
            time.sleep(0.02)
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(os.path.exists(dispatched[0]))
        os.unlink(dispatched[0])

    def test_partial_or_other_target_does_not_trigger_hide(self):
        hide_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=MagicMock(),
            phrase="okay asistente",
            commands={"ocultar ventana": hide_cb, "cultar ventana": hide_cb},
        )
        rec = MagicMock()
        det._handle_transcript("okay asistente", rec, is_final=True)
        self.assertFalse(det._handle_transcript("cierra antigravity", rec, is_final=True))
        self.assertFalse(det._handle_transcript("ocultar ventana de firefox", rec, is_final=True))
        hide_cb.assert_not_called()

        # Whisper artifacts like leading 'o ' or '¿Cúltar ventana?' should still match
        self.assertEqual(det._match_command("o ocultar ventana"), "ocultar ventana")
        self.assertEqual(det._match_command("¿Cúltar ventana?"), "cultar ventana")

    def test_strip_wake_prefix(self):
        phrases = ["okay asistente", "ok asistente"]
        self.assertEqual(strip_wake_prefix("Okay, asistente, abre Bitwig Studio", phrases), "abre Bitwig Studio")
        self.assertEqual(strip_wake_prefix("ok asistente qué hora es", phrases), "qué hora es")
        self.assertEqual(strip_wake_prefix("abre Bitwig Studio", phrases), "abre Bitwig Studio")

    def test_freeform_silence_cancels_without_dispatch(self):
        freeform_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=MagicMock(),
            on_freeform_audio=freeform_cb,
        )
        rec = MagicMock()
        det._handle_transcript("okay asistente", rec, is_final=True)
        silent_chunk = b"\x00\x00" * 1600
        for _ in range(36):
            det._process_freeform_chunk(silent_chunk, rec)
        freeform_cb.assert_not_called()

    def test_freeform_speech_then_silence_dispatches_wav(self):
        dispatched = []
        det = WakeWordDetector(
            on_detected=MagicMock(),
            on_freeform_audio=lambda p: dispatched.append(p),
            speech_rms_threshold=300.0,
        )
        rec = MagicMock()
        det._handle_transcript("okay asistente", rec, is_final=True)
        loud_chunk = struct.pack("<" + "h" * 1600, *([1500] * 1600))
        silent_chunk = b"\x00\x00" * 1600
        for _ in range(3):
            det._process_freeform_chunk(loud_chunk, rec)
        for _ in range(10):
            det._process_freeform_chunk(silent_chunk, rec)

        # Wait briefly for daemon thread dispatch
        for _ in range(20):
            if dispatched:
                break
            time.sleep(0.02)
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(os.path.exists(dispatched[0]))
        os.unlink(dispatched[0])

    def test_ok_variants_match_and_strip(self):
        det = WakeWordDetector(on_detected=MagicMock(), phrase="okay asistente")
        self.assertTrue(det._match_text("O.K. asistente"))
        self.assertTrue(det._match_text("oquei asistente"))
        self.assertTrue(det._match_text("o key asistente"))
        self.assertEqual(
            strip_wake_prefix("O.K., asistente, abre Firefox", det.phrases),
            "abre Firefox",
        )

    def test_verified_inline_instant_command_runs_without_freeform_or_show(self):
        wake_cb = MagicMock()
        hide_cb = MagicMock()
        freeform_cb = MagicMock()
        verified = []

        def verifier(path: str) -> bool:
            verified.append(path)
            if os.path.exists(path):
                os.unlink(path)
            return True

        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"ocultar ventana": hide_cb},
            on_freeform_audio=freeform_cb,
            verify_wake_audio=verifier,
        )
        rec = MagicMock()
        det._recent_chunks.append(b"\x01\x00" * 1600)
        det._utterance_chunks.append(b"\x01\x00" * 1600)

        det._handle_transcript("okay asistente ocultar ventana", rec, is_final=True)
        for _ in range(30):
            if hide_cb.called:
                break
            time.sleep(0.02)

        self.assertEqual(len(verified), 1)
        hide_cb.assert_called_once()
        wake_cb.assert_not_called()
        freeform_cb.assert_not_called()

    def test_verified_inline_agent_message_dispatches_freeform(self):
        wake_cb = MagicMock()
        hide_cb = MagicMock()
        dispatched = []

        def verifier(path: str) -> bool:
            if os.path.exists(path):
                os.unlink(path)
            return True

        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"ocultar ventana": hide_cb},
            on_freeform_audio=lambda p: dispatched.append(p),
            verify_wake_audio=verifier,
        )
        rec = MagicMock()
        det._recent_chunks.append(b"\x01\x00" * 1600)
        det._utterance_chunks.append(b"\x01\x00" * 1600)

        det._handle_transcript("okay asistente abre firefox", rec, is_final=True)
        for _ in range(30):
            if dispatched:
                break
            time.sleep(0.02)

        wake_cb.assert_called_once()
        hide_cb.assert_not_called()
        self.assertEqual(len(dispatched), 1)
        if os.path.exists(dispatched[0]):
            os.unlink(dispatched[0])

    def test_rejected_partial_does_not_open_freeform_or_block_final(self):
        wake_cb = MagicMock()
        freeform_cb = MagicMock()
        verdict = [False]
        verified = []

        def verifier(path: str) -> bool:
            verified.append(path)
            if os.path.exists(path):
                os.unlink(path)
            return verdict[0]

        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            on_freeform_audio=freeform_cb,
            verify_wake_audio=verifier,
            partial_streak_required=1,
        )
        rec = MagicMock()
        det._recent_chunks.append(b"\x01\x00" * 1600)

        det._handle_transcript("okay asistente", rec, is_final=False)
        for _ in range(20):
            if verified:
                break
            time.sleep(0.02)
        self.assertFalse(det._capturing_freeform)
        wake_cb.assert_not_called()
        freeform_cb.assert_not_called()

        # Subsequent final in same utterance can still verify and trigger
        verdict[0] = True
        det._last_verify_time = 0.0
        det._handle_transcript("okay asistente", rec, is_final=True)
        for _ in range(20):
            if wake_cb.called:
                break
            time.sleep(0.02)
        wake_cb.assert_called_once()

    def test_whisper_transcript_tuple_routes_command_or_agent_message(self):
        wake_cb = MagicMock()
        hide_cb = MagicMock()
        freeform_texts = []
        transcripts = ["o que ya existente ocultar ventana", "ok asistente abre firefox"]

        def verifier(path: str):
            if os.path.exists(path):
                os.unlink(path)
            return (True, transcripts.pop(0))

        det = WakeWordDetector(
            on_detected=wake_cb,
            phrase="okay asistente",
            commands={"ocultar ventana": hide_cb},
            on_freeform_text=lambda txt: freeform_texts.append(txt),
            verify_wake_audio=verifier,
            verify_on_partial=False,
        )
        rec = MagicMock()
        det._recent_chunks.append(b"\x01\x00" * 1600)
        det._utterance_chunks.append(b"\x01\x00" * 1600)

        # Partial must not trigger verification when verify_on_partial=False
        self.assertFalse(det._handle_transcript("okay asistente", rec, is_final=False))
        hide_cb.assert_not_called()

        # Final with Vosk only hearing 'okay asistente', Whisper hears 'o que ya existente ocultar ventana'
        det._handle_transcript("okay asistente", rec, is_final=True)
        for _ in range(30):
            if hide_cb.called:
                break
            time.sleep(0.02)
        hide_cb.assert_called_once()
        wake_cb.assert_not_called()
        self.assertEqual(freeform_texts, [])

        # Next utterance: Whisper hears 'ok asistente abre firefox' -> sends 'abre firefox' to agent
        det._last_trigger_time = 0.0
        det._utterance_chunks.append(b"\x01\x00" * 1600)
        det._handle_transcript("okay asistente", rec, is_final=True)
        for _ in range(30):
            if freeform_texts:
                break
            time.sleep(0.02)
        wake_cb.assert_called_once()
        self.assertEqual(freeform_texts, ["abre firefox"])

    def test_acoustic_dtw_and_template_persistence(self):
        import tempfile
        import numpy as np
        from pequeno_asistente.audio.acoustic_wake import AcousticWakeMatcher, dtw_cosine_similarity

        with tempfile.TemporaryDirectory() as tmpdir:
            matcher = AcousticWakeMatcher(templates_dir=tmpdir, threshold=0.80)
            a = np.eye(4, 96, dtype=np.float32)
            b = np.eye(4, 96, dtype=np.float32)
            c = np.roll(b, 10, axis=1)
            self.assertAlmostEqual(dtw_cosine_similarity(a, b), 1.0, places=4)
            self.assertLess(dtw_cosine_similarity(a, c), 0.2)

            matcher.save_label_templates("wake", [a])
            matcher.save_label_templates("ocultar ventana", [c])

            reloaded = AcousticWakeMatcher(templates_dir=tmpdir, threshold=0.80)
            self.assertEqual(reloaded.template_counts(), {"ocultar ventana": 1, "wake": 1})

    def test_acoustic_wake_and_command_bypass_vosk_without_transcription(self):
        wake_cb = MagicMock()
        hide_cb = MagicMock()
        fake_matcher = MagicMock()
        fake_matcher.has_templates.return_value = True
        fake_matcher.templates = {"wake": [1], "ocultar ventana": [1]}

        det = WakeWordDetector(
            on_detected=wake_cb,
            commands={"ocultar ventana": hide_cb},
            acoustic_matcher=fake_matcher,
        )
        rec = MagicMock()

        # First chunk matches 'wake'
        fake_matcher.process_pcm_chunk.return_value = ("wake", 0.89)
        self.assertTrue(det._process_acoustic_chunk(b"\x10\x00" * 1600, rec))
        wake_cb.assert_called_once()
        hide_cb.assert_not_called()

        # Next chunk inside window matches 'ocultar ventana' -> executes locally
        fake_matcher.process_pcm_chunk.return_value = ("ocultar ventana", 0.91)
        self.assertTrue(det._process_acoustic_chunk(b"\x10\x00" * 1600, rec))
        hide_cb.assert_called_once()

    def test_custom_voice_commands_config_and_update_commands(self):
        self.assertIn("custom_voice_commands", DEFAULT_CONFIG["audio"])
        self.assertEqual(DEFAULT_CONFIG["audio"]["custom_voice_commands"], [])

        wake_cb = MagicMock()
        custom_cb = MagicMock()
        fake_matcher = MagicMock()
        fake_matcher.has_templates.return_value = True
        fake_matcher.templates = {"wake": [1], "abre firefox": [1]}

        det = WakeWordDetector(on_detected=wake_cb, acoustic_matcher=fake_matcher)
        det.update_commands({"abre firefox": custom_cb})
        rec = MagicMock()

        fake_matcher.process_pcm_chunk.return_value = ("wake", 0.90)
        det._process_acoustic_chunk(b"\x10\x00" * 1600, rec)

        fake_matcher.process_pcm_chunk.return_value = ("abre firefox", 0.88)
        det._process_acoustic_chunk(b"\x10\x00" * 1600, rec)
        custom_cb.assert_called_once()

    def test_orphaned_asistente_prefix_stripped_and_matches_hide(self):
        hide_cb = MagicMock()
        det = WakeWordDetector(
            on_detected=MagicMock(),
            phrase="okay asistente",
            commands={"ocultar ventana": hide_cb},
        )
        self.assertEqual(strip_wake_prefix("asistente ocultar ventana", det.phrases), "ocultar ventana")
        self.assertEqual(det._match_command("asistente ocultar ventana"), "ocultar ventana")


if __name__ == "__main__":
    unittest.main()
