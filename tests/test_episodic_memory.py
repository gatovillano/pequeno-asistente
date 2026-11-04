import os
import json
import shutil
import tempfile
import datetime
import unittest
import asyncio
from pathlib import Path

from pequeno_asistente.agent.episodic_index import (
    EpisodicMemoryIndex,
    strip_accents,
    stem_word,
    parse_temporal_intent,
    extract_episode_metadata,
    is_trivial_interaction,
    summarize_assistant_response
)
from pequeno_asistente.agent.vector_engine import (
    DenseEmbeddingEngine,
    vector_to_blob,
    blob_to_vector,
    maximal_marginal_relevance
)
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.mcp_client.manager import MCPManager, MEMORY_TOOLS_DEFINITIONS
import numpy as np


class TestCognitiveEpisodicMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pa_episodic_test_"))
        self.memory_dir = self.temp_dir / "memory"
        self.threads_dir = self.temp_dir / "threads"
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        self.mem_mgr = MemoryManager(base_dir=str(self.memory_dir))
        MemoryManager._instance = self.mem_mgr

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stemming_and_diacritics(self):
        self.assertEqual(strip_accents("Configuración técnica en canción"), "Configuracion tecnica en cancion")
        # Inflected forms of configurar map to the same stem
        s1 = stem_word("configuración")
        s2 = stem_word("configurando")
        s3 = stem_word("configurar")
        self.assertEqual(s1, s2)
        self.assertEqual(s2, s3)

    def test_trivial_interaction_filtering(self):
        self.assertTrue(is_trivial_interaction("Hola!"))
        self.assertTrue(is_trivial_interaction("Muchas gracias"))
        self.assertTrue(is_trivial_interaction("ok"))
        self.assertFalse(is_trivial_interaction("Hola", tools_used=["hypruse__screenshot"]))
        self.assertFalse(is_trivial_interaction("Configura el servidor MCP de Bitwig Studio en ~/.config/pequeno-asistente/config.json"))

        # Logging a trivial greeting should be skipped
        res = self.mem_mgr.log_daily_interaction("Hola!", "¡Hola! ¿En qué puedo ayudarte hoy?")
        self.assertIsNone(res)
        self.assertEqual(self.mem_mgr.get_episodic_stats()["total_episodes"], 0)

    def test_entity_tag_and_importance_extraction(self):
        meta = extract_episode_metadata(
            user_msg="Solucionado el error de audio en PipeWire editando ~/.config/pipewire/pipewire.conf y ejecutando `systemctl --user restart pipewire`",
            assistant_msg="El fallo de latencia en PipeWire quedó corregido tras ajustar el quantum a 256.",
            tools_used=["desktop_bash__execute_command"]
        )
        self.assertEqual(meta["category"], "error_fix")
        self.assertIn("audio", meta["tags"])
        self.assertIn("desktop_bash", meta["tags"])
        self.assertTrue(any("pipewire.conf" in e for e in meta["entities"]))
        self.assertTrue(any("systemctl" in e for e in meta["entities"]))
        self.assertGreaterEqual(meta["importance"], 0.75)

    def test_hybrid_search_synonyms_and_diacritics(self):
        # Index an episode using English/technical terms ("screenshot", "hypruse")
        self.mem_mgr.episodic_index.index_interaction(
            date_str="2026-11-05",
            user_msg="Toma un screenshot del monitor principal en Hyprland",
            assistant_summary="Se capturó la imagen del escritorio con hypruse y se guardó en /home/gato/captura.png",
            tools_used=["hypruse__screenshot"]
        )
        # Index another unrelated episode
        self.mem_mgr.episodic_index.index_interaction(
            date_str="2026-11-05",
            user_msg="Crea una pista MIDI en Bitwig Studio con un sintetizador",
            assistant_summary="Pista Polysynth creada en el proyecto activo de Bitwig",
            tools_used=["bitwig__create_track"]
        )

        # Search using Spanish synonym ("captura de pantalla" -> matches "screenshot" / "imagen")
        results = self.mem_mgr.search_episodes("captura de pantalla")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("hypruse__screenshot", results[0]["tools_used"])

        # Search by tool_filter
        bitwig_res = self.mem_mgr.search_episodes("pista", tool_filter="bitwig")
        self.assertEqual(len(bitwig_res), 1)
        self.assertIn("bitwig__create_track", bitwig_res[0]["tools_used"])

    def test_temporal_intent_parsing_and_search(self):
        today = datetime.date(2026, 11, 7)
        yesterday = (today - datetime.timedelta(days=1)).isoformat()  # 2026-11-06
        two_days_ago = (today - datetime.timedelta(days=2)).isoformat()  # 2026-11-05

        t_ayer = parse_temporal_intent("¿Qué hicimos ayer por la tarde?", today=today)
        self.assertTrue(t_ayer["is_temporal_query"])
        self.assertEqual(t_ayer["date_from"], yesterday)
        self.assertEqual(t_ayer["date_to"], yesterday)

        t_anteayer = parse_temporal_intent(" qué configuramos anteayer ", today=today)
        self.assertEqual(t_anteayer["date_from"], two_days_ago)

        # Index an episode on yesterday's date that does NOT contain the word "ayer"
        self.mem_mgr.episodic_index.index_interaction(
            date_str=yesterday,
            user_msg="Optimiza las consultas SQL de la base de datos PostgreSQL",
            assistant_summary="Se crearon índices compuestos en la tabla de usuarios mejorando el tiempo en un 80%.",
            timestamp=f"{yesterday}T16:30:00"
        )
        # Index an episode from 20 days ago
        self.mem_mgr.episodic_index.index_interaction(
            date_str="2026-10-10",
            user_msg="Instala Docker en el servidor local",
            assistant_summary="Docker instalado correctamente."
        )

        # Query "¿qué hicimos ayer?" -> should find yesterday's PostgreSQL episode!
        hits = self.mem_mgr.episodic_index.search("¿Qué hicimos ayer?", today=today)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["date_str"], yesterday)
        self.assertIn("PostgreSQL", hits[0]["user_msg"])

    def test_spaced_repetition_reinforcement(self):
        ep = self.mem_mgr.save_episode(
            title="Configuración de túnel Cloudflare",
            summary="El túnel api-pa apunta al puerto 8765 mediante config_cloudflared_api-pa.yml",
            category="milestone",
            tags=["red", "cloudflare"]
        )
        sid = ep["source_id"]

        # First search reinforces access_count -> 1
        res1 = self.mem_mgr.search_episodes("túnel cloudflare")
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["source_id"], sid)
        self.assertEqual(res1[0]["access_count"], 1)

        # Second search reinforces access_count -> 2
        res2 = self.mem_mgr.search_episodes("túnel cloudflare")
        self.assertEqual(res2[0]["access_count"], 2)

    def test_sync_threads_and_deduplication(self):
        thread_file = self.threads_dir / "thread_20261107_120000_abcd.json"
        thread_data = {
            "id": "thread_20261107_120000_abcd",
            "title": "Depuración de Waybar",
            "created_at": "2026-11-07T12:00:00",
            "updated_at": "2026-11-07T12:05:00",
            "messages": [
                {"role": "user", "content": "Mi módulo personalizado de Waybar no muestra el icono de batería"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "desktop_bash__execute_command", "arguments": "{}"}}
                    ]
                },
                {"role": "tool", "tool_call_id": "c1", "name": "desktop_bash__execute_command", "content": "waybar config ok"},
                {"role": "assistant", "content": "Se corrigió el archivo ~/.config/waybar/config.jsonc añadiendo format-icons."}
            ]
        }
        thread_file.write_text(json.dumps(thread_data, ensure_ascii=False), encoding="utf-8")

        self.mem_mgr.sync_all()
        res = self.mem_mgr.search_episodes("icono de batería en waybar")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["thread_id"], "thread_20261107_120000_abcd")
        self.assertIn("desktop_bash__execute_command", res[0]["tools_used"])

    def test_consolidation_prunes_only_old_unaccessed_low_importance(self):
        # Old low-importance unaccessed interaction -> should be pruned
        self.mem_mgr.episodic_index.index_interaction(
            date_str="2026-08-01",
            user_msg="¿Qué hora es en Tokio?",
            assistant_summary="Son las 14:00.",
            importance=0.20
        )
        # Old high-importance milestone -> must be preserved
        self.mem_mgr.episodic_index.save_custom_episode(
            title="Arquitectura de seguridad sudo",
            summary="Implementado ASKPASS con custodia en memoria.",
            category="milestone",
            importance=0.90,
            date_str="2026-08-01"
        )

        self.assertEqual(self.mem_mgr.get_episodic_stats()["total_episodes"], 2)
        prune_res = self.mem_mgr.episodic_index.consolidate_old_episodes(days_threshold=30, min_importance_keep=0.35)
        self.assertEqual(prune_res["removed_count"], 1)
        self.assertEqual(self.mem_mgr.get_episodic_stats()["total_episodes"], 1)

    def test_mcp_manager_memory_tools_integration(self):
        mcp_mgr = MCPManager()
        tools = mcp_mgr.get_litellm_tools()
        tool_names = [t["function"]["name"] for t in tools]
        for expected in (
            "memory__search_episodes",
            "memory__save_episode",
            "memory__get_timeline",
            "memory__remember_fact",
            "memory__stats"
        ):
            self.assertIn(expected, tool_names)

        async def _run_mcp_memory_calls():
            # 1. Save an episode via MCP tool
            save_out = await mcp_mgr.call_tool("memory__save_episode", {
                "title": "Integración con KogniTerm completada",
                "summary": "Sincronizado el perfil de usuario y soporte de temas.",
                "category": "milestone",
                "tags": ["kogniterm", "integracion"],
                "importance": 0.95
            })
            save_data = json.loads(save_out)
            self.assertEqual(save_data["status"], "saved")

            # 2. Search for the episode via MCP tool
            search_out = await mcp_mgr.call_tool("memory__search_episodes", {
                "query": "integracion kogniterm",
                "tag": "kogniterm"
            })
            search_data = json.loads(search_out)
            self.assertEqual(search_data["count"], 1)
            self.assertEqual(search_data["episodes"][0]["title"], "Integración con KogniTerm completada")

            # 3. Remember a fact in MEMORY.md
            fact_out = await mcp_mgr.call_tool("memory__remember_fact", {
                "fact": "El servidor local de pruebas corre en el puerto 8765.",
                "target": "memory"
            })
            fact_data = json.loads(fact_out)
            self.assertEqual(fact_data["status"], "saved")
            self.assertIn("8765", self.mem_mgr.get_long_term_memory())

            # 4. Get stats
            stats_out = await mcp_mgr.call_tool("memory__stats", {"sync": True})
            stats_data = json.loads(stats_out)
            self.assertGreaterEqual(stats_data["total_episodes"], 2)
            self.assertEqual(stats_data["vectorized_episodes"], stats_data["total_episodes"])
            self.assertEqual(stats_data["vector_engine"]["dimensions"], 384)

            # 5. Find related episodes via dense vector cosine similarity
            ep2 = await mcp_mgr.call_tool("memory__save_episode", {
                "title": "Sincronización de temas visuales en KogniTerm",
                "summary": "Ajustada la paleta de colores y el perfil compartido de usuario.",
                "category": "milestone",
                "tags": ["kogniterm", "ui"]
            })
            ep2_id = json.loads(ep2)["episode"]["source_id"]
            rel_out = await mcp_mgr.call_tool("memory__find_related", {"source_id": ep2_id, "limit": 3})
            rel_data = json.loads(rel_out)
            self.assertGreaterEqual(rel_data["count"], 1)
            self.assertGreater(rel_data["related_episodes"][0]["vector_similarity"], 0.25)

        asyncio.run(_run_mcp_memory_calls())

    def test_dense_vector_engine_and_mmr(self):
        eng = DenseEmbeddingEngine.get_instance()
        v1 = eng.embed("Optimizar el uso de memoria RAM y CPU en Linux")
        v2 = eng.embed("Reducir consumo de recursos del procesador y memoria en el sistema")
        v3 = eng.embed("Receta de cocina para preparar pastel de chocolate")

        self.assertEqual(v1.shape, (384,))
        self.assertEqual(v1.dtype, np.float32)
        self.assertAlmostEqual(float(np.linalg.norm(v1)), 1.0, places=4)

        # Binary BLOB roundtrip
        blob = vector_to_blob(v1)
        self.assertEqual(len(blob), 384 * 4)
        v1_restored = blob_to_vector(blob, dim=384)
        self.assertTrue(np.allclose(v1, v1_restored))

        # Related technical sentences have higher cosine similarity than unrelated cooking sentence
        sim_related = float(np.dot(v1, v2))
        sim_unrelated = float(np.dot(v1, v3))
        self.assertGreater(sim_related, sim_unrelated)

        # Test MMR diversity: if candidate 0 and candidate 1 are identical vectors, MMR picks candidate 2 second
        cand_mat = np.vstack([v1, v1.copy(), v2])
        mmr_idx = maximal_marginal_relevance(v1, cand_mat, [0.95, 0.94, 0.85], top_k=2, lambda_mult=0.6)
        self.assertEqual(mmr_idx[0], 0)
        self.assertEqual(mmr_idx[1], 2)

    def test_timeline_deduplication_and_full_title(self):
        long_prompt = (
            "Configura la vista de las memorias en la ventana de configuración para que al hacer clic "
            "se despliegue el título completo y todo el contenido detallado sin truncar."
        )
        self.mem_mgr.log_daily_interaction(
            user_msg=long_prompt,
            assistant_summary="Se reemplazó Adw.ActionRow por Adw.ExpanderRow con soporte de expansión completa.",
            tools_used=["edit_file"]
        )
        # Force daily log sync to ensure get_timeline deduplicates live vs synced entries
        self.mem_mgr.sync_all()
        timeline = self.mem_mgr.get_timeline(limit=10)
        self.assertEqual(len(timeline), 1)
        ep = timeline[0]
        self.assertIn("…", ep["title"])
        self.assertEqual(ep["user_msg"], long_prompt)


if __name__ == "__main__":
    unittest.main()
