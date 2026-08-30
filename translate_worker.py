#!/usr/bin/env python3
"""
Worker de traducción independiente — sobrevive al cierre de la app Flask.
Uso: python3 translate_worker.py <rel_path> <job_file>
"""
import sys
import json
import time
import urllib.request
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from config import OBSIDIAN_TRADUCCIONES

BIBLIOTECA        = Path(__file__).parent / "biblioteca"
OLLAMA_URL        = "http://localhost:11434"
OLLAMA_MODEL      = "qwen2.5:7b-instruct-q4_K_M"
TRANSLATE_PROMPT  = (
    "You are a professional translator specializing in tabletop role-playing games (TTRPGs). "
    "Translate the following text from English to Spanish.\n\n"
    "Rules:\n"
    "- Use standard Spanish RPG terminology\n"
    "- Keep dice notation as-is (1d6, 2d8+3, DC 14, etc.)\n"
    "- Keep proper nouns in the original language unless they have a well-known Spanish equivalent\n"
    "- Output ONLY the translation, nothing else\n\n"
    "Text to translate:\n"
)

def save_state(job_file: Path, state: dict):
    job_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def ollama_translate(text: str, retries: int = 10) -> str:
    payload = json.dumps({"model": OLLAMA_MODEL, "prompt": TRANSLATE_PROMPT + text, "stream": False}).encode()
    wait = 15
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read()).get("response", "").strip()
        except Exception:
            if attempt < retries - 1:
                time.sleep(wait)
                wait = min(wait * 2, 120)  # backoff exponencial, máximo 2 min
    raise RuntimeError(f"Ollama no responde tras {retries} intentos")

def clean_text(raw: str) -> str:
    lines = raw.splitlines()
    paragraphs, buf = [], []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                paragraphs.append(" ".join(buf)); buf = []
        else:
            if buf and buf[-1].endswith("-"):
                buf[-1] = buf[-1][:-1] + stripped
            else:
                buf.append(stripped)
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs)

def main():
    if len(sys.argv) < 3:
        print("Uso: translate_worker.py <rel_path> <job_file>"); sys.exit(1)

    rel_path = sys.argv[1]
    job_file = Path(sys.argv[2])
    target   = BIBLIOTECA / rel_path

    if OBSIDIAN_TRADUCCIONES is None:
        save_state(job_file, {"page": 0, "total": 0, "done": True,
                               "error": "No hay vault de Obsidian configurado (OBSIDIAN_BIBLIOTECA en .env)",
                               "rel_path": rel_path})
        sys.exit(1)

    import fitz
    doc   = fitz.open(target)
    total = len(doc)
    today = date.today().isoformat()
    book  = target.stem
    out   = OBSIDIAN_TRADUCCIONES / f"{book}.md"

    # Leer estado previo si existe
    state = json.loads(job_file.read_text(encoding="utf-8")) if job_file.exists() else {}
    start_page = state.get("page", 0)

    OBSIDIAN_TRADUCCIONES.mkdir(parents=True, exist_ok=True)
    if start_page == 0:
        out.write_text(
            f'---\ntitle: "{book} — Traducción"\nfuente: "biblioteca/{rel_path}"\n'
            f'tags:\n  - rol\n  - biblioteca-pargen\n  - traduccion-completa\n'
            f'fecha_generacion: {today}\n---\n\n# {book}\n\n'
            f'> *Traducción automática completa — Ollama · {OLLAMA_MODEL} — {today}*\n',
            encoding="utf-8",
        )

    save_state(job_file, {"page": start_page, "total": total, "done": False, "error": None, "rel_path": rel_path})

    current_page = start_page
    try:
        for i in range(start_page, total):
            current_page = i
            raw = doc[i].get_text().strip()
            if not raw:
                section = f"\n## Página {i+1}\n\n*[Página sin texto extraíble]*\n\n---\n"
            else:
                translated = ollama_translate(clean_text(raw))
                section = f"\n## Página {i+1}\n\n{translated}\n\n---\n"
            with out.open("a", encoding="utf-8") as f:
                f.write(section)
            # Actualizar estado en disco tras cada página
            save_state(job_file, {"page": i + 1, "total": total, "done": False, "error": None, "rel_path": rel_path})
        doc.close()
        save_state(job_file, {"page": total, "total": total, "done": True, "error": None, "rel_path": rel_path})
    except Exception as e:
        save_state(job_file, {"page": current_page, "total": total, "done": True, "error": str(e), "rel_path": rel_path})
        sys.exit(1)

if __name__ == "__main__":
    main()
