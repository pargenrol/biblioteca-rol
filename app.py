#!/usr/bin/env python3
"""Biblioteca de Rol Pargen - Web App"""

from pathlib import Path
from urllib.parse import quote
from hashlib import md5
import os
import threading
import json
import subprocess
import re as _re
from datetime import date as _date
import fitz  # PyMuPDF
import argostranslate.translate
import urllib.request
import urllib.error
from flask import Flask, send_file, render_template_string, abort, request, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename

VERSION = "3.2"

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_MODEL        = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_MODEL_ESQUEMA = "qwen2.5:7b-instruct-q4_K_M"

CLAUDE_URL   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

def ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))

def claude_stream(prompt: str, max_tokens: int = 4096):
    """Generador de fragmentos de texto desde la API de Anthropic (streaming SSE)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        CLAUDE_URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "content_block_delta":
                text = event.get("delta", {}).get("text", "")
                if text:
                    yield text
            elif event.get("type") == "error":
                raise RuntimeError(event.get("error", {}).get("message", "Error de la API de Claude"))
            elif event.get("type") == "message_stop":
                return

ESQUEMA_PROMPT = """Eres un asistente de máster de rol experto. Analiza el texto de la siguiente aventura y genera un esquema de preparación de partida en español.

Genera exactamente estos ficheros, separando cada uno con la línea "===FICHERO: nombre.md===":

===FICHERO: 00_Aventura.md===
[Título, sistema de juego, nivel recomendado, tema central, premisa y ganchos para los jugadores]

===FICHERO: 01_PNJs.md===
[Todos los PNJs importantes: nombre en negrita, rol, descripción física breve, motivación y secreto si lo tiene]

===FICHERO: 02_Lugares.md===
[Localizaciones principales: nombre, descripción de ambiente, puntos de interés y secretos]

===FICHERO: 03_Encuentros.md===
[Combates, trampas, desafíos sociales y momentos clave con notas de dirección para el máster]

===FICHERO: 04_Loot.md===
[Tesoros, objetos mágicos y recompensas narrativas o monetarias]

===FICHERO: Prep_DM.md===
[Hoja de prep rápida estilo Lazy DM: apertura fuerte in media res, 3 escenas imprescindibles, tabla de secretos/pistas en d6, todos los PNJs resumidos en una frase, reloj de amenaza si los jugadores no actúan]

Texto de la aventura:
"""

TRANSLATE_PROMPT = """You are a professional translator specializing in tabletop role-playing games (TTRPGs). Translate the following text from English to Spanish.

Rules:
- Use standard Spanish RPG terminology (e.g. "tirada de salvación", "puntos de golpe", "director de juego")
- Keep dice notation as-is (1d6, 2d8+3, DC 14, etc.)
- Keep proper nouns (character names, place names) in the original language unless they have a well-known Spanish equivalent
- Output ONLY the translation, nothing else

Text to translate:
"""

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB por subida

from config import OBSIDIAN_BIBLIOTECA, OBSIDIAN_TRADUCCIONES, OBSIDIAN_PARTIDAS, PANTALLASISTEMAS_URL

BIBLIOTECA = Path(__file__).parent / "biblioteca"
BIBLIOTECA.mkdir(exist_ok=True)  # no viaja con el repo (gitignored) — se crea vacía al primer arranque
JOBS_DIR            = Path(__file__).parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
WORKER              = Path(__file__).parent / "translate_worker.py"
THUMBS       = Path(__file__).parent / "static" / "thumbs"
THUMBS.mkdir(exist_ok=True)
COVERS_FILE  = Path(__file__).parent / "static" / "folder_covers.json"

_thumb_lock  = threading.Lock()
_covers_lock = threading.Lock()

def _load_covers() -> dict:
    if COVERS_FILE.exists():
        try:
            return json.loads(COVERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_covers(data: dict):
    COVERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def thumb_path(rel: str) -> Path:
    """Devuelve la ruta del thumbnail para un PDF dado su path relativo."""
    key = md5(rel.encode()).hexdigest()
    return THUMBS / f"{key}.jpg"

def generate_thumb(pdf_path: Path, out: Path):
    """Renderiza la primera página del PDF como JPEG 300px de ancho."""
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        # Escala para que el ancho sea ~300px
        scale = 300 / page.rect.width
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(out), "jpeg")
        doc.close()
    except Exception:
        pass  # Si falla, simplemente no hay miniatura

def get_thumb_url(rel: str) -> str | None:
    """Devuelve la URL del thumbnail (generándolo si no existe todavía)."""
    out = thumb_path(rel)
    if not out.exists():
        pdf = BIBLIOTECA / rel
        if pdf.exists():
            with _thumb_lock:
                if not out.exists():  # doble check dentro del lock
                    generate_thumb(pdf, out)
    return f"/static/thumbs/{out.name}" if out.exists() else None

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

HTML_LIBRARY = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0a0603">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Pargen">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<title>Biblioteca — Club de Rol Pargen</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,600;0,700;1,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:        #0a0603;
    --surface:   #120d08;
    --card:      #1a1208;
    --card-h:    #221808;
    --gold:      #c9a84c;
    --gold-dim:  rgba(201,168,76,.15);
    --gold-brd:  rgba(201,168,76,.25);
    --gold-brdh: rgba(201,168,76,.55);
    --red:       #8b1a1a;
    --red-dim:   rgba(139,26,26,.18);
    --cream:     #f5ead8;
    --muted:     rgba(245,234,216,.45);
    --border:    rgba(201,168,76,.12);
    --glow:      0 4px 32px rgba(201,168,76,.12);
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { height: 100%; }
  body {
    background: var(--bg); color: var(--cream);
    font-family: 'Inter', system-ui, sans-serif;
    min-height: 100%; min-height: 100dvh;
    background-image:
      radial-gradient(ellipse 80% 50% at 50% -5%, rgba(139,26,26,.18) 0%, transparent 65%),
      radial-gradient(ellipse 50% 30% at 90% 90%, rgba(201,168,76,.06) 0%, transparent 60%);
  }

  /* ── Ornamento: línea dorada ── */
  .ornament {
    display: flex; align-items: center; gap: 0.8rem;
    color: var(--gold); font-size: 0.7rem; letter-spacing: .2em;
    text-transform: uppercase;
  }
  .ornament::before, .ornament::after {
    content: ''; flex: 1; height: 1px;
    background: linear-gradient(90deg, transparent, var(--gold-brd), transparent);
  }

  /* ── Hero (solo portada) ── */
  .hero {
    display: flex; flex-direction: column; align-items: center;
    padding: 3rem 1.5rem 2rem; text-align: center; gap: 1.2rem;
  }
  .hero-logo { height: 90px; width: auto; filter: drop-shadow(0 0 24px rgba(201,168,76,.35)); }
  .hero-title {
    font-family: 'Playfair Display', serif;
    font-size: clamp(2rem, 6vw, 3rem); font-weight: 700;
    color: var(--gold); letter-spacing: -.01em; line-height: 1.1;
  }
  .hero-title em { font-style: italic; color: var(--cream); }
  .hero-sub { color: var(--muted); font-size: 0.88rem; letter-spacing: .05em; text-transform: uppercase; }
  .version-badge { font-size: 0.68rem; color: var(--muted); background: rgba(201,168,76,.08); border: 1px solid var(--gold-brd); border-radius: 4px; padding: 0.1rem 0.4rem; }

  /* ── Header (páginas interiores) ── */
  header {
    position: sticky; top: 0; z-index: 50;
    background: rgba(10,6,3,.92); backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border);
    padding: 0 1.2rem;
  }
  .header-inner {
    max-width: 1280px; margin: 0 auto;
    height: 58px; display: flex; align-items: center; gap: 0.8rem;
  }
  .logo-sm { display: flex; align-items: center; gap: 0.6rem; text-decoration: none; flex-shrink: 0; }
  .logo-sm img { height: 30px; width: auto; }
  .logo-sm span { font-family: 'Playfair Display', serif; font-size: 1.05rem; color: var(--gold); }
  .header-sep { width: 1px; height: 20px; background: var(--border); flex-shrink: 0; }

  /* ── Buscador ── */
  .search-wrap { flex: 1; position: relative; max-width: 400px; margin-left: auto; }
  .search-wrap svg { position: absolute; left: 0.75rem; top: 50%; transform: translateY(-50%); color: var(--muted); pointer-events: none; }
  .search-box {
    width: 100%; background: var(--surface); border: 1px solid var(--gold-brd);
    color: var(--cream); padding: 0.45rem 0.9rem 0.45rem 2.2rem;
    border-radius: 8px; font-size: 0.88rem; outline: none; font-family: inherit;
    transition: border-color .2s, box-shadow .2s;
  }
  .search-box::placeholder { color: var(--muted); }
  .search-box:focus { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(201,168,76,.1); }
  .book-count { color: var(--muted); font-size: 0.78rem; white-space: nowrap; flex-shrink: 0; }

  /* ── Main ── */
  main { max-width: 1280px; margin: 0 auto; padding: 0 1.2rem 4rem; }

  /* ── Search bar portada ── */
  .hero-search { padding: 0 1.5rem 1.5rem; max-width: 560px; margin: 0 auto; width: 100%; }
  .hero-search .search-wrap { max-width: 100%; margin: 0; }
  .hero-search .search-box { padding: 0.65rem 1rem 0.65rem 2.4rem; font-size: 1rem; border-radius: 10px; }

  /* ── Breadcrumb ── */
  .breadcrumb {
    display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
    font-size: 0.8rem; color: var(--muted); margin-bottom: 1.5rem; padding-top: 1.5rem;
  }
  .breadcrumb a { color: var(--gold); text-decoration: none; }
  .breadcrumb a:hover { color: var(--cream); }

  /* ── Section label ── */
  .section-label {
    font-family: 'Playfair Display', serif; font-size: 1.1rem;
    color: var(--gold); margin-bottom: 1rem; padding-top: 1.5rem;
    display: flex; align-items: center; gap: 0.8rem;
  }
  .section-label::after { content: ''; flex: 1; height: 1px; background: var(--border); }

  /* ── Grid ── */
  .grid { display: grid; gap: 1rem; }
  .grid-folders { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
  .grid-books   { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }

  /* ── Folder card — estilo portada ── */
  .folder-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden;
    text-decoration: none; color: var(--cream);
    display: flex; flex-direction: column;
    transition: border-color .2s, box-shadow .2s, transform .15s;
    position: relative;
  }
  .folder-card:hover {
    border-color: var(--gold-brdh);
    box-shadow: var(--glow), 0 8px 32px rgba(0,0,0,.5);
    transform: translateY(-4px) scale(1.01);
  }
  .folder-cover-img {
    width: 100%; aspect-ratio: 3/4; object-fit: cover; display: block;
  }
  .folder-cover-placeholder {
    width: 100%; aspect-ratio: 3/4;
    background: linear-gradient(160deg, var(--red) 0%, #1a0a0a 60%, #0d0a00 100%);
    display: flex; align-items: center; justify-content: center; font-size: 2.8rem;
  }
  .folder-body {
    padding: 0.65rem; display: flex; flex-direction: column; gap: 0.3rem;
    border-top: 2px solid var(--gold);
  }
  .folder-name { font-family: 'Playfair Display', serif; font-size: 0.82rem; font-weight: 600; line-height: 1.3; }
  .folder-count { color: var(--muted); font-size: 0.68rem; letter-spacing: .05em; }

  /* ── Book card — estilo portada de libro ── */
  .book-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden;
    display: flex; flex-direction: column;
    transition: border-color .2s, box-shadow .2s, transform .15s;
    cursor: pointer;
  }
  .book-card:hover {
    border-color: var(--gold-brdh);
    box-shadow: var(--glow), 0 8px 32px rgba(0,0,0,.5);
    transform: translateY(-4px) scale(1.01);
  }
  .book-thumb {
    width: 100%; aspect-ratio: 3/4; object-fit: cover;
    display: block;
  }
  .book-thumb-placeholder {
    width: 100%; aspect-ratio: 3/4;
    background: linear-gradient(160deg, var(--red) 0%, #1a0a0a 60%, #0d0a00 100%);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 0.5rem; padding: 1rem;
    border-bottom: 1px solid var(--border);
  }
  .placeholder-icon { font-size: 2rem; opacity: .6; }
  .placeholder-title {
    font-family: 'Playfair Display', serif; font-size: 0.7rem; font-style: italic;
    color: rgba(245,234,216,.6); text-align: center; line-height: 1.3;
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
  }
  .book-body { padding: 0.65rem; display: flex; flex-direction: column; gap: 0.5rem; flex: 1; }
  .book-title {
    font-size: 0.78rem; font-weight: 600; line-height: 1.35; color: var(--cream);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .book-folder { color: var(--muted); font-size: 0.68rem; }
  .book-actions { display: flex; gap: 0.4rem; margin-top: auto; padding-top: 0.3rem; }
  .btn {
    flex: 1; padding: 0.38rem 0.4rem; border-radius: 6px;
    font-size: 0.72rem; font-weight: 600; text-align: center;
    text-decoration: none; border: none; cursor: pointer;
    font-family: inherit; transition: opacity .15s, transform .1s;
    display: flex; align-items: center; justify-content: center; gap: 0.2rem;
  }
  .btn:active { transform: scale(.96); }
  .btn-read { background: var(--gold); color: #0a0603; }
  .btn-read:hover { opacity: .88; }
  .btn-dl { background: var(--red-dim); color: #f87171; border: 1px solid rgba(139,26,26,.4); }
  .btn-dl:hover { background: rgba(139,26,26,.3); }
  .btn-pin { background: var(--gold-dim); color: var(--gold); border: 1px solid var(--gold-brd); flex: 0; padding: 0.38rem 0.5rem; }
  .btn-pin:hover { background: rgba(201,168,76,.28); }
  .btn-pin.pinned { background: var(--gold); color: #0a0603; }
  .btn-esquema { background: rgba(34,139,34,.12); color: #4ade80; border: 1px solid rgba(34,139,34,.3); flex: 0; padding: 0.38rem 0.5rem; }
  .btn-esquema:hover { background: rgba(34,139,34,.25); }
  .btn-esquema:disabled { opacity: .4; cursor: default; }
  /* Modal esquema */
  #esquema-modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.7); z-index:9999; align-items:center; justify-content:center; }
  #esquema-modal.open { display:flex; }
  .esquema-box { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1.5rem 1.8rem; max-width:480px; width:90%; box-shadow:0 8px 32px rgba(0,0,0,.6); }
  .esquema-box h3 { font-family:'Playfair Display',serif; color:var(--gold); margin:0 0 0.3rem; font-size:1.1rem; }
  .esquema-box .esquema-sub { font-size:0.78rem; color:var(--muted); margin-bottom:1.2rem; }
  .esquema-status { font-size:0.88rem; color:var(--cream); min-height:2rem; }
  .esquema-files { margin-top:0.8rem; font-size:0.82rem; color:#4ade80; }
  .esquema-files li { list-style:none; padding:0.15rem 0; }
  .esquema-files li::before { content:'✓ '; }
  .esquema-err { color:#f87171; font-size:0.85rem; margin-top:0.6rem; }
  .esquema-spinner { display:inline-block; width:14px; height:14px; border:2px solid rgba(201,168,76,.2); border-top-color:var(--gold); border-radius:50%; animation:spin .7s linear infinite; margin-right:0.5rem; vertical-align:middle; }
  .esquema-close { margin-top:1.2rem; display:block; width:100%; padding:0.5rem; background:var(--gold-dim); color:var(--gold); border:1px solid var(--gold-brd); border-radius:8px; cursor:pointer; font-family:inherit; font-size:0.85rem; }
  .esquema-close:hover { background:rgba(201,168,76,.28); }
  .esquema-warn { font-size:0.85rem; color:var(--cream); line-height:1.55; margin-bottom:1rem; padding:0.7rem 0.8rem; background:rgba(251,191,36,.07); border:1px solid rgba(251,191,36,.2); border-radius:8px; }
  .esquema-warn strong { color:#fbbf24; }
  .esquema-warn-btns { display:flex; gap:0.5rem; flex-wrap:wrap; }
  .esquema-act-btn { flex:1; padding:0.45rem 0.7rem; border-radius:8px; cursor:pointer; font-family:inherit; font-size:0.82rem; border:1px solid rgba(74,222,128,.3); background:rgba(74,222,128,.1); color:#4ade80; }
  .esquema-act-btn:hover { background:rgba(74,222,128,.2); }
  .esquema-act-sec { background:rgba(255,255,255,.04); color:var(--muted); border-color:var(--border); }
  .esquema-act-sec:hover { background:rgba(255,255,255,.08); color:var(--cream); }
  .esquema-elapsed { font-size:0.75rem; color:var(--muted); font-style:italic; }
  .esquema-resumed { margin-top:0.7rem; font-size:0.8rem; color:#4ade80; padding:0.4rem 0.6rem; background:rgba(74,222,128,.08); border:1px solid rgba(74,222,128,.2); border-radius:6px; }

  /* ── Result header ── */
  .result-header { padding-top: 1.5rem; margin-bottom: 1.2rem; }
  .result-header h3 { font-family: 'Playfair Display', serif; font-size: 1.2rem; color: var(--gold); }
  .result-header span { color: var(--muted); font-size: 0.82rem; }

  /* ── Empty ── */
  .empty { grid-column: 1/-1; text-align: center; padding: 5rem 2rem; color: var(--muted); }
  .empty-icon { font-size: 3rem; margin-bottom: 1rem; }
</style>
</head>
<body>

{% if not current_path and not query %}
<!-- ══ PORTADA ══ -->
<div class="hero">
  <img class="hero-logo" src="/static/logo.webp" alt="Pargen"
       onerror="this.style.display='none'">
  <div>
    <div class="hero-title"><em>Biblioteca</em> Pargen</div>
    <div class="hero-sub">Club de Rol · Colección digital</div>
    <span class="version-badge">v{{ version }}</span>
  </div>
  <div class="ornament">——  Fondo de libros  ——</div>
  <a href="/jobs" style="text-decoration:none;background:rgba(201,168,76,.12);border:1px solid rgba(201,168,76,.3);color:var(--gold);padding:0.4rem 1rem;border-radius:8px;font-size:0.82rem;font-family:inherit">📚 Traducciones en curso</a>
  <button type="button" id="upload-btn" style="cursor:pointer;background:rgba(201,168,76,.12);border:1px solid rgba(201,168,76,.3);color:var(--gold);padding:0.4rem 1rem;border-radius:8px;font-size:0.82rem;font-family:inherit;margin-left:.5rem">📤 Subir documentos</button>
  <button type="button" id="rag-btn" title="Pide a Pantallasistemas que reindexe esta biblioteca para su asistente IA"
          style="cursor:pointer;background:rgba(201,168,76,.12);border:1px solid rgba(201,168,76,.3);color:var(--gold);padding:0.4rem 1rem;border-radius:8px;font-size:0.82rem;font-family:inherit;margin-left:.5rem">🔄 Generar RAG</button>
  <div id="rag-status" style="font-size:0.75rem;color:var(--muted);margin-top:.4rem"></div>
</div>

<div class="hero-search">
  <div class="search-wrap">
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
    </svg>
    <input class="search-box" type="search" id="search" placeholder="Buscar en la colección…"
           autocomplete="off" value="{{ query }}">
  </div>
</div>

<main>
  <p class="section-label">Secciones</p>
  <div class="grid grid-folders">
    {% for f in folders %}
    <a class="folder-card" href="/browse/{{ f.rel }}">
      {% if f.cover %}
        <img class="folder-cover-img" src="{{ f.cover }}" alt="{{ f.name }}" loading="lazy">
      {% else %}
        <div class="folder-cover-placeholder">{{ f.icon }}</div>
      {% endif %}
      <div class="folder-body">
        <div class="folder-name">{{ f.name }}</div>
        <div class="folder-count">{{ f.count }} libros</div>
      </div>
    </a>
    {% endfor %}
  </div>
</main>

{% else %}
<!-- ══ PÁGINAS INTERIORES ══ -->
<header>
  <div class="header-inner">
    <a class="logo-sm" href="/">
      <img src="/static/logo.webp" alt="Pargen" onerror="this.style.display='none'">
      <span>Pargen</span>
    </a>
    <div class="header-sep"></div>
    <div class="search-wrap">
      <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <input class="search-box" type="search" id="search" placeholder="Buscar…"
             autocomplete="off" value="{{ query }}">
    </div>
    <a href="/jobs" style="text-decoration:none;color:var(--muted);font-size:0.78rem;white-space:nowrap;flex-shrink:0" title="Traducciones en curso">📚 Trad.</a>
    <button type="button" id="upload-btn" style="cursor:pointer;background:none;border:1px solid var(--line, #444);color:var(--muted);font-size:0.78rem;white-space:nowrap;flex-shrink:0;border-radius:6px;padding:.3rem .6rem;font-family:inherit" title="Subir documentos a esta carpeta">📤 Subir</button>
    <div class="book-count" id="count"></div>
  </div>
</header>

<main>
  {% if not query and current_path %}
  <nav class="breadcrumb">
    <a href="/">Inicio</a>
    {% for part in breadcrumb %}
      <span>›</span>
      {% if not loop.last %}<a href="/browse/{{ part.path }}">{{ part.name }}</a>
      {% else %}<span>{{ part.name }}</span>{% endif %}
    {% endfor %}
  </nav>
  {% endif %}

  {% if query %}
  <div class="result-header">
    <h3>«{{ query }}»</h3>
    <span id="count"></span>
  </div>
  {% endif %}

  {% if folders %}
  <p class="section-label">Subsecciones</p>
  <div class="grid grid-folders" style="margin-bottom:2rem">
    {% for f in folders %}
    <a class="folder-card" href="/browse/{{ f.rel }}">
      {% if f.cover %}
        <img class="folder-cover-img" src="{{ f.cover }}" alt="{{ f.name }}" loading="lazy">
      {% else %}
        <div class="folder-cover-placeholder">{{ f.icon }}</div>
      {% endif %}
      <div class="folder-body">
        <div class="folder-name">{{ f.name }}</div>
        <div class="folder-count">{{ f.count }} libros</div>
      </div>
    </a>
    {% endfor %}
  </div>
  {% endif %}

  {% if books %}
  {% if folders %}<p class="section-label">Libros</p>{% endif %}
  <div class="grid grid-books" id="grid">
    {% for b in books %}
    <div class="book-card">
      {% if b.thumb %}
        <img class="book-thumb" src="{{ b.thumb }}" alt="{{ b.name }}" loading="lazy">
      {% else %}
        <div class="book-thumb-placeholder">
          <div class="placeholder-icon">📖</div>
          <div class="placeholder-title">{{ b.name }}</div>
        </div>
      {% endif %}
      <div class="book-body">
        <div class="book-title">{{ b.name }}</div>
        <div class="book-folder">{{ b.folder }}</div>
        <div class="book-actions">
          <a class="btn btn-read" href="/viewer/{{ b.rel }}">Leer</a>
          <a class="btn btn-dl"   href="/download/{{ b.rel }}" download>↓</a>
          {% if current_path %}<button class="btn btn-pin {% if pinned_covers.get(b.folder) == b.rel %}pinned{% endif %}"
            data-rel="{{ b.rel }}" data-folder="{{ b.folder }}" title="Usar como portada de sección">📌</button>{% endif %}
          {% if in_aventuras %}<button class="btn btn-esquema" data-rel="{{ b.rel }}" data-name="{{ b.name }}" title="Generar esquema de partida en Obsidian">🗺️</button>{% endif %}
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% elif not folders %}
  <div class="grid"><div class="empty">
    <div class="empty-icon">🔍</div>
    <p>No se encontraron libros{% if query %} para «{{ query }}»{% endif %}</p>
  </div></div>
  {% endif %}
</main>
{% endif %}

<div id="esquema-modal">
  <div class="esquema-box">
    <h3 class="esquema-title"></h3>
    <div class="esquema-sub">Generando esquema de partida en Obsidian</div>
    <div class="esquema-status"></div>
    <div class="esquema-files"></div>
    <div class="esquema-err"></div>
    <button class="esquema-close">Cerrar</button>
  </div>
</div>

<input type="file" id="upload-input" multiple accept="application/pdf,.pdf" style="display:none">
<div id="upload-dropzone" style="display:none;position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.55);align-items:center;justify-content:center;flex-direction:column;color:var(--gold);font-family:inherit;pointer-events:none">
  <div style="border:3px dashed var(--gold);border-radius:16px;padding:3rem 4rem;font-size:1.1rem;background:rgba(0,0,0,.3)">📤 Suelta los PDF aquí</div>
</div>
<div id="upload-status" style="display:none;position:fixed;bottom:1.2rem;right:1.2rem;z-index:9999;background:var(--panel,#1a1a1a);border:1px solid var(--gold);color:var(--cream,#eee);padding:.7rem 1.1rem;border-radius:8px;font-size:.85rem;font-family:inherit;max-width:320px"></div>

<script>
// ── Subida de documentos (arrastrar/soltar o botón) ─────────────────────────
(function() {
  const btn      = document.getElementById('upload-btn');
  const input    = document.getElementById('upload-input');
  const dropzone = document.getElementById('upload-dropzone');
  const status   = document.getElementById('upload-status');
  const currentFolder = {{ current_path | tojson }} || "";
  let dragCounter = 0;

  function showStatus(text, isError) {
    status.style.display = 'block';
    status.style.borderColor = isError ? '#c0392b' : 'var(--gold)';
    status.textContent = text;
  }

  function uploadFiles(files) {
    const pdfs = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (!pdfs.length) { showStatus('Solo se admiten ficheros .pdf', true); return; }
    const fd = new FormData();
    pdfs.forEach(f => fd.append('files', f));
    fd.append('folder', currentFolder);
    showStatus('Subiendo ' + pdfs.length + ' fichero(s)…', false);
    fetch('/upload', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        if (d.error) { showStatus('⚠ ' + d.error, true); return; }
        showStatus('✓ ' + d.uploaded.length + ' fichero(s) subidos', false);
        setTimeout(() => window.location.reload(), 700);
      })
      .catch(err => showStatus('⚠ Error de red: ' + err.message, true));
  }

  if (btn) btn.addEventListener('click', () => input.click());
  if (input) input.addEventListener('change', () => { if (input.files.length) uploadFiles(input.files); });

  window.addEventListener('dragenter', e => {
    if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
    dragCounter++;
    dropzone.style.display = 'flex';
  });
  window.addEventListener('dragleave', () => {
    dragCounter = Math.max(0, dragCounter - 1);
    if (dragCounter === 0) dropzone.style.display = 'none';
  });
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => {
    e.preventDefault();
    dragCounter = 0;
    dropzone.style.display = 'none';
    if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });
})();

// ── Botón "Generar RAG" (dispara reindexado en Pantallasistemas, si está disponible) ──
(function() {
  const btn      = document.getElementById('rag-btn');
  const statusEl = document.getElementById('rag-status');
  if (!btn) return;
  let poll = null;

  function setStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || 'var(--muted)';
  }

  function pollStatus() {
    fetch('/generar-rag/status').then(r => r.json()).then(d => {
      if (d.proxy_error) { setStatus('⚠ ' + d.proxy_error, '#f87171'); clearInterval(poll); btn.disabled = false; return; }
      if (d.running) { setStatus('Indexando… puede tardar varios minutos.', 'var(--gold)'); return; }
      clearInterval(poll); btn.disabled = false;
      if (d.error) { setStatus('⚠ ' + d.error, '#f87171'); return; }
      setStatus('✓ Reindexado completado.', '#4ade80');
    }).catch(() => {});
  }

  btn.addEventListener('click', () => {
    btn.disabled = true;
    setStatus('Lanzando indexación…', 'var(--muted)');
    fetch('/generar-rag', { method: 'POST' }).then(r => r.json()).then(d => {
      if (!d.ok) { setStatus('⚠ ' + (d.error || 'Error desconocido'), '#f87171'); btn.disabled = false; return; }
      poll = setInterval(pollStatus, 3000);
      pollStatus();
    }).catch(err => { setStatus('⚠ Error de red: ' + err.message, '#f87171'); btn.disabled = false; });
  });
})();

const search  = document.getElementById('search');
const countEl = document.getElementById('count');

function updateCount() {
  if (!countEl) return;
  const n = document.querySelectorAll('.book-card').length;
  countEl.textContent = n ? n + ' libro' + (n !== 1 ? 's' : '') : '';
}

if (search) {
  search.addEventListener('input', () => {
    const q = search.value.trim();
    if (!q) { window.location.href = '/'; return; }
    if (q.length < 2) return;
    window.location.href = '/search?q=' + encodeURIComponent(q);
  });
  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement !== search) {
      e.preventDefault(); search.focus();
    }
  });
}

updateCount();

// Botón fijar portada de sección
document.addEventListener('click', e => {
  const btn = e.target.closest('.btn-pin');
  if (!btn) return;
  const wasPinned = btn.classList.contains('pinned');
  fetch('/set-cover', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({folder: btn.dataset.folder, rel: btn.dataset.rel})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      document.querySelectorAll('.btn-pin[data-folder="' + btn.dataset.folder + '"]')
        .forEach(b => b.classList.remove('pinned'));
      if (!wasPinned) btn.classList.add('pinned');
    }
  });
});

// ── Esquema de partida ──────────────────────────────────────────────────────
(function() {
  const modal   = document.getElementById('esquema-modal');
  const mTitle  = modal.querySelector('.esquema-title');
  const mStatus = modal.querySelector('.esquema-status');
  const mFiles  = modal.querySelector('.esquema-files');
  const mErr    = modal.querySelector('.esquema-err');
  const mClose  = modal.querySelector('.esquema-close');
  let pollTimer       = null;
  let elapsedTimer    = null;
  let elapsedSecs     = 0;
  let currentRel      = null;
  let pausedTransRel  = null;  // rel_path de la traducción que pausamos

  function resetModal(name) {
    mTitle.textContent = name;
    mStatus.innerHTML  = '';
    mFiles.innerHTML   = '';
    mErr.textContent   = '';
    mClose.disabled    = true;
    pausedTransRel     = null;
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
    elapsedSecs = 0;
    modal.classList.add('open');
  }

  function closeModal() {
    if (pollTimer)   { clearInterval(pollTimer);   pollTimer   = null; }
    if (elapsedTimer){ clearInterval(elapsedTimer); elapsedTimer = null; }
    modal.classList.remove('open');
    currentRel = null;
  }

  function startElapsed() {
    elapsedSecs = 0;
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      elapsedSecs++;
      const m = Math.floor(elapsedSecs / 60), s = elapsedSecs % 60;
      const t = m > 0 ? m + 'm ' + s + 's' : s + 's';
      const hint = elapsedSecs > 90 ? ' — Ollama puede tardar varios minutos con PDFs largos' : '';
      mStatus.innerHTML = '<span class="esquema-spinner"></span> Generando esquema con Ollama… <span class="esquema-elapsed">(' + t + hint + ')</span>';
    }, 1000);
  }

  function showWarning(runningRel, bookName, esquemaRel) {
    const runningName = runningRel.split('/').pop().replace(/[.]pdf$/i, '');
    mStatus.innerHTML =
      '<div class="esquema-warn">⚠️ Ollama está traduciendo <strong>' + runningName + '</strong>.<br>' +
      'Puedes pausar la traducción para que el esquema sea más rápido.</div>' +
      '<div class="esquema-warn-btns">' +
        '<button class="esquema-act-btn" id="eq-pause-btn">⏸ Pausar y generar</button>' +
        '<button class="esquema-act-btn esquema-act-sec" id="eq-anyway-btn">Generar igualmente</button>' +
      '</div>';
    modal.querySelector('#eq-pause-btn').onclick = () => {
      mStatus.innerHTML = '<span class="esquema-spinner"></span> Pausando traducción…';
      fetch('/translate-bg/' + runningRel + '/pause', { method: 'POST' })
        .then(() => { pausedTransRel = runningRel; startGenerar(esquemaRel); });
    };
    modal.querySelector('#eq-anyway-btn').onclick = () => startGenerar(esquemaRel);
  }

  function startGenerar(rel) {
    mStatus.innerHTML = '<span class="esquema-spinner"></span> Extrayendo texto del PDF…';
    mFiles.innerHTML  = '';
    mErr.textContent  = '';
    fetch('/generar-esquema/' + rel, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.error) { mStatus.textContent = ''; mErr.textContent = d.error; showClose(); return; }
        mStatus.innerHTML = '<span class="esquema-spinner"></span> Generando esquema con Ollama…';
        startElapsed();
        pollTimer = setInterval(() => pollStatus(rel), 3000);
      })
      .catch(err => { mStatus.textContent = ''; mErr.textContent = String(err); showClose(); });
  }

  function pollStatus(rel) {
    fetch('/esquema-status/' + rel)
      .then(r => r.json())
      .then(d => {
        if (d.status === 'running') {
          mStatus.innerHTML = '<span class="esquema-spinner"></span> ' + (d.msg || 'Generando…');
        } else if (d.status === 'done') {
          clearInterval(pollTimer); pollTimer = null;
          if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
          mStatus.textContent = d.msg || 'Esquema generado.';
          mFiles.innerHTML = '<ul class="esquema-files">' +
            (d.files || []).map(f => '<li>' + f + '</li>').join('') + '</ul>';
          if (pausedTransRel) {
            const transName = pausedTransRel.split('/').pop().replace(/[.]pdf$/i, '');
            fetch('/translate-bg/' + pausedTransRel + '/resume', { method: 'POST' });
            mFiles.innerHTML += '<div class="esquema-resumed">▶ Traducción de «' + transName + '» reanudada automáticamente</div>';
            pausedTransRel = null;
          }
          showClose();
        } else if (d.status === 'error') {
          clearInterval(pollTimer); pollTimer = null;
          if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
          mErr.textContent = 'Error: ' + d.msg;
          if (pausedTransRel) {
            fetch('/translate-bg/' + pausedTransRel + '/resume', { method: 'POST' });
            pausedTransRel = null;
          }
          showClose();
        }
      });
  }

  function showClose() { mClose.disabled = false; }

  document.addEventListener('click', e => {
    const btn = e.target.closest('.btn-esquema');
    if (!btn) return;
    const rel  = btn.dataset.rel;
    const name = btn.dataset.name;
    currentRel = rel;
    resetModal(name);
    // Comprobar si hay traducción en curso
    fetch('/translate-queue')
      .then(r => r.json())
      .then(d => {
        if (d.running) {
          showWarning(d.running.rel_path, name, rel);
        } else {
          startGenerar(rel);
        }
      })
      .catch(() => startGenerar(rel));
  });

  mClose.addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
})();
</script>

</body>
</html>
"""

HTML_VIEWER = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>{{ title }} — Pargen</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#030620">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #030620; --surface: #080d2e; --card: #0d1340;
    --blue: #0161ef; --purple: #6d28d9;
    --text: #f7f8f8; --muted: rgba(229,236,246,.55); --border: rgba(1,97,239,.2);
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    display: flex; flex-direction: column; height: 100dvh; overflow: hidden;
  }

  /* toolbar */
  #toolbar {
    background: rgba(3,6,32,.95); backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 0 1rem; height: 56px; flex-shrink: 0;
    display: flex; align-items: center; gap: 0.5rem;
  }
  .logo-sm { display: flex; align-items: center; gap: 0.5rem; text-decoration: none; flex-shrink: 0; }
  .logo-sm img { height: 28px; width: auto; }
  .logo-sm span { font-weight: 700; font-size: 0.85rem; color: var(--text); }
  .divider { width: 1px; height: 24px; background: var(--border); flex-shrink: 0; margin: 0 0.3rem; }
  #book-title {
    flex: 1; font-size: 0.82rem; font-weight: 500; color: var(--muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
  }
  .tbtn {
    background: var(--card); border: 1px solid var(--border);
    color: var(--text); padding: 0.35rem 0.65rem; border-radius: 8px;
    font-size: 0.82rem; font-weight: 600; cursor: pointer; font-family: inherit;
    text-decoration: none; white-space: nowrap; transition: all .15s;
    display: flex; align-items: center; gap: 0.25rem; flex-shrink: 0;
  }
  .tbtn:hover { background: rgba(1,97,239,.2); border-color: var(--blue); }
  .tbtn.accent { background: var(--blue); border-color: var(--blue); color: #fff; }
  .tbtn.accent:hover { background: #0154cf; }
  .tbtn:disabled { opacity: .35; cursor: default; pointer-events: none; }
  #page-input {
    width: 3.2rem; background: var(--card); border: 1px solid var(--border);
    color: var(--text); padding: 0.3rem 0.4rem; border-radius: 7px;
    font-size: 0.82rem; text-align: center; font-family: inherit;
  }
  #page-total { color: var(--muted); font-size: 0.8rem; white-space: nowrap; }

  /* viewer — movido al bloque del panel */
  canvas {
    display: block; background: #fff;
    border-radius: 4px; box-shadow: 0 8px 40px rgba(0,0,0,.6), 0 0 0 1px rgba(1,97,239,.1);
    transform-origin: center center;
    will-change: transform;
  }

  /* loading overlay */
  #loading {
    position: fixed; inset: 0; background: rgba(3,6,32,.92);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 1.2rem; z-index: 100;
  }
  .spinner {
    width: 48px; height: 48px; border-radius: 50%;
    border: 3px solid rgba(1,97,239,.2); border-top-color: var(--blue);
    animation: spin .75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  #load-status { color: var(--muted); font-size: 0.85rem; }
  #error { display: none; padding: 2rem; text-align: center; color: #f87171; }

  /* panel traducción */
  #wrap { flex: 1; display: flex; overflow: hidden; }
  #viewer {
    flex: 1; overflow: hidden; position: relative;
    display: flex; align-items: center; justify-content: center;
    background-image: radial-gradient(ellipse 60% 30% at 50% 0%, rgba(1,97,239,.06) 0%, transparent 60%);
    touch-action: none;
  }
  #trans-panel {
    width: 0; overflow: hidden; transition: width .3s ease;
    background: #080d2e; border-left: 1px solid var(--border);
    display: flex; flex-direction: column;
  }
  #trans-panel.open { width: min(380px, 45vw); }
  @media (max-width: 600px) { #trans-panel.open { width: 100vw; position: fixed; inset: 56px 0 0 0; z-index: 20; } }
  @media (max-width: 640px) {
    #toolbar {
      overflow-x: auto; overflow-y: hidden;
      scrollbar-width: none; -webkit-overflow-scrolling: touch;
      gap: 0.3rem; padding: 0 0.5rem;
    }
    #toolbar::-webkit-scrollbar { display: none; }
    #book-title   { display: none; }
    .divider      { display: none; }
    #page-total   { display: none; }
    .back-lbl     { display: none; }
    .logo-sm span { display: none; }
    .logo-sm img  { height: 24px; }
    #btn-zoom-in  { display: flex; }
    #btn-zoom-out { display: flex; }
    .tbtn { padding: 0.35rem 0.5rem; font-size: 0.8rem; flex-shrink: 0; }
    #page-input { width: 2.6rem; flex-shrink: 0; }
  }
  .trans-header {
    padding: 0.7rem 1rem; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; flex-wrap: wrap;
  }
  .trans-header .th-title { font-size: 0.78rem; font-weight: 600; color: var(--muted);
    letter-spacing: .06em; text-transform: uppercase; flex: 1; }
  .engine-toggle { display: flex; background: #0a0f2e; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .engine-btn {
    padding: 0.25rem 0.6rem; font-size: 0.72rem; font-weight: 600; cursor: pointer;
    border: none; background: none; color: var(--muted); font-family: inherit; transition: all .15s;
  }
  .engine-btn.active { background: var(--blue); color: #fff; }
  .engine-btn:disabled { opacity: .35; cursor: default; }
  .trans-close { background: none; border: none; color: var(--muted); font-size: 1rem;
    cursor: pointer; padding: 0.2rem 0.4rem; margin-left: auto; }
  .trans-close:hover { color: var(--text); }

  #trans-body { flex: 1; overflow-y: auto; padding: 1.2rem; }
  #trans-body p { font-size: 0.88rem; line-height: 1.75; color: rgba(229,236,246,.85);
    margin-bottom: 1em; }
  #trans-body p:last-child { margin-bottom: 0; }
  .trans-loading { display: flex; align-items: center; gap: 0.6rem; color: var(--muted);
    font-size: 0.82rem; padding: 0.5rem 0; }
  .trans-loading::before { content: ''; width: 14px; height: 14px; flex-shrink: 0;
    border: 2px solid rgba(1,97,239,.25); border-top-color: var(--blue);
    border-radius: 50%; animation: spin .7s linear infinite; }
  .trans-warning { font-size: 0.75rem; color: #fbbf24; margin-bottom: 0.8rem;
    padding: 0.4rem 0.6rem; background: rgba(251,191,36,.08); border-radius: 6px;
    border: 1px solid rgba(251,191,36,.2); }
  .trans-engine-tag { font-size: 0.68rem; color: var(--muted); margin-bottom: 0.8rem; }
  .tbtn.active { background: rgba(1,97,239,.3); border-color: var(--blue); }
  /* traducción completa en 2º plano */
  #full-trans-bar {
    padding: 0.6rem 1rem 0.7rem; border-bottom: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 0.45rem; flex-shrink: 0;
  }
  #btn-full-trans { width: 100%; justify-content: center; font-size: 0.78rem; }
  #full-trans-info { font-size: 0.74rem; color: var(--muted); }
  .prog-bar { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .prog-fill { height: 100%; background: var(--blue); border-radius: 2px; transition: width .4s; }

  /* notas y guardado */
  #notes-section {
    flex-shrink: 0; padding: 0.8rem 1.2rem 1rem;
    border-top: 1px solid var(--border);
    background: rgba(0,0,0,.15);
  }
  .notes-label { font-size: 0.72rem; font-weight: 600; color: var(--muted);
    letter-spacing: .06em; text-transform: uppercase; margin-bottom: 0.4rem; }
  .notes-area {
    width: 100%; min-height: 80px;
    background: rgba(255,255,255,.04); border: 1px solid var(--border);
    color: var(--text); padding: 0.55rem 0.7rem; border-radius: 6px;
    font-family: inherit; font-size: 0.83rem; line-height: 1.55;
    resize: vertical; outline: none; transition: border-color .2s;
  }
  .notes-area:focus { border-color: var(--blue); }
  .notes-area::placeholder { color: var(--muted); }
  .save-row { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.5rem; flex-wrap: wrap; }
  .save-status { font-size: 0.74rem; flex: 1; }
</style>
</head>
<body>

<div id="loading">
  <div class="spinner"></div>
  <p id="load-status">Cargando PDF…</p>
</div>

<div id="toolbar">
  <a class="logo-sm" href="/">
    <img src="/static/logo.webp" alt="Pargen" onerror="this.style.display='none'">
    <span>Pargen</span>
  </a>
  <div class="divider"></div>
  <div id="book-title">{{ title }}</div>
  <a class="tbtn" href="{{ back_url }}">← <span class="back-lbl">Volver</span></a>
  <button class="tbtn" id="btn-prev" disabled>‹</button>
  <input id="page-input" type="number" min="1" value="1">
  <span id="page-total">/ —</span>
  <button class="tbtn" id="btn-next" disabled>›</button>
  <button class="tbtn" id="btn-zoom-out" title="Reducir">−</button>
  <button class="tbtn" id="btn-zoom-in" title="Ampliar">+</button>
  <button class="tbtn" id="btn-col" title="Modo columna — zoom para leer libros a doble columna">▐</button>
  <a class="tbtn" href="/jobs" title="Estado de traducciones">📚</a>
  <button class="tbtn" id="btn-trans" title="Traducir al español">🌐 ES</button>
  <a class="tbtn accent" href="/download/{{ rel }}" download>⬇</a>
</div>

<div id="wrap">
  <div id="viewer"></div>
  <div id="trans-panel">
    <div class="trans-header">
      <span class="th-title">Traducción y notas</span>
      <div class="engine-toggle">
        <button class="engine-btn active" id="eng-ollama" data-engine="ollama">Ollama</button>
        <button class="engine-btn" id="eng-claude" data-engine="claude">Claude</button>
        <button class="engine-btn" id="eng-argos" data-engine="argos">Argos</button>
      </div>
      <button class="trans-close" id="trans-close">✕</button>
    </div>
    <div id="full-trans-bar">
      <button class="tbtn" id="btn-full-trans">📚 Traducir PDF completo en 2.º plano</button>
      <div id="full-trans-info"></div>
      <div class="prog-bar" id="full-prog-bar" style="display:none">
        <div class="prog-fill" id="full-prog-fill" style="width:0%"></div>
      </div>
    </div>
    <div id="trans-body"></div>
    <div id="notes-section">
      <p class="notes-label">Notas — pág. <span id="notes-page">—</span></p>
      <textarea class="notes-area" id="trans-notes" placeholder="Apuntes sobre esta página… (sin traducción también se puede guardar)"></textarea>
      <div class="save-row">
        <button class="tbtn" id="btn-save-note" style="font-size:0.78rem">📓 Guardar en Obsidian</button>
        <span class="save-status" id="save-status"></span>
      </div>
    </div>
  </div>
</div>
<div id="error"></div>

<script type="module">
import * as pdfjsLib from 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs';
pdfjsLib.GlobalWorkerOptions.workerSrc =
  'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs';

// ── Visor página única ──────────────────────────────────────────────────────

const PDF_URL    = '/pdf/{{ rel_encoded }}';
const viewer     = document.getElementById('viewer');
const loading    = document.getElementById('loading');
const loadStatus = document.getElementById('load-status');
const errDiv     = document.getElementById('error');
const btnPrev    = document.getElementById('btn-prev');
const btnNext    = document.getElementById('btn-next');
const pageInput  = document.getElementById('page-input');
const pageTotal  = document.getElementById('page-total');

// ── Estado visor página única ───────────────────────────────────────────────
let pdfDoc = null, currentPage = 1;
let zoom = 1.0, panX = 0, panY = 0;
let _rgen = 0;  // generación de render para cancelar renders obsoletos

// Canvas único
const canvas = document.createElement('canvas');
canvas.style.display = 'block';
canvas.style.background = '#fff';
canvas.style.borderRadius = '4px';
canvas.style.boxShadow = '0 8px 40px rgba(0,0,0,.6), 0 0 0 1px rgba(1,97,239,.1)';
canvas.style.transformOrigin = 'center center';
canvas.style.willChange = 'transform';
viewer.appendChild(canvas);

function applyTransform() {
  canvas.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
}

function clampPan() {
  const vw = viewer.clientWidth, vh = viewer.clientHeight;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  const maxX = Math.max(0, (cw * zoom - vw) / 2);
  const maxY = Math.max(0, (ch * zoom - vh) / 2);
  panX = Math.max(-maxX, Math.min(maxX, panX));
  panY = Math.max(-maxY, Math.min(maxY, panY));
}

async function fitScale(page) {
  const base = page.getViewport({ scale: 1 });
  return Math.min(viewer.clientWidth / base.width, viewer.clientHeight / base.height);
}

async function renderCurrentPage() {
  if (!pdfDoc) return;
  const gen = ++_rgen;
  try {
    const page  = await pdfDoc.getPage(currentPage);
    const scale = await fitScale(page);
    const dpr   = window.devicePixelRatio || 1;
    const vp    = page.getViewport({ scale });
    if (gen !== _rgen) return;
    canvas.width  = Math.round(vp.width  * dpr);
    canvas.height = Math.round(vp.height * dpr);
    canvas.style.width  = Math.round(vp.width)  + 'px';
    canvas.style.height = Math.round(vp.height) + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    await page.render({ canvasContext: ctx, viewport: vp }).promise;
    if (gen !== _rgen) return;
    applyTransform();
  } catch(e) {}
}

function goToPage(n) {
  if (!pdfDoc) return;
  n = Math.max(1, Math.min(pdfDoc.numPages, n));
  currentPage = n;
  pageInput.value = n;
  btnPrev.disabled = n <= 1;
  btnNext.disabled = n >= pdfDoc.numPages;
  zoom = 1.0; panX = 0; panY = 0;
  canvas.style.transform = '';
  resetNotes();
  if (transOpen) translatePage(n);
  renderCurrentPage();
}
btnPrev.addEventListener('click', () => goToPage(currentPage - 1));
btnNext.addEventListener('click', () => goToPage(currentPage + 1));
pageInput.addEventListener('change', () => goToPage(+pageInput.value || 1));

// Teclado
window.addEventListener('keydown', e => {
  if (document.activeElement === pageInput) return;
  if (e.key === 'ArrowRight' || e.key === 'PageDown') goToPage(currentPage + 1);
  if (e.key === 'ArrowLeft'  || e.key === 'PageUp')   goToPage(currentPage - 1);
});

// ── Zoom botones ────────────────────────────────────────────────────────────
document.getElementById('btn-zoom-in').addEventListener('click', () => {
  zoom = Math.min(zoom * 1.4, 6); clampPan(); applyTransform();
});
document.getElementById('btn-zoom-out').addEventListener('click', () => {
  zoom = Math.max(zoom / 1.4, 0.5);
  if (zoom <= 0.55) { zoom = 1.0; panX = 0; panY = 0; }
  clampPan(); applyTransform();
});

// Ctrl+rueda del ratón
viewer.addEventListener('wheel', e => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  zoom = e.deltaY < 0 ? Math.min(zoom * 1.12, 6) : Math.max(zoom / 1.12, 0.5);
  clampPan(); applyTransform();
}, { passive: false });

// Modo columna: zoom para ver una columna (la izquierda) de libros a doble columna
document.getElementById('btn-col').addEventListener('click', async () => {
  const btn = document.getElementById('btn-col');
  if (btn.classList.toggle('active') && pdfDoc) {
    const page = await pdfDoc.getPage(currentPage);
    const fit  = await fitScale(page);
    const base = page.getViewport({ scale: 1 });
    const canvasCSSWidth = base.width * fit;
    // Zoom para que la página ocupe 2x el ancho del visor (cada columna = 1 visor)
    zoom = (2 * viewer.clientWidth) / canvasCSSWidth;
    // panX positivo = desplazar canvas a la derecha = ver lado izquierdo de la página
    panX = viewer.clientWidth / 2;
    panY = 0;
    clampPan(); applyTransform();
  } else {
    zoom = 1.0; panX = 0; panY = 0; applyTransform();
  }
});

// Resize
let _resizeT;
window.addEventListener('resize', () => {
  clearTimeout(_resizeT);
  _resizeT = setTimeout(() => { zoom = 1; panX = 0; panY = 0; renderCurrentPage(); }, 300);
});

// ── Gestos táctiles ─────────────────────────────────────────────────────────
let _touch = null;

viewer.addEventListener('touchstart', e => {
  if (e.touches.length === 1) {
    _touch = {
      type: 'single',
      x0: e.touches[0].clientX, y0: e.touches[0].clientY,
      panX0: panX, panY0: panY,
    };
  } else if (e.touches.length === 2) {
    _touch = {
      type: 'pinch',
      dist0: Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY),
      zoom0: zoom, panX0: panX, panY0: panY,
    };
  }
}, { passive: true });

viewer.addEventListener('touchmove', e => {
  if (!_touch) return;
  if (_touch.type === 'pinch' && e.touches.length === 2) {
    e.preventDefault();
    const d = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY);
    zoom = Math.max(0.5, Math.min(6, _touch.zoom0 * d / _touch.dist0));
    clampPan(); applyTransform();
  } else if (_touch.type === 'single' && e.touches.length === 1 && zoom > 1.05) {
    panX = _touch.panX0 + e.touches[0].clientX - _touch.x0;
    panY = _touch.panY0 + e.touches[0].clientY - _touch.y0;
    clampPan(); applyTransform();
  }
}, { passive: false });

viewer.addEventListener('touchend', e => {
  if (!_touch) return;
  if (_touch.type === 'single' && zoom <= 1.05 && e.changedTouches.length) {
    const dx = e.changedTouches[0].clientX - _touch.x0;
    const dy = e.changedTouches[0].clientY - _touch.y0;
    if (Math.abs(dx) > Math.abs(dy) * 1.5 && Math.abs(dx) > 60) {
      if (dx < 0) goToPage(currentPage + 1);
      else        goToPage(currentPage - 1);
    }
  }
  if (e.touches.length === 0) _touch = null;
}, { passive: true });

// ── Carga ───────────────────────────────────────────────────────────────────
async function loadPDF() {
  try {
    const task = pdfjsLib.getDocument(PDF_URL);
    task.onProgress = ({ loaded, total }) => {
      if (total) loadStatus.textContent = `Cargando… ${Math.round(loaded / total * 100)}%`;
    };
    pdfDoc = await task.promise;
    const n = pdfDoc.numPages;
    pageTotal.textContent = '/ ' + n;
    pageInput.max = n;
    loading.style.display = 'none';
    const initPage = parseInt(new URLSearchParams(location.search).get('page') || '1', 10);
    goToPage(Math.max(1, Math.min(n, initPage)));
  } catch(e) {
    loading.style.display = 'none';
    errDiv.style.display  = 'block';
    errDiv.textContent    = 'Error al cargar el PDF: ' + e.message;
  }
}

// ── Traducción ──
const btnTrans   = document.getElementById('btn-trans');
const transPanel = document.getElementById('trans-panel');
const transBody  = document.getElementById('trans-body');
const transClose = document.getElementById('trans-close');
const REL        = '{{ rel_encoded }}';

let transOpen = false, lastTransKey = null, currentEngine = 'ollama';
let activeSSE = null;  // EventSource activo
let lastTransText = '';  // último texto traducido (para guardar)

// Comprobar motores disponibles
fetch('/translate/status').then(r => r.json()).then(d => {
  const btnOllama = document.getElementById('eng-ollama');
  const btnClaude = document.getElementById('eng-claude');
  if (!d.ollama) {
    btnOllama.disabled = true;
    btnOllama.title = 'Ollama no disponible';
  }
  if (!d.claude) {
    btnClaude.disabled = true;
    btnClaude.title = 'Configura ANTHROPIC_API_KEY en .env para usar Claude';
  }
  if (!d.ollama) {
    // Elegir el primer motor utilizable como activo por defecto
    const fallback = d.claude ? 'claude' : 'argos';
    currentEngine = fallback;
    document.getElementById('eng-' + fallback).classList.add('active');
    btnOllama.classList.remove('active');
  }
});

document.querySelectorAll('.engine-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.disabled) return;
    currentEngine = btn.dataset.engine;
    document.querySelectorAll('.engine-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    lastTransKey = null;
    if (transOpen) translatePage(currentPage);
  });
});

function engineLabel(e) {
  if (e === 'ollama') return 'Ollama · qwen2.5:7b';
  if (e === 'claude') return 'Claude · haiku 4.5';
  return 'Argos Translate (offline)';
}

const notesArea  = document.getElementById('trans-notes');
const saveStatus = document.getElementById('save-status');
const notesPage  = document.getElementById('notes-page');

function resetNotes() {
  notesArea.value    = '';
  saveStatus.textContent = '';
  notesPage.textContent  = currentPage;
}

document.getElementById('btn-save-note').addEventListener('click', async () => {
  const notes   = notesArea.value.trim();
  const btnSave = document.getElementById('btn-save-note');
  if (!lastTransText && !notes) { saveStatus.textContent = 'Escribe algo antes de guardar.'; saveStatus.style.color = 'var(--muted)'; return; }
  btnSave.disabled = true;
  saveStatus.textContent = 'Guardando…';
  saveStatus.style.color = 'var(--muted)';
  try {
    const r = await fetch('/save-note/{{ rel_encoded }}', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: currentPage, translation: lastTransText, notes, engine: currentEngine })
    });
    const d = await r.json();
    if (d.ok) {
      saveStatus.textContent = '✓ ' + d.file;
      saveStatus.style.color = '#4ade80';
    } else {
      saveStatus.textContent = '✗ ' + (d.error || 'Error');
      saveStatus.style.color = '#f87171';
    }
  } catch(e) {
    saveStatus.textContent = '✗ Error de red';
    saveStatus.style.color = '#f87171';
  }
  btnSave.disabled = false;
});

function renderStatic(data) {
  if (data.error) { transBody.innerHTML = '<p style="color:#f87171">Error: ' + data.error + '</p>'; return; }
  if (data.empty) { transBody.innerHTML = '<p style="color:var(--muted)">Página sin texto extraíble (imagen escaneada).</p>'; return; }
  let html = '';
  if (data.warning) html += '<div class="trans-warning">⚠ ' + data.warning + '</div>';
  html += '<div class="trans-engine-tag">Página ' + currentPage + ' · ' + engineLabel(data.engine) + '</div>';
  html += data.translated.split(/\\n\\n+/).map(p => '<p>' + p.trim() + '</p>').join('');
  transBody.innerHTML = html;
  transBody.scrollTop = 0;
  lastTransText = data.translated;
}

async function translatePage(pageNum) {
  const key = pageNum + ':' + currentEngine;
  if (key === lastTransKey) return;
  lastTransKey = key;
  lastTransText = '';

  // Cancelar SSE anterior si existe
  if (activeSSE) { activeSSE.close(); activeSSE = null; }

  transBody.innerHTML =
    '<div class="trans-engine-tag">Motor: ' + engineLabel(currentEngine) + '</div>' +
    '<div class="trans-loading">Enviando texto a ' + engineLabel(currentEngine) + '…</div>' +
    '<p style="font-size:0.75rem;color:var(--muted);margin-top:0.5rem">La primera traducción puede tardar unos segundos.</p>';

  const url = '/translate/' + REL + '?page=' + pageNum + '&engine=' + currentEngine;

  if (currentEngine === 'ollama' || currentEngine === 'claude') {
    // Streaming SSE
    let textDiv = null;
    let buffer  = '';
    activeSSE = new EventSource(url);
    activeSSE.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.error) {
        activeSSE.close(); activeSSE = null;
        transBody.innerHTML = '<p style="color:#f87171">Error: ' + data.error + '</p>';
        return;
      }
      if (data.token !== undefined) {
        // Primera vez: quitar spinner y crear contenedor de texto
        if (!textDiv) {
          transBody.innerHTML =
            '<div class="trans-engine-tag">Motor: ' + engineLabel(currentEngine) + '</div>' +
            '<div id="trans-stream"></div>';
          textDiv = document.getElementById('trans-stream');
        }
        buffer += data.token;
        // Renderizar párrafos progresivamente
        const paras = buffer.split(/\\n\\n+/);
        textDiv.innerHTML = paras.map((p, i) =>
          i < paras.length - 1
            ? '<p>' + p.trim() + '</p>'   // párrafo completo
            : '<p>' + p + '▌</p>'         // cursor en el último
        ).join('');
      }
      if (data.done) {
        activeSSE.close(); activeSSE = null;
        if (textDiv) {
          textDiv.innerHTML = buffer.split(/\\n\\n+/).map(p => '<p>' + p.trim() + '</p>').join('');
          // Actualizar etiqueta con estadísticas de tokens
          const tag = transBody.querySelector('.trans-engine-tag');
          if (tag && data.stats) {
            const s = data.stats;
            const tps = s.eval_duration > 0
              ? (s.gen_tokens / (s.eval_duration / 1e9)).toFixed(1) + ' t/s'
              : '';
            const parts = [engineLabel(currentEngine)];
            if (s.prompt_tokens) parts.push('entrada: ' + s.prompt_tokens + ' tok');
            if (s.gen_tokens)    parts.push('salida: ' + s.gen_tokens + ' tok');
            if (tps)             parts.push(tps);
            tag.textContent = 'Motor: ' + parts.join(' · ');
          }
        }
        lastTransText = buffer;
      }
    };
    activeSSE.onerror = () => {
      activeSSE.close(); activeSSE = null;
      if (!buffer) transBody.innerHTML = '<p style="color:#f87171">Error de conexión con Ollama.</p>';
    };
  } else {
    // Argos: respuesta JSON normal
    try {
      const r = await fetch(url);
      renderStatic(await r.json());
    } catch(e) {
      transBody.innerHTML = '<p style="color:#f87171">Error: ' + e.message + '</p>';
    }
  }
}

btnTrans.addEventListener('click', () => {
  transOpen = !transOpen;
  transPanel.classList.toggle('open', transOpen);
  btnTrans.classList.toggle('active', transOpen);
  if (transOpen) { notesPage.textContent = currentPage; translatePage(currentPage); }
  else if (activeSSE) { activeSSE.close(); activeSSE = null; }
});

transClose.addEventListener('click', () => {
  transOpen = false;
  transPanel.classList.remove('open');
  btnTrans.classList.remove('active');
  lastTransKey = null;
  if (activeSSE) { activeSSE.close(); activeSSE = null; }
});

// ── Traducción completa en segundo plano ──
const btnFullTrans  = document.getElementById('btn-full-trans');
const fullTransInfo = document.getElementById('full-trans-info');
const fullProgBar   = document.getElementById('full-prog-bar');
const fullProgFill  = document.getElementById('full-prog-fill');
let fullPoll = null;

function setFullUI(status, page, total, error, queuePos) {
  if (status === 'running') {
    const pct = total ? Math.round(page / total * 100) : 0;
    fullTransInfo.textContent = `⏳ Página ${page} / ${total} (${pct}%)`;
    fullTransInfo.style.color = 'var(--muted)';
    fullProgBar.style.display = 'block';
    fullProgFill.style.width  = pct + '%';
    btnFullTrans.textContent  = '📚 Traducción en curso…';
    btnFullTrans.disabled = true;
  } else if (status === 'queued') {
    fullTransInfo.textContent = `⌛ En cola — posición ${queuePos}`;
    fullTransInfo.style.color = '#fbbf24';
    fullProgBar.style.display = 'none';
    btnFullTrans.textContent  = '📚 En cola — quitar';
    btnFullTrans.disabled = false;
  } else if (status === 'done') {
    fullProgBar.style.display = 'none';
    if (error) {
      fullTransInfo.textContent = '✗ Error: ' + error;
      fullTransInfo.style.color = '#f87171';
      btnFullTrans.textContent  = '📚 Reintentar traducción';
      btnFullTrans.disabled = false;
    } else {
      fullTransInfo.textContent = '✓ Guardada en Obsidian — pulsa para repetir desde cero';
      fullTransInfo.style.color = '#4ade80';
      btnFullTrans.textContent  = '🔄 Repetir traducción';
      btnFullTrans.disabled = false;
    }
    if (fullPoll) { clearInterval(fullPoll); fullPoll = null; }
  } else {
    fullTransInfo.textContent = '';
    fullProgBar.style.display = 'none';
    btnFullTrans.textContent  = '📚 Traducir PDF completo en 2.º plano';
    btnFullTrans.disabled = false;
  }
}

async function checkFullStatus() {
  try {
    const r = await fetch('/translate-status/{{ rel_encoded }}');
    const d = await r.json();
    setFullUI(d.status, d.page, d.total, d.error, d.queue_pos);
    return d.status;
  } catch(e) { return null; }
}

btnFullTrans.addEventListener('click', async () => {
  const curStatus = fullTransInfo.textContent;
  // Si está en cola, al pulsar lo quitamos
  if (curStatus.includes('En cola')) {
    try {
      await fetch('/translate-bg/{{ rel_encoded }}', { method: 'DELETE' });
      setFullUI('idle', 0, 0, null, null);
      if (fullPoll) { clearInterval(fullPoll); fullPoll = null; }
    } catch(e) {}
    return;
  }
  const isRepeat = curStatus.includes('Repetir') || curStatus.includes('Reintentar') || curStatus.includes('repetir');
  const msg = isRepeat
    ? '¿Repetir la traducción desde cero? Se sobreescribirá el markdown en Obsidian.'
    : '¿Iniciar traducción completa del PDF en segundo plano? Puede tardar mucho tiempo. Puedes seguir usando la app mientras.';
  if (!confirm(msg)) return;
  btnFullTrans.disabled = true;
  fullTransInfo.textContent = 'Añadiendo a la cola…';
  fullTransInfo.style.color = 'var(--muted)';
  const url = '/translate-bg/{{ rel_encoded }}' + (isRepeat ? '?reset=true' : '');
  try {
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      if (d.position === 1) {
        setFullUI('running', 0, d.total, null, null);
      } else {
        setFullUI('queued', 0, d.total, null, d.position);
      }
      if (!fullPoll) fullPoll = setInterval(checkFullStatus, 3000);
    } else {
      fullTransInfo.textContent = '✗ ' + (d.error || 'Error');
      fullTransInfo.style.color = '#f87171';
      btnFullTrans.disabled = false;
    }
  } catch(e) {
    fullTransInfo.textContent = '✗ Error de red';
    fullTransInfo.style.color = '#f87171';
    btnFullTrans.disabled = false;
  }
});

// Comprobar si hay un job en curso al cargar la página
checkFullStatus().then(status => {
  if (status === 'running' || status === 'queued') fullPoll = setInterval(checkFullStatus, 3000);
});

loadPDF();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def count_pdfs(path: Path) -> int:
    return sum(1 for f in path.rglob("*") if f.suffix.lower() == ".pdf")

def safe_path(rel_path: str) -> Path:
    target = (BIBLIOTECA / rel_path).resolve()
    try:
        target.relative_to(BIBLIOTECA.resolve())
    except ValueError:
        abort(403)
    return target

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

FOLDER_ICONS = {
    "general":                    "🗂️",
    "dungeons":                   "⚔️",
    "mundo de tinieblas":         "🧛",
    "libroslibrojuegos":          "📖",
    "librojuegos":                "📖",
    "epub":                       "📖",
    "otros":                      "🎲",
    "peticiones":                 "📬",
    "pregenerados":               "🧙",
}

def folder_icon(name: str) -> str:
    low = name.lower()
    for key, icon in FOLDER_ICONS.items():
        if key in low:
            return icon
    return "📁"

def folder_cover(folder_path: Path) -> str | None:
    """Devuelve la URL de miniatura: primero el config, luego el primer PDF disponible."""
    folder_name = folder_path.name
    covers = _load_covers()
    if folder_name in covers:
        url = get_thumb_url(covers[folder_name])
        if url:
            return url
    for pdf in sorted(folder_path.rglob("*.pdf")):
        rel = str(pdf.relative_to(BIBLIOTECA))
        url = get_thumb_url(rel)
        if url:
            return url
    return None

@app.route("/")
def index():
    folders = []
    for d in sorted(BIBLIOTECA.iterdir()):
        if d.is_dir():
            folders.append({"name": d.name, "rel": d.name, "count": count_pdfs(d), "icon": folder_icon(d.name), "cover": folder_cover(d)})
    return render_template_string(HTML_LIBRARY,
        folders=folders, books=[], current_path=None, breadcrumb=[], query="", pinned_covers={}, in_aventuras=True, version=VERSION)

@app.route("/browse/<path:rel_path>")
def browse(rel_path):
    target = BIBLIOTECA / rel_path
    if not target.exists() or not target.is_dir():
        abort(404)
    folders = []
    for d in sorted(target.iterdir()):
        if d.is_dir():
            folders.append({
                "name": d.name,
                "rel": str(Path(rel_path) / d.name),
                "count": count_pdfs(d),
                "icon": folder_icon(d.name),
                "cover": folder_cover(d),
            })
    books = []
    for f in sorted(target.iterdir()):
        if f.is_file() and f.suffix.lower() == ".pdf":
            rel = f.relative_to(BIBLIOTECA)
            r = str(rel)
            books.append({"name": f.stem, "rel": r, "folder": target.name, "thumb": get_thumb_url(r)})
    parts = Path(rel_path).parts
    breadcrumb = [{"name": p, "path": "/".join(parts[:i+1])} for i, p in enumerate(parts)]
    in_aventuras = True
    return render_template_string(HTML_LIBRARY,
        folders=folders, books=books, current_path=rel_path,
        breadcrumb=breadcrumb, query="", pinned_covers=_load_covers(), in_aventuras=in_aventuras, version=VERSION)

@app.route("/upload", methods=["POST"])
def upload_files():
    folder = (request.form.get("folder") or "").strip()
    dest_dir = BIBLIOTECA
    if folder:
        candidate = (BIBLIOTECA / folder).resolve()
        try:
            candidate.relative_to(BIBLIOTECA.resolve())
        except ValueError:
            return jsonify({"error": "Carpeta destino no válida"}), 400
        dest_dir = candidate
    dest_dir.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No se ha recibido ningún fichero"}), 400

    uploaded, rechazados = [], []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            rechazados.append(f.filename or "(sin nombre)")
            continue
        name = secure_filename(f.filename)
        if not name:
            rechazados.append(f.filename)
            continue
        dest_file = dest_dir / name
        # Evitar sobreescribir un libro ya existente con el mismo nombre
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            n = 2
            while dest_file.exists():
                dest_file = dest_dir / f"{stem} ({n}){suffix}"
                n += 1
        f.save(dest_file)
        uploaded.append(str(dest_file.relative_to(BIBLIOTECA)))

    if not uploaded:
        return jsonify({"error": "Ningún fichero válido (solo se admiten .pdf)", "rechazados": rechazados}), 400
    return jsonify({"ok": True, "uploaded": uploaded, "rechazados": rechazados})


@app.route("/generar-rag", methods=["POST"])
def generar_rag():
    """Proxy hacia Pantallasistemas (proyecto hermano, opcional): le pide que
    reindexe esta biblioteca vía su propio /api/rag/reindex. No hay import ni
    llamada directa a su código — solo HTTP, así que si no está instalado o
    no está corriendo, esto falla con un mensaje claro sin afectar al resto
    de la app."""
    try:
        req = urllib.request.Request(f"{PANTALLASISTEMAS_URL}/api/rag/reindex", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return jsonify(json.loads(resp.read())), resp.status
    except urllib.error.HTTPError as e:
        try:
            return jsonify(json.loads(e.read())), e.code
        except Exception:
            return jsonify({"ok": False, "error": f"Pantallasistemas respondió {e.code}"}), e.code
    except urllib.error.URLError as e:
        return jsonify({"ok": False, "error": f"No se pudo contactar con Pantallasistemas en {PANTALLASISTEMAS_URL}: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/generar-rag/status")
def generar_rag_status():
    try:
        with urllib.request.urlopen(f"{PANTALLASISTEMAS_URL}/api/rag/status", timeout=5) as resp:
            return jsonify(json.loads(resp.read()))
    except Exception as e:
        return jsonify({"proxy_error": str(e)}), 502


@app.route("/search")
def search_books():
    q = request.args.get("q", "").strip().lower()
    books = []
    if q and len(q) >= 2:
        for f in sorted(BIBLIOTECA.rglob("*")):
            if f.suffix.lower() == ".pdf" and q in f.stem.lower():
                rel = f.relative_to(BIBLIOTECA)
                r = str(rel)
                books.append({"name": f.stem, "rel": r, "folder": f.parent.name, "thumb": get_thumb_url(r), "icon": folder_icon(f.parent.name)})
    return render_template_string(HTML_LIBRARY,
        folders=[], books=books, current_path=None, breadcrumb=[], query=q, pinned_covers=_load_covers(), in_aventuras=True, version=VERSION)

@app.route("/viewer/<path:rel_path>")
def viewer(rel_path):
    target = safe_path(rel_path)
    if not target.exists() or not target.is_file():
        abort(404)
    parent_rel = str(Path(rel_path).parent)
    back_url = f"/browse/{parent_rel}" if parent_rel != "." else "/"
    return render_template_string(HTML_VIEWER,
        title=target.stem,
        rel=rel_path,
        rel_encoded=quote(rel_path),
        back_url=back_url)

@app.route("/pdf/<path:rel_path>")
def serve_pdf(rel_path):
    target = safe_path(rel_path)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target, mimetype="application/pdf")

@app.route("/download/<path:rel_path>")
def download_pdf(rel_path):
    target = safe_path(rel_path)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target, mimetype="application/pdf",
                     as_attachment=True, download_name=target.name)

def clean_pdf_text(raw: str) -> str:
    """Une líneas rotas del mismo párrafo y normaliza el espaciado."""
    lines = raw.splitlines()
    paragraphs, buf = [], []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
        else:
            # Si la línea anterior termina con guión de corte, pegar sin espacio
            if buf and buf[-1].endswith("-"):
                buf[-1] = buf[-1][:-1] + stripped
            else:
                buf.append(stripped)
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs)

@app.route("/translate/status")
def translate_status():
    return jsonify({"ollama": ollama_available(), "claude": claude_available(), "argos": True})

@app.route("/translate/<path:rel_path>")
def translate_page(rel_path):
    target = safe_path(rel_path)
    if not target.exists() or not target.is_file():
        abort(404)
    page_num = int(request.args.get("page", 1)) - 1
    engine   = request.args.get("engine", "ollama")

    # Extraer texto del PDF
    try:
        doc = fitz.open(target)
        if page_num >= len(doc):
            abort(400)
        raw = doc[page_num].get_text().strip()
        doc.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not raw:
        return jsonify({"empty": True})
    text = clean_pdf_text(raw)

    if engine == "claude" and claude_available():
        # Streaming via Server-Sent Events
        def generate():
            try:
                for token in claude_stream(TRANSLATE_PROMPT + text):
                    yield f"data: {json.dumps({'token': token})}\n\n"
                yield f"data: {json.dumps({'done': True, 'engine': 'claude'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return Response(stream_with_context(generate()),
                        mimetype="text/event-stream",
                        headers={"X-Accel-Buffering": "no"})
    elif engine == "ollama" and ollama_available():
        # Streaming via Server-Sent Events
        def generate():
            payload = json.dumps({
                "model": OLLAMA_MODEL,
                "prompt": TRANSLATE_PROMPT + text,
                "stream": True
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    for line in resp:
                        chunk = json.loads(line.decode())
                        token = chunk.get("response", "")
                        if token:
                            yield f"data: {json.dumps({'token': token})}\n\n"
                        if chunk.get("done"):
                            stats = {
                                "prompt_tokens": chunk.get("prompt_eval_count", 0),
                                "gen_tokens":    chunk.get("eval_count", 0),
                                "eval_duration": chunk.get("eval_duration", 0),
                            }
                            yield f"data: {json.dumps({'done': True, 'engine': 'ollama', 'stats': stats})}\n\n"
                            return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return Response(stream_with_context(generate()),
                        mimetype="text/event-stream",
                        headers={"X-Accel-Buffering": "no"})
    else:
        # Argos (síncrono, respaldo)
        try:
            translated = argostranslate.translate.translate(text, "en", "es")
            warning = f"{engine.capitalize()} no disponible, usando Argos." if engine != "argos" else None
            return jsonify({"translated": translated, "engine": "argos",
                            "empty": False, "warning": warning})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Cola de traducción — proceso independiente por trabajo, cola persistente
# ---------------------------------------------------------------------------

_QUEUE_FILE   = JOBS_DIR / "queue.json"
_RUNNING_FILE = JOBS_DIR / "running.json"

_tq_lock      = threading.Lock()
_tq_queue    : list[str] = []   # rel_paths pendientes
_tq_running  : str | None = None
_tq_paused   : str | None = None  # rel_path de traducción pausada manualmente
_survivor_pid: int | None = None  # PID de worker que sobrevivió al reinicio de Flask

def _job_file(rel_path: str) -> Path:
    return JOBS_DIR / (md5(rel_path.encode()).hexdigest() + ".json")

def _read_job(rel_path: str) -> dict | None:
    f = _job_file(rel_path)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:
        return None

def _persist_queue():
    _QUEUE_FILE.write_text(json.dumps(_tq_queue, ensure_ascii=False), encoding="utf-8")

def _start_worker(rel_path: str):
    """Prepara el job file y lanza el worker como proceso independiente."""
    target     = BIBLIOTECA / rel_path
    out        = OBSIDIAN_TRADUCCIONES / f"{target.stem}.md"
    start_page = 0
    if out.exists():
        done = set(int(m) for m in _re.findall(r'^## Página (\d+)', out.read_text(encoding="utf-8"), _re.MULTILINE))
        start_page = max(done) if done else 0
    doc   = fitz.open(target); total = len(doc); doc.close()
    jf    = _job_file(rel_path)
    jf.write_text(json.dumps({"page": start_page, "total": total, "done": False,
                               "error": None, "rel_path": rel_path}), encoding="utf-8")
    proc = subprocess.Popen(
        ["/usr/bin/python3", str(WORKER), rel_path, str(jf)],
        stdout=open(JOBS_DIR / "worker.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # Guardar PID para que un reinicio de Flask detecte si el proceso sigue vivo
    _RUNNING_FILE.write_text(json.dumps({"rel_path": rel_path, "pid": proc.pid}), encoding="utf-8")
    return proc

def _queue_runner():
    """Hilo daemon: procesa la cola uno a uno."""
    global _tq_running, _survivor_pid
    import time as _time, os as _os

    # Si hay un worker que sobrevivió al reinicio de Flask, esperarlo sin lanzar otro
    if _survivor_pid is not None:
        rp, pid = _tq_running, _survivor_pid
        while True:
            j = _read_job(rp)
            if not j or j.get("done"): break
            try: _os.kill(pid, 0)
            except OSError: break   # proceso ya terminó
            _time.sleep(3)
        with _tq_lock:
            _tq_running = None
        _RUNNING_FILE.unlink(missing_ok=True)
        _survivor_pid = None

    while True:
        with _tq_lock:
            if _tq_queue:
                rel_path    = _tq_queue.pop(0)
                _tq_running = rel_path
                _persist_queue()
            else:
                _tq_running = None
                rel_path    = None
        if rel_path is None:
            _RUNNING_FILE.unlink(missing_ok=True)
            _time.sleep(2)
            continue
        try:
            proc = _start_worker(rel_path)
            proc.wait()                 # bloqueante hasta que el worker termine
        except Exception:
            pass
        with _tq_lock:
            _tq_running = None
        _RUNNING_FILE.unlink(missing_ok=True)

# ── Inicializar cola al arrancar ──────────────────────────────────────────────
def _init_queue():
    global _tq_queue, _tq_running, _survivor_pid
    import os as _os
    saved: list[str] = []
    if _QUEUE_FILE.exists():
        try: saved = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    if _RUNNING_FILE.exists():
        try:
            info = json.loads(_RUNNING_FILE.read_text(encoding="utf-8"))
            rp   = info.get("rel_path")
            pid  = info.get("pid")
            if rp:
                job = _read_job(rp)
                if job and not job.get("done"):
                    alive = False
                    if pid:
                        try: _os.kill(pid, 0); alive = True
                        except OSError: pass
                    if alive:
                        # El worker sigue corriendo: monitorizarlo sin crear otro
                        _tq_running   = rp
                        _survivor_pid = pid
                    elif rp not in saved:
                        # El worker murió: reencolar para reanudar
                        saved.insert(0, rp)
        except Exception: pass
        if not _survivor_pid:
            _RUNNING_FILE.unlink(missing_ok=True)
    _tq_queue = saved
    _persist_queue()

_init_queue()
threading.Thread(target=_queue_runner, daemon=True).start()

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/translate-bg/<path:rel_path>", methods=["POST"])
def translate_bg_start(rel_path):
    if OBSIDIAN_TRADUCCIONES is None:
        return jsonify({"error": "No hay vault de Obsidian configurado (OBSIDIAN_BIBLIOTECA en .env) — "
                                  "la traducción completa en segundo plano lo necesita para guardar el resultado."}), 400
    target = safe_path(rel_path)
    if not target.exists(): abort(404)
    reset = request.args.get("reset") == "true"
    with _tq_lock:
        if rel_path == _tq_running:
            return jsonify({"error": "Ya está traduciéndose"}), 409
        if rel_path in _tq_queue:
            pos = _tq_queue.index(rel_path) + 1
            return jsonify({"error": f"Ya está en cola (posición {pos})"}), 409
        job = _read_job(rel_path)
        if job and not job.get("done"):
            return jsonify({"error": "Ya en proceso"}), 409
        if reset:
            # Borrar job file y markdown para empezar de cero
            _job_file(rel_path).unlink(missing_ok=True)
            md = OBSIDIAN_TRADUCCIONES / f"{target.stem}.md"
            md.unlink(missing_ok=True)
        _tq_queue.append(rel_path)
        position = len(_tq_queue)
        _persist_queue()
    doc = fitz.open(target); total = len(doc); doc.close()
    return jsonify({"ok": True, "total": total, "position": position})

@app.route("/translate-bg/<path:rel_path>", methods=["DELETE"])
def translate_bg_remove(rel_path):
    with _tq_lock:
        if rel_path in _tq_queue:
            _tq_queue.remove(rel_path)
            _persist_queue()
            return jsonify({"ok": True})
    return jsonify({"error": "No está en cola"}), 404

@app.route("/translate-bg/<path:rel_path>/pause", methods=["POST"])
def translate_bg_pause(rel_path):
    global _tq_paused
    import os as _os
    with _tq_lock:
        if _tq_running != rel_path:
            return jsonify({"error": "No está traduciéndose"}), 409
        _tq_paused = rel_path
    # Matar el proceso worker — guarda su progreso tras cada página, así no pierde nada
    if _RUNNING_FILE.exists():
        try:
            info = json.loads(_RUNNING_FILE.read_text(encoding="utf-8"))
            pid  = info.get("pid")
            if pid:
                _os.kill(pid, 15)  # SIGTERM
        except Exception:
            pass
    return jsonify({"ok": True})

@app.route("/translate-bg/<path:rel_path>/resume", methods=["POST"])
def translate_bg_resume(rel_path):
    global _tq_paused
    with _tq_lock:
        if _tq_paused != rel_path:
            return jsonify({"error": "No está pausada"}), 409
        _tq_paused = None
        _tq_queue.insert(0, rel_path)
        _persist_queue()
    return jsonify({"ok": True})

@app.route("/translate-status/<path:rel_path>")
def translate_bg_status(rel_path):
    with _tq_lock:
        running  = _tq_running
        queue_cp = list(_tq_queue)
        paused   = _tq_paused
    job = _read_job(rel_path)
    if rel_path == running:
        status = "running"
    elif rel_path in queue_cp:
        status = "queued"
    elif rel_path == paused:
        status = "paused"
    elif job and job.get("done"):
        status = "done"
    else:
        status = "idle"
    pos = queue_cp.index(rel_path) + 1 if rel_path in queue_cp else None
    return jsonify({
        "status":    status,
        "queue_pos": pos,
        "page":      job.get("page", 0)  if job else 0,
        "total":     job.get("total", 0) if job else 0,
        "error":     job.get("error")    if job else None,
    })

@app.route("/translate-queue")
def translate_queue_all():
    """Estado completo de la cola para la página /jobs."""
    with _tq_lock:
        running  = _tq_running
        queue_cp = list(_tq_queue)
        paused   = _tq_paused
    # Jobs completados/con error
    completed = []
    for f in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.name in ("queue.json",): continue
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            rp = j.get("rel_path", "")
            if rp and rp != running and rp not in queue_cp:
                completed.append(j)
        except Exception:
            pass
    running_job = _read_job(running) if running else None
    if running_job: running_job["rel_path"] = running
    return jsonify({
        "running":   running_job,
        "queue":     queue_cp,
        "completed": completed[:20],
        "paused":    paused,
    })

@app.route("/save-note/<path:rel_path>", methods=["POST"])
def save_note(rel_path):
    from datetime import date as _date
    if OBSIDIAN_BIBLIOTECA is None:
        return jsonify({"error": "No hay vault de Obsidian configurado (OBSIDIAN_BIBLIOTECA en .env)"}), 400
    target = safe_path(rel_path)
    if not target.exists():
        abort(404)
    data       = request.json or {}
    page       = int(data.get("page", 1))
    translation = data.get("translation", "").strip()
    notes      = data.get("notes", "").strip()
    engine     = data.get("engine", "ollama")
    if not translation and not notes:
        return jsonify({"error": "Nada que guardar"}), 400

    OBSIDIAN_TRADUCCIONES.mkdir(parents=True, exist_ok=True)
    book_name = target.stem
    note_file = OBSIDIAN_BIBLIOTECA / f"{book_name}.md"
    today     = _date.today().isoformat()
    eng_label = {"ollama": "Ollama · qwen2.5:7b", "claude": f"Claude · {CLAUDE_MODEL}"}.get(engine, "Argos Translate")

    if not note_file.exists():
        note_file.write_text(
            f'---\ntitle: "{book_name}"\nfuente: "biblioteca/{rel_path}"\n'
            f'tags:\n  - rol\n  - biblioteca-pargen\n  - traduccion\n'
            f'fecha_creacion: {today}\n---\n\n# {book_name}\n',
            encoding="utf-8"
        )

    section = f"\n## Página {page}\n\n"
    if translation:
        section += f"> *Traducción — {eng_label} — {today}*\n\n{translation}\n\n"
    if notes:
        section += f"### Mis notas\n\n{notes}\n\n"
    section += "---\n"

    with note_file.open("a", encoding="utf-8") as f:
        f.write(section)

    rel_to_vault = note_file.relative_to(OBSIDIAN_BIBLIOTECA.parent)
    return jsonify({"ok": True, "file": str(rel_to_vault)})

@app.route("/set-cover", methods=["POST"])
def set_cover():
    data   = request.json or {}
    folder = data.get("folder", "").strip()
    rel    = data.get("rel", "").strip()
    if not folder or not rel:
        return jsonify({"error": "Faltan parámetros"}), 400
    with _covers_lock:
        covers = _load_covers()
        covers[folder] = rel
        _save_covers(covers)
    return jsonify({"ok": True})

@app.route("/jobs")
def jobs_page():
    return render_template_string(HTML_JOBS)

HTML_JOBS = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0a0603">
<title>Traducciones — Pargen</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0603; --surface: #120d08; --card: #1a1208;
    --gold: #c9a84c; --gold-brd: rgba(201,168,76,.25);
    --cream: #f5ead8; --muted: rgba(245,234,216,.45);
    --border: rgba(201,168,76,.12); --blue: #0161ef;
    --green: #4ade80; --red: #f87171; --yellow: #fbbf24;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--cream);
    font-family: 'Inter', sans-serif; min-height: 100dvh; }
  header {
    position: sticky; top: 0; z-index: 50;
    background: rgba(10,6,3,.92); backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border); padding: 0 1.2rem;
  }
  .header-inner { max-width: 900px; margin: 0 auto; height: 58px;
    display: flex; align-items: center; gap: 0.8rem; }
  .logo-sm { display: flex; align-items: center; gap: 0.6rem; text-decoration: none; }
  .logo-sm img { height: 28px; }
  .logo-sm span { font-family: 'Playfair Display', serif; font-size: 1rem; color: var(--gold); }
  main { max-width: 900px; margin: 0 auto; padding: 2rem 1.2rem 4rem; }
  h1 { font-family: 'Playfair Display', serif; font-size: 1.6rem;
    color: var(--gold); margin-bottom: 0.4rem; }
  .subtitle { color: var(--muted); font-size: 0.83rem; margin-bottom: 2rem; }
  .section-label {
    font-family: 'Playfair Display', serif; font-size: 0.85rem; color: var(--gold);
    letter-spacing: .06em; text-transform: uppercase; margin: 1.8rem 0 0.7rem;
    display: flex; align-items: center; gap: 0.6rem;
  }
  .section-label::after { content: ''; flex: 1; height: 1px; background: var(--border); }
  .job-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 1rem 1.2rem;
    margin-bottom: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem;
  }
  .job-top { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; }
  .job-title { font-family: 'Playfair Display', serif; font-size: 0.97rem;
    color: var(--cream); flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge {
    font-size: 0.68rem; font-weight: 700; padding: 0.18rem 0.55rem;
    border-radius: 20px; white-space: nowrap; flex-shrink: 0;
  }
  .badge-running { background: rgba(1,97,239,.25);   color: #60a5fa; border: 1px solid rgba(1,97,239,.4); }
  .badge-queued  { background: rgba(251,191,36,.15); color: var(--yellow); border: 1px solid rgba(251,191,36,.3); }
  .badge-done    { background: rgba(74,222,128,.15); color: var(--green);  border: 1px solid rgba(74,222,128,.3); }
  .badge-error   { background: rgba(248,113,113,.15);color: var(--red);   border: 1px solid rgba(248,113,113,.3); }
  .job-path { color: var(--muted); font-size: 0.72rem; }
  .prog-wrap { display: flex; align-items: center; gap: 0.8rem; }
  .prog-bar { flex: 1; height: 4px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .prog-fill { height: 100%; border-radius: 3px; transition: width .4s; }
  .prog-fill-run  { background: var(--blue); }
  .prog-fill-done { background: var(--green); }
  .prog-fill-err  { background: var(--red); }
  .prog-label { font-size: 0.76rem; color: var(--muted); white-space: nowrap; }
  .btn-rm {
    background: rgba(248,113,113,.1); border: 1px solid rgba(248,113,113,.3);
    color: var(--red); font-size: 0.72rem; padding: 0.2rem 0.55rem;
    border-radius: 6px; cursor: pointer; font-family: inherit;
    flex-shrink: 0; transition: background .15s;
  }
  .btn-rm:hover { background: rgba(248,113,113,.22); }
  .btn-repeat {
    background: rgba(201,168,76,.1); border: 1px solid rgba(201,168,76,.3);
    color: var(--gold); font-size: 0.72rem; padding: 0.2rem 0.55rem;
    border-radius: 6px; cursor: pointer; font-family: inherit;
    flex-shrink: 0; transition: background .15s;
  }
  .btn-repeat:hover { background: rgba(201,168,76,.2); }
  .empty { text-align: center; padding: 4rem 2rem; color: var(--muted); }
  .empty-icon { font-size: 2.5rem; margin-bottom: 0.8rem; }
  #status-note { font-size: 0.74rem; color: var(--muted); margin-top: 1.2rem; }
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <a class="logo-sm" href="/">
      <img src="/static/logo.webp" alt="Pargen" onerror="this.style.display='none'">
      <span>Pargen</span>
    </a>
    <span style="color:var(--gold);font-size:0.85rem;margin-left:0.3rem">› Traducciones</span>
    <a href="/" style="margin-left:auto;text-decoration:none;background:var(--card);border:1px solid var(--gold-brd);color:var(--cream);padding:0.35rem 0.8rem;border-radius:8px;font-size:0.82rem">← Volver</a>
  </div>
</header>
<main>
  <h1>Traducciones en segundo plano</h1>
  <p class="subtitle">Ficheros generados en <em>Biblioteca Pargen / Traducciones /</em> en Obsidian</p>
  <div id="content"><p style="color:var(--muted);font-size:0.85rem">Cargando…</p></div>
  <p id="status-note"></p>
</main>
<script>
let pollTimer = null;

function bookName(rel_path) {
  return (rel_path || '').split('/').pop().replace(/\\.pdf$/i, '');
}

function jobCard(j, badgeHtml, progClass, pct, labelHtml, showDelete, showRepeat, resetOnRepeat) {
  const rel = j.rel_path || j;
  const del = showDelete
    ? `<button class="btn-rm" data-rel="${rel}" onclick="removeJob(this)">✕ Quitar</button>`
    : '';
  const rep = showRepeat
    ? `<button class="btn-repeat" data-rel="${rel}" data-reset="${resetOnRepeat ? 'true' : ''}" onclick="repeatJob(this)">🔄 Repetir</button>`
    : '';
  return `
<div class="job-card">
  <div class="job-top">
    <div class="job-title">${bookName(rel)}</div>
    ${badgeHtml} ${rep} ${del}
  </div>
  <div class="job-path">${rel}</div>
  ${pct !== null ? `
  <div class="prog-wrap">
    <div class="prog-bar"><div class="prog-fill ${progClass}" style="width:${pct}%"></div></div>
    <span class="prog-label">${labelHtml}</span>
  </div>` : ''}
</div>`;
}

async function removeJob(btn) {
  const rel = btn.dataset.rel;
  btn.disabled = true;
  try {
    await fetch('/translate-bg/' + encodeURIComponent(rel).replace(/%2F/g, '/'), { method: 'DELETE' });
    await refresh();
  } catch(e) { btn.disabled = false; }
}

async function repeatJob(btn) {
  const rel   = btn.dataset.rel;
  const reset = btn.dataset.reset === 'true';
  const msg   = reset
    ? '¿Repetir traducción desde cero? Se sobreescribirá el markdown en Obsidian.'
    : '¿Reintentar traducción? Reanudará desde la última página guardada.';
  if (!confirm(msg)) return;
  btn.disabled = true;
  try {
    const url = '/translate-bg/' + encodeURIComponent(rel).replace(/%2F/g, '/') + (reset ? '?reset=true' : '');
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();
    if (!d.ok) { alert('Error: ' + (d.error || 'desconocido')); btn.disabled = false; return; }
    await refresh();
  } catch(e) { btn.disabled = false; }
}

async function refresh() {
  let data;
  try {
    const r = await fetch('/translate-queue');
    data = await r.json();
  } catch(e) { return; }

  let html = '';
  let hasActive = false;

  // ── En curso ──────────────────────────────────────────────
  if (data.running) {
    hasActive = true;
    const j   = data.running;
    const pct = j.total ? Math.round(j.page / j.total * 100) : 0;
    html += '<p class="section-label">En curso</p>';
    html += jobCard(j,
      '<span class="badge badge-running">⏳ Traduciendo</span>',
      'prog-fill-run', pct,
      `${j.page} / ${j.total} páginas (${pct}%)`,
      false, false, false);
  }

  // ── En cola ────────────────────────────────────────────────
  if (data.queue && data.queue.length) {
    hasActive = true;
    html += '<p class="section-label">En cola</p>';
    data.queue.forEach((rel, i) => {
      html += jobCard({rel_path: rel},
        `<span class="badge badge-queued">⌛ Posición ${i + 1}</span>`,
        null, null, null,
        true, false, false);
    });
  }

  // ── Completadas / con error ────────────────────────────────
  if (data.completed && data.completed.length) {
    html += '<p class="section-label">Completadas</p>';
    data.completed.forEach(j => {
      const pct   = j.total ? Math.round(j.page / j.total * 100) : 0;
      const isErr = j.done && j.error;
      html += jobCard(j,
        isErr ? '<span class="badge badge-error">✗ Error</span>'
              : '<span class="badge badge-done">✓ Completada</span>',
        isErr ? 'prog-fill-err' : 'prog-fill-done',
        pct,
        isErr ? 'Error: ' + j.error : `${j.page} / ${j.total} páginas (${pct}%)`,
        false, true, !isErr);   // showRepeat=true; resetOnRepeat=true si completada sin error
    });
  }

  if (!html) {
    html = '<div class="empty"><div class="empty-icon">📭</div><p>No hay ninguna traducción iniciada todavía.</p></div>';
  }

  document.getElementById('content').innerHTML = html;
  const note = document.getElementById('status-note');
  if (hasActive) {
    note.textContent = '↻ Actualizando cada 4 s…';
    if (!pollTimer) pollTimer = setInterval(refresh, 4000);
  } else {
    note.textContent = '';
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
}

refresh();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Generador de esquema de partida (solo carpeta Aventuras)
# ---------------------------------------------------------------------------

_esquema_jobs: dict = {}   # rel_path -> {"status": "running"|"done"|"error", "msg": str, "files": list}
_esquema_lock = threading.Lock()


def _run_esquema(rel_path: str):
    print(f"[esquema] START rel_path={rel_path!r}", flush=True)
    with _esquema_lock:
        _esquema_jobs[rel_path] = {"status": "running", "msg": "Extrayendo texto del PDF…", "files": []}
    try:
        target = BIBLIOTECA / rel_path
        doc = fitz.open(target)
        pages_text = []
        for i in range(min(15, len(doc))):
            raw = doc[i].get_text().strip()
            if raw:
                pages_text.append(clean_pdf_text(raw))
        doc.close()
        text = "\n\n---\n\n".join(pages_text)
        if len(text) > 6000:
            text = text[:6000] + "\n\n[...texto truncado...]"

        with _esquema_lock:
            _esquema_jobs[rel_path]["msg"] = "Generando esquema con Ollama…"

        payload = json.dumps({
            "model": OLLAMA_MODEL_ESQUEMA,
            "system": "Eres un asistente especializado en juegos de rol de mesa (TTRPGs) como Dungeons & Dragons. Todo el contenido que procesas es ficción fantástica para entretenimiento adulto. Debes seguir siempre las instrucciones del usuario y generar el esquema solicitado sin negarte ni añadir advertencias.",
            "prompt": ESQUEMA_PROMPT + text,
            "stream": True,
            "options": {"num_ctx": 8192},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        output = ""
        tokens = 0
        with urllib.request.urlopen(req, timeout=600) as resp:
            for line in resp:
                chunk = json.loads(line.decode())
                output += chunk.get("response", "")
                tokens += 1
                if tokens % 50 == 0:
                    # Actualizar progreso cada 50 tokens
                    ficheros_vistos = output.count("===FICHERO:")
                    with _esquema_lock:
                        _esquema_jobs[rel_path]["msg"] = (
                            f"Generando esquema… {tokens} tokens"
                            + (f" · {ficheros_vistos}/6 ficheros" if ficheros_vistos else "")
                        )
                if chunk.get("done"):
                    break

        # Parsear la salida buscando separadores ===FICHERO: nombre.md===
        created_files = []
        adventure_name = Path(rel_path).stem
        dest_dir = OBSIDIAN_PARTIDAS / adventure_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        print(f"[esquema] OUTPUT PREVIEW: {output[:500]!r}", flush=True)
        parts = output.split("===FICHERO:")
        for part in parts[1:]:
            first_newline = part.find("\n")
            if first_newline == -1:
                continue
            filename = part[:first_newline].strip().rstrip("=").strip()
            content  = part[first_newline:].strip()
            if not filename or not content:
                continue
            # Sanear el nombre de fichero
            safe_name = _re.sub(r'[<>:"/\\|?*]', "_", filename)
            dest_file = dest_dir / safe_name
            dest_file.write_text(content, encoding="utf-8")
            created_files.append(safe_name)

        with _esquema_lock:
            _esquema_jobs[rel_path] = {
                "status": "done",
                "msg": f"{len(created_files)} fichero(s) creados en Obsidian › Partidas › {adventure_name}",
                "files": created_files,
            }
    except Exception as e:
        print(f"[esquema] ERROR rel_path={rel_path!r} error={e!r}", flush=True)
        with _esquema_lock:
            _esquema_jobs[rel_path] = {"status": "error", "msg": str(e), "files": []}


@app.route("/generar-esquema/<path:rel_path>", methods=["POST"])
def generar_esquema(rel_path):
    if OBSIDIAN_PARTIDAS is None:
        return jsonify({"error": "No hay vault de Obsidian configurado (OBSIDIAN_PARTIDAS en .env) — "
                                  "el esquema de partida se guarda ahí."}), 400
    target = safe_path(rel_path)
    if not target.exists() or not target.is_file():
        abort(404)
    with _esquema_lock:
        job = _esquema_jobs.get(rel_path)
        if job and job["status"] == "running":
            return jsonify({"error": "Ya está generando el esquema"}), 409
    t = threading.Thread(target=_run_esquema, args=(rel_path,), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/esquema-status/<path:rel_path>")
def esquema_status(rel_path):
    with _esquema_lock:
        job = _esquema_jobs.get(rel_path)
    if not job:
        return jsonify({"status": "idle"})
    return jsonify(job)


@app.route("/debug-esquema-jobs")
def debug_esquema_jobs():
    with _esquema_lock:
        return jsonify({k: v for k, v in _esquema_jobs.items()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    app.run(host="0.0.0.0", port=port, debug=False)
