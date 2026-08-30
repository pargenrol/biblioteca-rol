# Manual de instalación y uso — Biblioteca de Rol Pargen

Guía completa para instalar tu propia copia y usarla. Para la lista de características y la nota legal sobre derechos de autor, ver [`readme.md`](readme.md); este documento se centra en el paso a paso.

## 1. Instalación

### Requisitos

* Python 3.11 o superior.
* No requiere ninguna base de datos — todo es sistema de ficheros.
* (Opcional, solo para traducción/esquemas por IA) [Ollama](https://ollama.ai) con el modelo `qwen2.5:7b-instruct-q4_K_M`, o una clave de Claude (Anthropic). Sin ninguna de las dos, la traducción cae a Argos Translate (offline) si está instalado, o queda desactivada — el resto de la app (biblioteca, visor, subida) funciona igual.

### Pasos

1. Clona el repositorio.
2. Crea un entorno virtual e instala dependencias:
   ```
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copia `.env.example` a `.env` y rellena solo lo que necesites (ver tabla abajo — todo es opcional).
4. Arranca: `python3 app.py` (o `./start.sh` en Mac/Linux) — la app abre en `http://localhost:8765` (puerto configurable con `PORT`).

### Configurar `.env` (todo opcional)

| Variable | Para qué | Si no la pones |
|---|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_CANAL_ID` | Solo si usas `descargar.py`/`listar_canales.py` para poblar la biblioteca desde tu propio canal de Telegram | Esos dos scripts no arrancan (dan un error claro); usa `/upload` en su lugar |
| `OBSIDIAN_BIBLIOTECA` / `OBSIDIAN_PARTIDAS` | Rutas a tu vault de Obsidian, para guardar traducciones/notas/esquemas de partida ahí | Esas funciones concretas quedan desactivadas con un error claro; biblioteca, visor, traducción al vuelo y subida funcionan igual |
| `ANTHROPIC_API_KEY` | Traducir con Claude en vez de/además de Ollama | Solo Ollama/Argos disponibles como motor de traducción |
| `PANTALLASISTEMAS_URL` | Si [Pantallasistemas](https://github.com/pargenrol/PantallaDigitalMaster) está instalado en otro host/puerto que no sea `localhost:5001` | El botón "Generar RAG" usa `http://localhost:5001` por defecto |

### Meter documentos en la biblioteca

La vía normal: **arrastra los PDF a la ventana**, o pulsa "📤 Subir" — no hace falta Telegram ni ningún canal externo. Los scripts de Telegram (`descargar.py`, `listar_canales.py`) son opcionales, solo si tú mismo tienes un canal propio del que importar en bloque.

## 2. Uso

### Navegación y búsqueda

La portada muestra las carpetas/categorías de tu biblioteca; el buscador filtra por nombre de fichero desde cualquier pantalla.

### Visor de PDF

Zoom, navegación táctil, modo columna para libros a doble columna. Botón de descarga directa.

### Traducción (🌐 ES)

Desde el visor, botón "🌐 ES" abre el panel de traducción con tres motores intercambiables:

* **Ollama** (local, gratis) — necesita tenerlo corriendo.
* **Claude** (remoto, de pago) — necesita `ANTHROPIC_API_KEY` en `.env`; si no está configurada, el botón aparece deshabilitado.
* **Argos Translate** (offline, siempre disponible si está instalado) — respaldo automático si ninguno de los otros dos está disponible.

Cada página se traduce al vuelo con streaming en vivo. El botón **"📚 Traducir PDF completo en 2.º plano"** encola el libro entero, con una cola persistente que sobrevive a reinicios (progreso consultable en `/jobs`) — por ahora esta cola solo usa Ollama, aunque la traducción al vuelo ya soporta los tres motores.

Con notas o traducción en pantalla, **"📓 Guardar en Obsidian"** las añade a tu vault (si tienes `OBSIDIAN_BIBLIOTECA` configurado).

### Esquema de preparación de partida

A partir del texto de una aventura, genera 6 ficheros Markdown (visión general, PNJs, lugares, encuentros, loot, hoja de prep rápida) con IA — requiere Ollama u otra IA configurada y `OBSIDIAN_PARTIDAS`.

### Integración con Pantallasistemas (opcional)

Si tienes también [Pantallasistemas](https://github.com/pargenrol/PantallaDigitalMaster) instalado:

* El botón **"🔄 Generar RAG"** en la portada le pide que reindexe esta biblioteca para que su asistente de IA pueda citar tus PDFs — con barra de progreso, y un error claro si Pantallasistemas no está instalado o no está corriendo.
* Son proyectos totalmente independientes: cada uno funciona solo, sin el otro instalado.
