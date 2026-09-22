# Biblioteca de Rol Pargen — CHANGELOG

Historial de cambios significativos del proyecto.

---

## [2026-09-22] — Versión 3.3: arrastre con ratón en el visor de PDF

### Contexto
El visor de PDF permite ampliar la página con el botón "+", pero al hacerlo en Mac/PC no había forma de desplazarse por ella: la rueda del ratón solo hacía zoom (con Ctrl/Cmd) y el arrastre para moverse solo estaba implementado para gestos táctiles (un dedo), que sí funcionaba en tablet.

### Corregido
- **Arrastrar con el botón izquierdo del ratón** para desplazarse por la página ampliada, igual que el gesto de un dedo en la tablet — con cursor de manita (🤚/✊) como pista visual.
- **Rueda del ratón/trackpad sin Ctrl** también desplaza la página cuando hay zoom (Ctrl+rueda sigue haciendo zoom, sin cambios).

### Ficheros modificados
- `app.py` — manejadores `mousedown`/`mousemove`/`mouseup` y `wheel` sin Ctrl en el visor de PDF (HTML_VIEWER)
- `VERSION` (`app.py`) — **3.2 → 3.3**

Subido a `pargenrol/biblioteca-rol` (rama `main`).

---

## [2026-08-30] — Versión 3.2: traducción con Claude, integración con Pantallasistemas y corrección de rutas personales

### Añadido
- **Claude como motor de traducción alternativo**, junto a Ollama y Argos Translate: nuevo botón "Claude" en el visor de PDF, streaming en vivo igual que con Ollama (llamada directa a la API de Anthropic, sin dependencia nueva). Activable con `ANTHROPIC_API_KEY` en `.env`; sin ella, sigue funcionando igual que siempre con Ollama/Argos. *Por ahora solo cubre la traducción página a página al vuelo — la cola de traducción completa en 2º plano sigue solo con Ollama.*
- **Botón "🔄 Generar RAG"** en la portada: pide a Pantallasistemas (si está instalado y accesible) que reindexe esta biblioteca para su asistente IA, con seguimiento de progreso. Es un proxy server-side hacia `POST /api/rag/reindex` de Pantallasistemas — sin ningún acoplamiento de código entre los dos proyectos, y con un error claro si Pantallasistemas no está corriendo. Configurable con `PANTALLASISTEMAS_URL` en `.env` (por defecto `http://localhost:5001`).
- `requirements.txt` (no existía) — Flask, PyMuPDF, argostranslate, telethon.

### Corregido — ruta personal hardcodeada (repositorio público)
`config.py` usaba mi vault real de Obsidian como valor por defecto de `OBSIDIAN_BIBLIOTECA`/`OBSIDIAN_PARTIDAS` — además de exponer la ruta, esto contradecía lo que dice el propio readme ("si no están configuradas, esas funciones quedan desactivadas"): un usuario nuevo sin `.env` habría intentado escribir en mi ruta en vez de desactivarse limpiamente. Ahora el valor por defecto es ninguno.

### Ficheros modificados
- `app.py` — motor Claude en `/translate/<path>`, endpoints `/generar-rag` y `/generar-rag/status`
- `config.py` — `PANTALLASISTEMAS_URL`, corrección de rutas por defecto
- `.env.example` — `ANTHROPIC_API_KEY`
- `requirements.txt` — nuevo
- `VERSION` (`app.py`) — **3.1 → 3.2**

Subido a `pargenrol/biblioteca-rol` (rama `main`).

---

## [2026-08-30] — Versión 3.1: primera versión portable

### Añadido
- Credenciales de Telegram movidas de hardcodeadas a `.env` / `.env.example`.
- `.gitignore` (el repo no era ni siquiera un repositorio git antes de esto).
- Rutas de vault de Obsidian configurables por `.env`, con error claro si faltan.
- **`POST /upload`** con arrastrar-y-soltar en la interfaz, para meter documentos propios sin necesitar acceso al Telegram del club.
- `SINTAXIS_MARKDOWN.md` documentando el formato que genera la app.
- Puerto configurable vía `PORT` (por defecto 8765, igual que antes).

### Corregido
- `biblioteca/` (gitignorada) no se creaba sola en un clon nuevo — crasheaba al listar.
- Faltaba `import os` en `app.py`.

Subido a `pargenrol/biblioteca-rol` (rama `main`), commits `c7f9c77`, `217b0e5`, `9598878`.

---
