# 📚 Biblioteca de Rol Pargen

Una aplicación web para gestionar y consultar una biblioteca de PDFs de rol: navegación por carpetas, búsqueda, visor integrado, traducción de páginas al vuelo (o de un libro entero en segundo plano) y generación de un esquema de preparación de partida a partir del texto de una aventura. Pensada como **proyecto complementario e independiente** de [Pantallasistemas](https://github.com/pargenrol/PantallaDigitalMaster) — funciona perfectamente sola, y si además tienes Pantallasistemas instalado, su asistente IA puede indexar y citar estos mismos PDFs (ver sección "Proyecto hermano" más abajo).

## ✨ Características

* **📂 Biblioteca navegable**: carpetas por sistema/tema, búsqueda por nombre, miniaturas de portada.
* **📖 Visor de PDF integrado** (PDF.js) con zoom y navegación táctil.
* **🌐 Traducción EN→ES**: página a página al vuelo (Ollama local, con Argos Translate offline como respaldo si Ollama no está disponible), o el libro completo en segundo plano con una cola persistente que sobrevive a reinicios.
* **📝 Notas a tu vault de Obsidian** (opcional): guarda traducciones y anotaciones directamente en tus notas, si configuras la ruta.
* **🧙 Esquema de preparación de partida**: a partir del texto de una aventura, genera 6 ficheros Markdown (visión general, PNJs, lugares, encuentros, loot, hoja de prep rápida) con IA local.
* **📤 Subida de documentos**: arrastra PDFs a la ventana o usa el botón "Subir" — no hace falta ningún bot ni canal de Telegram para meter contenido propio.

## 📂 Estructura del Proyecto

* `app.py`: servidor Flask (todas las rutas y plantillas HTML en un único fichero).
* `config.py`: rutas configurables (vault de Obsidian) — lee `.env` si existe.
* `translate_worker.py`: proceso independiente para la traducción completa en segundo plano.
* `descargar.py`, `listar_canales.py`: **scripts opcionales**, solo si quieres poblar tu biblioteca automáticamente desde tu propio canal de Telegram (requieren tus propias credenciales, ver más abajo). No hacen falta para usar la app — la vía normal de meter documentos es `/upload`.
* `biblioteca/`: tus PDFs — **vacía en el repositorio**, cada instalación pone la suya (ver "Nota Legal").
* `SINTAXIS_MARKDOWN.md`: qué formato generan las notas que se escriben a tu vault, si lo configuras.

## 🚀 Instalación y Uso

### Requisitos previos

* Python 3.11 o superior.
* (Opcional, solo para traducción/esquemas por IA) [Ollama](https://ollama.ai) corriendo en local con el modelo `qwen2.5:7b-instruct-q4_K_M`. Sin Ollama, la app funciona igual (biblioteca, visor, subida) — la traducción cae a Argos Translate si está instalado, o queda desactivada.
* No requiere ninguna base de datos — todo es sistema de ficheros.

### Instalación rápida

1. **Clona el repositorio.**
2. Instala dependencias: `pip3 install flask pymupdf argostranslate telethon` (o crea tu propio venv primero).
3. Copia `.env.example` a `.env` y rellena solo lo que necesites (ver siguiente sección).
4. Arranca: `python3 app.py` — la app abre en `http://localhost:8765`.

### Configurar `.env` (todo opcional)

| Variable | Para qué | Si no la pones |
|---|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_CANAL_ID` | Solo si usas `descargar.py`/`listar_canales.py` para poblar la biblioteca desde tu propio canal de Telegram | Esos dos scripts no arrancan (dan un error claro); el resto de la app funciona igual — usa `/upload` en su lugar |
| `OBSIDIAN_BIBLIOTECA` / `OBSIDIAN_PARTIDAS` | Rutas a tu vault de Obsidian, para guardar notas/traducciones/esquemas de partida ahí | Esas tres funciones concretas devuelven un error claro al usarlas; biblioteca, visor, traducción al vuelo y subida funcionan igual sin esto |

### Meter documentos en la biblioteca

La vía normal: **arrastra los PDF a la ventana**, o pulsa el botón "📤 Subir" — van a la carpeta que estés navegando en ese momento. No hace falta Telegram ni ningún canal externo.

---

## 🔗 Proyecto hermano: Pantallasistemas

Esta app y [Pantallasistemas](https://github.com/pargenrol/PantallaDigitalMaster) (pantalla digital de máster con asistente IA) son **proyectos independientes**, sin llamadas entre ellos — el único vínculo es que Pantallasistemas puede indexar (vía RAG) la carpeta `biblioteca/` de esta app para que su asistente responda citando tus PDFs, si le apuntas `BIBLIOTECA_PATH` a esta carpeta en su `config.py`. Puedes usar cualquiera de las dos por separado sin ningún problema.

---

**Nota Legal:** este repositorio no incluye ningún PDF — la carpeta `biblioteca/` viaja vacía (ver `.gitignore`). Cada instalación añade su propio material, bajo su propia responsabilidad respecto a derechos de autor.
