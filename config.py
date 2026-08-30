"""Configuración de rol-biblioteca. Lee un .env local si existe (mismo
cargador manual de dos líneas que usa Pantallasistemas/app.py)."""
import os
from pathlib import Path

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


def _optional_path(env_var: str, default: str = "") -> Path | None:
    """Devuelve un Path si la variable de entorno está definida (o hay
    default), o None si no — para poder desactivar limpiamente las
    funciones que escriben al vault de Obsidian cuando no está configurado."""
    value = os.environ.get(env_var, default).strip()
    return Path(value) if value else None


# Rutas de tu propio vault de Obsidian (opcional). Si están vacías, las
# funciones de "guardar en vault" (notas, traducciones, esquemas de partida)
# quedan desactivadas — el resto de la app (biblioteca, visor, subida)
# funciona igual sin esto configurado.
OBSIDIAN_BIBLIOTECA = _optional_path("OBSIDIAN_BIBLIOTECA")
OBSIDIAN_TRADUCCIONES = (OBSIDIAN_BIBLIOTECA / "Traducciones") if OBSIDIAN_BIBLIOTECA else None
OBSIDIAN_PARTIDAS = _optional_path("OBSIDIAN_PARTIDAS")

# URL de Pantallasistemas (proyecto hermano, opcional), solo para el botón
# "Generar RAG" — le pide que reindexe esta biblioteca. Si no está accesible,
# el botón simplemente muestra un error claro; el resto de la app no depende
# de esto en absoluto.
PANTALLASISTEMAS_URL = os.environ.get("PANTALLASISTEMAS_URL", "http://localhost:5001").rstrip("/")
