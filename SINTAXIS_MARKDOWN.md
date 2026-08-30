# Sintaxis Markdown que genera rol-biblioteca

Esta app no requiere Obsidian para funcionar (biblioteca, visor de PDF, traducción al vuelo y subida de documentos van igual sin ningún vault configurado). Si configuras `OBSIDIAN_BIBLIOTECA` y/o `OBSIDIAN_PARTIDAS` en tu `.env` (ver `.env.example`), tres funciones adicionales escriben notas en Markdown ahí. Esto es lo que generan, para que sepas qué esperar en tu propio vault (de Obsidian o de cualquier otro editor que lea Markdown + frontmatter YAML).

## 1. Guardar nota desde el visor (`POST /save-note`)

Fichero: `<OBSIDIAN_BIBLIOTECA>/<nombre del libro>.md`

Al guardar la primera anotación de un libro, crea el fichero con esta cabecera:

```markdown
---
title: "Nombre del libro"
fuente: "biblioteca/Sistema/Nombre del libro.pdf"
tags:
  - rol
  - biblioteca-pargen
  - traduccion
fecha_creacion: 2026-08-28
---

# Nombre del libro
```

Cada vez que guardas algo (traducción de una página y/o tus propias notas), añade una sección al final del fichero:

```markdown
## Página 12

> *Traducción — Ollama · qwen2.5:7b — 2026-08-28*

[texto traducido]

### Mis notas

[tus notas]

---
```

## 2. Traducción completa en segundo plano (`POST /translate-bg/<pdf>`)

Fichero: `<OBSIDIAN_BIBLIOTECA>/Traducciones/<nombre del libro>.md`

Traduce el PDF entero, página a página, en un proceso independiente (`translate_worker.py`) que sobrevive a reinicios de la app. Cabecera al empezar:

```markdown
---
title: "Nombre del libro — Traducción"
fuente: "biblioteca/Sistema/Nombre del libro.pdf"
tags:
  - rol
  - biblioteca-pargen
  - traduccion-completa
fecha_generacion: 2026-08-28
---

# Nombre del libro

> *Traducción automática completa — Ollama · qwen2.5:7b-instruct-q4_K_M — 2026-08-28*
```

Y va añadiendo, página por página, exactamente igual que el punto 1 (`## Página N` + texto), sin la parte de "Mis notas".

## 3. Esquema de preparación de partida (`POST /generar-esquema/<pdf>`)

Carpeta: `<OBSIDIAN_PARTIDAS>/<nombre de la aventura>/`

A partir del texto completo del PDF, genera **6 ficheros Markdown independientes** en esa carpeta (sin frontmatter, texto libre estructurado por el propio modelo):

- `00_Aventura.md` — título, sistema, nivel recomendado, premisa y ganchos.
- `01_PNJs.md` — PNJs importantes: nombre en negrita, rol, descripción, motivación, secreto.
- `02_Lugares.md` — localizaciones: nombre, ambiente, puntos de interés, secretos.
- `03_Encuentros.md` — combates, trampas, desafíos sociales, notas de dirección.
- `04_Loot.md` — tesoros, objetos mágicos, recompensas.
- `Prep_DM.md` — hoja de prep rápida estilo Lazy DM (apertura in media res, escenas imprescindibles, tabla de secretos en d6, PNJs resumidos, reloj de amenaza).

## Notas generales

- Todo el texto se escribe en UTF-8, en modo "añadir" (`open(..., "a")`) para las notas por página — nunca se sobreescribe una nota ya existente, solo se le añaden secciones nuevas al final.
- Los nombres de fichero se sanean quitando `< > : " / \ | ? *` — seguros para Windows, Mac y Linux por igual.
- Ninguna de estas tres funciones es necesaria para usar la app — si no configuras `OBSIDIAN_BIBLIOTECA`/`OBSIDIAN_PARTIDAS`, simplemente responden con un error claro en vez de intentar escribir en un sitio que no existe en tu máquina.
