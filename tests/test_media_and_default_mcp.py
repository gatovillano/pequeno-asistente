"""
Unit and integration tests for:
1. Default built-in MCP server (`pequeno-core`) connected by default on install.
2. Document and image processing (`media_processor.py`) and native MCP tools (`media__read_document`, `media__read_image`, `media__inspect_file`).
3. Automatic multimodal image & document attachment handling in `AgentCore`.
"""
import os
import json
import struct
import zlib
import zipfile
import tempfile
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from pequeno_asistente.config import ConfigManager, DEFAULT_MCP_SERVERS
from pequeno_asistente.mcp_client.catalog import MCP_CATALOG
from pequeno_asistente.mcp_client.default_server import (
    DEFAULT_MCP_SERVER_ID,
    ALL_DEFAULT_TOOLS_DEFINITIONS,
    execute_default_mcp_tool,
)
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.media_processor import (
    is_model_multimodal,
    extract_file_paths_from_text,
    process_image,
    process_document,
    inspect_file,
)
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks


def _create_sample_png(path: str, width: int = 32, height: int = 24):
    """Creates a valid minimal RGB PNG file with exact dimensions."""
    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + (b"\x40\x80\xff" * width) for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    with open(path, "wb") as f:
        f.write(header)
        f.write(png_chunk(b"IHDR", ihdr))
        f.write(png_chunk(b"IDAT", idat))
        f.write(png_chunk(b"IEND", b""))


def _create_sample_docx(path: str, paragraphs: list[str]):
    """Creates a minimal valid .docx file containing the given paragraphs."""
    body_xml = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)


def _create_sample_xlsx(path: str):
    """Creates a minimal valid .xlsx spreadsheet."""
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Producto</t></si><si><t>Precio</t></si><si><t>Teclado Mecánico</t></si>"
        "</sst>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>89.99</v></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


class TestMediaAndDefaultMCP(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = self.tmp_dir.name

    def tearDown(self):
        self.tmp_dir.cleanup()

    async def test_default_mcp_server_connected_by_default(self):
        """Verifies `pequeno-core` is configured and connected by default with all native tools."""
        self.assertIn(DEFAULT_MCP_SERVER_ID, DEFAULT_MCP_SERVERS)
        self.assertIn(DEFAULT_MCP_SERVER_ID, MCP_CATALOG)

        mcp = MCPManager()
        self.assertIn(DEFAULT_MCP_SERVER_ID, mcp.instances)
        self.assertEqual(mcp.instances[DEFAULT_MCP_SERVER_ID].status, "connected")

        litellm_tools = mcp.get_litellm_tools()
        tool_names = {t["function"]["name"] for t in litellm_tools}

        # Verify all tool families are exposed through the default MCP server
        expected_tools = {
            "media__read_document",
            "media__read_image",
            "media__inspect_file",
            "memory__search_episodes",
            "memory__save_episode",
            "memory__get_timeline",
            "memory__remember_fact",
            "memory__stats",
            "skills__list",
            "skills__get",
            "automation__list",
            "security__execute_sudo",
            "security__request_credential",
        }
        for name in expected_tools:
            self.assertIn(name, tool_names, f"Missing default MCP tool: {name}")

        # Verify server survives sync_servers({})
        await mcp.sync_servers({})
        self.assertIn(DEFAULT_MCP_SERVER_ID, mcp.instances)
        self.assertEqual(mcp.instances[DEFAULT_MCP_SERVER_ID].status, "connected")

    async def test_media_tools_read_documents_and_images(self):
        """Tests reading TXT, CSV, DOCX, XLSX, and PNG via MCPManager and media_processor."""
        mcp = MCPManager()

        # 1. Markdown / Text document
        md_path = os.path.join(self.base_path, "notas.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Proyecto Alfa\nEste documento describe la arquitectura multimodal.")

        res_md = json.loads(await mcp.call_tool("media__read_document", {"path": md_path}))
        self.assertEqual(res_md["status"], "ok")
        self.assertIn("Proyecto Alfa", res_md["content"])

        # 2. CSV document converted to Markdown table
        csv_path = os.path.join(self.base_path, "ventas.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Mes,Ingresos\nEnero,1500\nFebrero,2300\n")

        res_csv = json.loads(await mcp.call_tool("media__read_document", {"path": csv_path}))
        self.assertEqual(res_csv["status"], "ok")
        self.assertIn("Enero", res_csv["content"])
        self.assertIn("|", res_csv["content"])

        # 3. DOCX document
        docx_path = os.path.join(self.base_path, "informe.docx")
        _create_sample_docx(docx_path, ["Resumen Ejecutivo 2026", "Crecimiento sostenido del 42%."])
        res_docx = json.loads(await mcp.call_tool("media__read_document", {"path": docx_path}))
        self.assertEqual(res_docx["status"], "ok")
        self.assertIn("Resumen Ejecutivo 2026", res_docx["content"])
        self.assertIn("42%", res_docx["content"])

        # 4. XLSX spreadsheet
        xlsx_path = os.path.join(self.base_path, "inventario.xlsx")
        _create_sample_xlsx(xlsx_path)
        res_xlsx = json.loads(await mcp.call_tool("media__read_document", {"path": xlsx_path}))
        self.assertEqual(res_xlsx["status"], "ok")
        self.assertIn("Teclado Mecánico", res_xlsx["content"])
        self.assertIn("89.99", res_xlsx["content"])

        # 5. PNG image via media__read_image & process_image
        png_path = os.path.join(self.base_path, "grafico.png")
        _create_sample_png(png_path, width=64, height=48)
        res_img = json.loads(await mcp.call_tool("media__read_image", {"path": png_path}))
        self.assertEqual(res_img["status"], "ok")
        self.assertEqual(res_img["width"], 64)
        self.assertEqual(res_img["height"], 48)
        self.assertTrue(res_img["multimodal_ready"])

        raw_img = process_image(png_path, include_base64=True)
        self.assertTrue(raw_img["data_url"].startswith("data:image/png;base64,"))

        # 6. media__inspect_file
        res_insp = json.loads(await mcp.call_tool("media__inspect_file", {"path": png_path}))
        self.assertTrue(res_insp["exists"])
        self.assertTrue(res_insp["is_image"])
        self.assertEqual(res_insp["width"], 64)

    def test_multimodal_model_detection_and_path_extraction(self):
        """Tests multimodal model classification and path extraction from user prompts."""
        self.assertTrue(is_model_multimodal("antigravity/gemini-3-flash", "antigravity"))
        self.assertTrue(is_model_multimodal("antigravity/claude-sonnet-4-6", "antigravity"))
        self.assertTrue(is_model_multimodal("openai/gpt-4o", "openai"))
        self.assertTrue(is_model_multimodal("anthropic/claude-3-7-sonnet-latest", "anthropic"))
        self.assertTrue(is_model_multimodal("ollama/llava:latest", "ollama"))

        self.assertFalse(is_model_multimodal("openrouter/deepseek/deepseek-r1", "openrouter"))
        self.assertFalse(is_model_multimodal("openrouter/meta-llama/llama-3.3-70b-instruct", "openrouter"))

        png_path = os.path.join(self.base_path, "captura.png")
        _create_sample_png(png_path, 16, 16)
        detected = extract_file_paths_from_text(f"Por favor revisa esta imagen {png_path} y dime qué ves")
        self.assertIn(os.path.realpath(png_path), detected)

    async def test_agent_core_automatic_multimodal_and_document_processing(self):
        """Verifies AgentCore automatically injects image_url parts for multimodal models and extracts documents."""
        cfg = ConfigManager()
        cfg.data["llm"]["provider"] = "antigravity"
        cfg.data["llm"]["active_model"] = "antigravity/gemini-3-flash"
        cfg.data["memory"]["auto_log"] = False

        mem = MemoryManager(base_dir=self.base_path)
        th_mgr = ThreadManager(base_dir=self.base_path)
        mcp = MCPManager()
        mcp.memory_manager = mem
        mcp.config_manager = cfg
        agent = AgentCore(cfg, mcp, mem, th_mgr)

        png_path = os.path.join(self.base_path, "diagrama.png")
        _create_sample_png(png_path, width=40, height=30)

        doc_path = os.path.join(self.base_path, "especificacion.txt")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("Código secreto del documento: ZETA-9988.")

        captured_messages = []

        def fake_completion(model, messages, tools=None, stream=True, temperature=0.7, **kwargs):
            captured_messages.extend(messages)
            chunk = SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="He visto el diagrama y leído ZETA-9988.", tool_calls=None))]
            )
            yield chunk

        with patch("pequeno_asistente.agent.core.AntigravityClient.is_logged_in", return_value=True), \
             patch("pequeno_asistente.agent.core.AntigravityClient.completion", side_effect=fake_completion):

            completed_texts = []
            callbacks = AgentCallbacks(on_complete=lambda txt: completed_texts.append(txt))
            await agent.run_turn(
                user_message="Analiza estos archivos",
                callbacks=callbacks,
                attachments=[png_path, doc_path]
            )

        self.assertEqual(len(completed_texts), 1)
        user_msgs = [m for m in captured_messages if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
        content = user_msgs[0]["content"]

        # Because gemini-3-flash is multimodal, content must be a list containing text + image_url
        self.assertIsInstance(content, list)
        text_parts = [p for p in content if p.get("type") == "text"]
        img_parts = [p for p in content if p.get("type") == "image_url"]
        self.assertEqual(len(text_parts), 1)
        self.assertEqual(len(img_parts), 1)
        self.assertIn("ZETA-9988", text_parts[0]["text"])
        self.assertTrue(img_parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
