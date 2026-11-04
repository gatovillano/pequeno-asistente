---
name: skills-installer-skill
description: Guía procedimental para buscar, instalar, crear y configurar habilidades (skills) desde Claude Code, skills.sh, repositorios de GitHub, archivos locales o especificaciones del usuario.
category: system
tags: ["skills", "installation", "claude-code", "agent-skills", "config"]
---

# 🎯 Habilidad: Procedimiento para Instalar y Gestionar Habilidades (Skills)

Esta habilidad instruye al agente en el procedimiento riguroso para responder a solicitudes de instalación, búsqueda o creación de nuevas habilidades procedimentales (skills) para Pequeño Asistente, Claude Code o el estándar global de agentes.

---

## 1. Dónde Residen las Habilidades y Ecosistemas Soportados

El sistema integra y sincroniza automáticamente las habilidades desde los siguientes entornos:
1. **Pequeño Asistente (Usuario)**: `~/.config/pequeno-asistente/skills/` (destino por defecto para habilidades locales).
2. **Claude Code**: `~/.claude/skills/` (habilidades instaladas para Claude Code de Anthropic; si se instala aquí o se crea symlink, tanto Pequeño Asistente como Claude Code las reconocen inmediatamente).
3. **Estándar Global de Agentes (skills.sh)**: `~/.agents/skills/` (estándar abierto comunitario compartido).
4. **Sistema Operativo**: `/etc/agent-skills/`, `/usr/share/agent-skills/` y `/etc/pequeno-asistente/skills/`.
5. **Proyecto Actual / Workspace**: `./.claude/skills/`, `./.agents/skills/` o `./skills/`.

---

## 2. Herramientas Nativas Disponibles para el Agente

Dispones de herramientas nativas de primera clase para la gestión de habilidades:

- `skills__list`:
  - Lista todas las habilidades descubiertas, filtrando opcionalmente por procedencia (`source`: `"claude"`, `"user"`, `"agents_global"`, `"system"`, etc.) o por término de búsqueda (`query`).
- `skills__get`:
  - Obtiene el contenido completo en Markdown y metadatos de cualquier habilidad por su identificador (`skill_id`), ideal si requieres consultar el procedimiento completo antes de ejecutar una tarea especializada.
- `skills__install`:
  - Instala una habilidad automáticamente a partir de:
    - Una URL directa a un archivo Markdown (ej: `https://raw.githubusercontent.com/.../SKILL.md`).
    - Un repositorio de GitHub (ej: `https://github.com/usuario/mi-skill` o paquete `owner/repo`).
    - Una ruta local a un archivo `.md` o directorio existente.
    - Contenido Markdown directo con frontmatter.
    - Parámetro `target`: `"user"` (default, en `~/.config/pequeno-asistente/skills`), `"claude"` (`~/.claude/skills`) o `"agents"` (`~/.agents/skills`).
- `skills__uninstall`:
  - Desinstala una habilidad eliminando sus archivos del directorio de usuario o Claude Code.
- `skills__toggle`:
  - Activa o desactiva una habilidad sin eliminar sus archivos.

---

## 3. Procedimiento Paso a Paso según la Solicitud

### Caso A: El usuario pide instalar una habilidad desde una URL o GitHub
1. Si el usuario te proporciona un enlace de GitHub o raw URL:
   - Invoca `skills__install` con `source="<URL>"` y el nombre descriptivo correspondiente.
2. Si la habilidad debe quedar disponible también para Claude Code, indica `target="claude"`.
3. Informa al usuario el resultado, la ruta en disco donde quedó instalada y un resumen de las capacidades añadidas.

### Caso B: El usuario te pide crear o redactar una nueva habilidad personalizada
1. Redacta el contenido Markdown asegurándote de incluir el frontmatter YAML estandarizado al inicio:
   ```yaml
   ---
   name: nombre-de-la-habilidad
   description: Explicación concisa de qué hace la habilidad y exactamente cuándo debe activarse.
   tags: ["tag1", "tag2"]
   ---
   ```
2. Agrega la estructura procedimental clara (cuándo usar, prerrequisitos, pasos a seguir, ejemplos).
3. Invoca `skills__install` con `name="nombre-de-la-habilidad"` y `content="<contenido markdown completo>"`.
4. La habilidad quedará inmediatamente registrada y activa para responder a futuras consultas.

### Caso C: El usuario te pide buscar habilidades comunitarias (skills.sh / Claude Code)
1. Si tienes herramientas de consola o de búsqueda web, puedes consultar habilidades populares en el catálogo comunitario:
   - `anthropics/skills`: habilidades oficiales para diseño de interfaces, procesamiento de documentos, etc.
   - `vercel-labs/agent-skills`: habilidades para React, Next.js, etc.
2. Puedes instalar paquetes comunitarios usando `skills__install` con `source="nombre-organizacion/repositorio"`.
