"""
Descargador de biblioteca de rol desde Telegram
Organiza PDFs por tema y genera vault de Obsidian
"""

import asyncio
import os
import re
from pathlib import Path
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument

# ── .env (mismo cargador manual que usa Pantallasistemas/app.py) ───────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── CONFIGURACIÓN ──────────────────────────────────────────────────────────────
# API_ID/API_HASH: los tuyos de https://my.telegram.org — nunca hardcodeados aquí,
# van en .env (TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_CANAL_ID).
API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
CANAL    = int(os.environ.get("TELEGRAM_CANAL_ID", "0"))  # ID numérico del canal (prefijo -100 para supergrupos)

if not API_ID or not API_HASH:
    raise SystemExit(
        "Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH. Copia .env.example a .env y rellénalos "
        "con tus credenciales de https://my.telegram.org"
    )

CARPETA_DESCARGAS = Path("./biblioteca")
CARPETA_VAULT     = Path("./vault-obsidian")
# ──────────────────────────────────────────────────────────────────────────────


def nombre_seguro(nombre: str) -> str:
    """Convierte un nombre en nombre válido para carpeta/archivo."""
    nombre = re.sub(r'[<>:"/\\|?*]', '', nombre)
    nombre = re.sub(r'\s+', ' ', nombre).strip()
    return nombre[:80]  # límite razonable de longitud


async def obtener_temas(client, canal):
    """Obtiene todos los temas (forum topics) del canal."""
    result = await client(GetForumTopicsRequest(
        peer=canal,
        offset_date=None,
        offset_id=0,
        offset_topic=0,
        limit=100
    ))
    return result.topics


async def descargar_pdfs_de_tema(client, canal, tema, carpeta_tema: Path, progreso: dict):
    """Descarga todos los PDFs de un tema concreto."""
    pdfs_descargados = []

    async for mensaje in client.iter_messages(canal, reply_to=tema.id):
        if not mensaje.media:
            continue
        if not isinstance(mensaje.media, MessageMediaDocument):
            continue

        doc = mensaje.media.document
        nombre_archivo = None

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                nombre_archivo = attr.file_name
                break

        if not nombre_archivo:
            continue

        es_pdf = (
            nombre_archivo.lower().endswith('.pdf') or
            doc.mime_type == 'application/pdf'
        )
        if not es_pdf:
            continue

        ruta_destino = carpeta_tema / nombre_archivo

        if ruta_destino.exists():
            print(f"  [ya existe] {nombre_archivo}")
            pdfs_descargados.append(nombre_archivo)
            progreso['omitidos'] += 1
            continue

        print(f"  Descargando: {nombre_archivo}")
        try:
            await client.download_media(mensaje, file=ruta_destino)
            pdfs_descargados.append(nombre_archivo)
            progreso['descargados'] += 1
        except Exception as e:
            print(f"  [error] {nombre_archivo}: {e}")
            progreso['errores'] += 1

    return pdfs_descargados


def generar_nota_obsidian(tema_nombre: str, pdfs: list[str], carpeta_vault: Path, carpeta_relativa: str):
    """Genera un archivo .md para el vault de Obsidian."""
    carpeta_vault.mkdir(parents=True, exist_ok=True)
    nombre_nota = nombre_seguro(tema_nombre) + ".md"
    ruta_nota = carpeta_vault / nombre_nota

    lineas = [
        f"# {tema_nombre}",
        "",
        f"> Sistema de juego descargado el {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## PDFs disponibles",
        "",
    ]

    if pdfs:
        for pdf in sorted(pdfs):
            # Enlace relativo al archivo dentro del vault
            ruta_pdf = f"../biblioteca/{carpeta_relativa}/{pdf}"
            lineas.append(f"- [{pdf}]({ruta_pdf})")
    else:
        lineas.append("_No se encontraron PDFs en este tema._")

    lineas += ["", "## Notas", "", ""]

    ruta_nota.write_text("\n".join(lineas), encoding="utf-8")
    return nombre_nota


def generar_indice(temas_pdfs: dict, carpeta_vault: Path):
    """Genera el índice general del vault."""
    carpeta_vault.mkdir(parents=True, exist_ok=True)
    ruta_indice = carpeta_vault / "index.md"

    total_pdfs = sum(len(v) for v in temas_pdfs.values())
    lineas = [
        "# Biblioteca de Rol",
        "",
        f"> Generado el {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"{len(temas_pdfs)} sistemas · {total_pdfs} PDFs",
        "",
        "## Sistemas de juego",
        "",
    ]

    for tema, pdfs in sorted(temas_pdfs.items()):
        nombre_nota = nombre_seguro(tema) + ".md"
        lineas.append(f"- [[{nombre_nota[:-3]}]] ({len(pdfs)} PDFs)")

    lineas += ["", "---", "_Vault generado automáticamente_", ""]
    ruta_indice.write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nÍndice generado: {ruta_indice}")


async def main():
    if not API_ID or not API_HASH or not CANAL:
        print("ERROR: Configura API_ID, API_HASH y CANAL en el script.")
        return

    CARPETA_DESCARGAS.mkdir(parents=True, exist_ok=True)
    CARPETA_VAULT.mkdir(parents=True, exist_ok=True)

    async with TelegramClient("sesion_biblioteca", API_ID, API_HASH) as client:
        print(f"Conectado. Obteniendo temas del canal: {CANAL}\n")

        canal_entity = await client.get_entity(CANAL)
        temas = await obtener_temas(client, canal_entity)

        print(f"Temas encontrados: {len(temas)}")
        for t in temas:
            print(f"  - {t.title} (id: {t.id})")
        print()

        progreso = {'descargados': 0, 'omitidos': 0, 'errores': 0}
        temas_pdfs = {}

        for tema in temas:
            nombre = tema.title
            carpeta_nombre = nombre_seguro(nombre)
            carpeta_tema = CARPETA_DESCARGAS / carpeta_nombre
            carpeta_tema.mkdir(parents=True, exist_ok=True)

            print(f"\n[{nombre}]")
            pdfs = await descargar_pdfs_de_tema(client, canal_entity, tema, carpeta_tema, progreso)
            temas_pdfs[nombre] = pdfs

            # Nota de Obsidian para este sistema
            generar_nota_obsidian(nombre, pdfs, CARPETA_VAULT / "sistemas", carpeta_nombre)

        # Índice general
        generar_indice(temas_pdfs, CARPETA_VAULT)

        print(f"\n{'='*50}")
        print(f"Descargados: {progreso['descargados']}")
        print(f"Ya existían: {progreso['omitidos']}")
        print(f"Errores:     {progreso['errores']}")
        print(f"\nBiblioteca: {CARPETA_DESCARGAS.resolve()}")
        print(f"Vault:      {CARPETA_VAULT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
