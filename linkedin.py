import json
import os
import re
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import requests

WEBHOOK_URL_ENV = "LINKEDIN_WEBHOOK_URL"
DEFAULT_OCR_LANGUAGE = "eng"
MAX_PDF_TEXT_CHARS = 100000
DEFAULT_CONFIG = {
    "default_pages": "1-40",
    "auto_full_pdf_max_pages": 40,
    "save_drafts": True,
    "drafts_dir": "posts/drafts",
    "max_generation_attempts": 2,
    "target_post_chars": 1100,
    "max_post_chars": 1800,
    "max_hashtags": 5,
    "blocked_phrases": [
        "en estas paginas",
        "estas paginas",
        "en las paginas",
        "en las primeras paginas",
        "las primeras paginas",
        "al principio del libro",
        "en el principio del libro",
        "en el comienzo del libro",
        "en este capitulo",
        "en el capitulo",
        "el autor dice",
        "los autores dicen",
        "el libro dice",
    ],
    "default_notes": (
        "Escribir para security engineers, IAM people y backend engineers. "
        "Devolver posts listos para publicar y pensados para leer en celular. "
        "Cada opcion deberia apuntar a 900-1400 caracteres, sin obsesionarse "
        "si necesita un poco mas para mantener claridad. Usar parrafos cortos "
        "de 1 o 2 lineas, una sola idea central y hasta 5 hashtags. "
        "No usar referencias mecanicas al material leido como 'en estas paginas', "
        "'en el principio del libro', 'en las primeras paginas', 'el autor dice' "
        "o frases parecidas. Entrar directo en la idea. "
        "No hacer un resumen generico: elegir una idea fuerte de lo leido, "
        "explicar por que importa y conectarla con problemas reales de seguridad."
    ),
}


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error leyendo config {path}: {e}", file=sys.stderr)
        sys.exit(1)

    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    return config


def load_env_file(env_path: str = ".env"):
    path = Path(env_path)
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_webhook_url() -> str:
    webhook_url = os.environ.get(WEBHOOK_URL_ENV, "").strip()
    if webhook_url:
        return webhook_url

    print(
        f"Error: configurá {WEBHOOK_URL_ENV} en el entorno o en .env.",
        file=sys.stderr,
    )
    sys.exit(1)


def looks_like_pages(value: str) -> bool:
    return bool(re.fullmatch(r"\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*", value))


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "draft"


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def find_blocked_phrases(output: str, blocked_phrases: list[str]) -> list[str]:
    normalized_output = normalize_text(output)
    matches = []

    for phrase in blocked_phrases:
        normalized_phrase = normalize_text(phrase)
        if normalized_phrase and normalized_phrase in normalized_output:
            matches.append(phrase)

    return sorted(set(matches))


def split_posts(output: str) -> list[str]:
    return [part.strip() for part in output.split("---") if part.strip()]


def find_output_issues(output: str, config: dict) -> list[str]:
    issues = []
    blocked_matches = find_blocked_phrases(output, config.get("blocked_phrases", []))
    if blocked_matches:
        issues.append("frases no deseadas: " + ", ".join(blocked_matches))

    max_post_chars = int(config.get("max_post_chars", 0) or 0)
    max_hashtags = int(config.get("max_hashtags", 0) or 0)

    for index, post in enumerate(split_posts(output), 1):
        if max_post_chars and len(post) > max_post_chars:
            issues.append(
                f"opcion {index} demasiado larga: {len(post)} caracteres "
                f"(maximo {max_post_chars})"
            )

        if max_hashtags:
            hashtags = re.findall(r"(?<!\w)#[^\s#]+", post)
            if len(hashtags) > max_hashtags:
                issues.append(
                    f"opcion {index} tiene {len(hashtags)} hashtags "
                    f"(maximo {max_hashtags})"
                )

    return issues


def parse_pages(pages_str: str) -> list[int]:
    pages = []
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return pages


def get_pdf_page_count(pdf_path: str) -> int:
    from pypdf import PdfReader
    return len(PdfReader(pdf_path).pages)


def extract_pdf_text(pdf_path: str, pages_str: str = None) -> str:
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    page_numbers = parse_pages(pages_str) if pages_str else list(range(1, total + 1))
    texts = []
    for n in page_numbers:
        if n < 1 or n > total:
            continue
        texts.append(reader.pages[n - 1].extract_text() or "")
    return "\n\n".join(texts)


@contextmanager
def suppress_stderr():
    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        yield
        return

    saved_stderr_fd = os.dup(stderr_fd)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), stderr_fd)
            yield
    finally:
        os.dup2(saved_stderr_fd, stderr_fd)
        os.close(saved_stderr_fd)


def find_tessdata(language: str = DEFAULT_OCR_LANGUAGE) -> str:
    primary_language = language.split("+", 1)[0]
    candidates = []

    env_tessdata = os.environ.get("TESSDATA_PREFIX")
    if env_tessdata:
        candidates.extend([env_tessdata, str(Path(env_tessdata) / "tessdata")])

    candidates.extend([
        "/opt/homebrew/share/tessdata",
        "/opt/homebrew/opt/tesseract/share/tessdata",
        "/usr/local/share/tessdata",
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    ])

    for candidate in candidates:
        if candidate and (Path(candidate) / f"{primary_language}.traineddata").exists():
            return candidate

    raise RuntimeError(
        "no se encontró tessdata para OCR. Instalá tesseract o configurá TESSDATA_PREFIX."
    )


def extract_pdf_ocr_text(
    pdf_path: str,
    pages_str: str = None,
    language: str = DEFAULT_OCR_LANGUAGE,
) -> str:
    import fitz

    tessdata = find_tessdata(language)
    texts = []

    with fitz.open(pdf_path) as doc:
        total = len(doc)
        page_numbers = parse_pages(pages_str) if pages_str else list(range(1, total + 1))
        for n in page_numbers:
            if n < 1 or n > total:
                continue
            page = doc[n - 1]
            with suppress_stderr():
                textpage = page.get_textpage_ocr(
                    dpi=200,
                    full=True,
                    language=language,
                    tessdata=tessdata,
                )
            texts.append(page.get_text(textpage=textpage) or "")

    return "\n\n".join(texts)


def resolve_input(args, parser):
    pages = args.pages or args.page_range

    if args.topic:
        if args.pdf or args.book or args.source:
            parser.error("usá --topic solo, sin PDF, libro ni source posicional.")
        return {
            "pdf_path": None,
            "book_name": args.topic,
            "pages": pages or "recomendacion semanal",
            "topic_context": args.context or "",
        }

    if args.pdf and args.book:
        parser.error("usá --pdf o --book, no ambos.")

    if args.pdf:
        if args.source:
            if not pages and looks_like_pages(args.source):
                pages = args.source
            else:
                parser.error("si usás --pdf, el argumento posicional solo puede ser el rango de páginas.")
        return {"pdf_path": args.pdf, "book_name": None, "pages": pages, "topic_context": ""}

    if args.book:
        if args.source:
            if not pages and looks_like_pages(args.source):
                pages = args.source
            else:
                parser.error("si usás --book, el argumento posicional solo puede ser el rango de páginas.")
        return {"pdf_path": None, "book_name": args.book, "pages": pages, "topic_context": ""}

    if not args.source:
        parser.error("pasá un PDF, un nombre de libro, --pdf o --book.")

    source_path = Path(args.source).expanduser()
    if source_path.suffix.lower() == ".pdf":
        return {"pdf_path": str(source_path), "book_name": None, "pages": pages, "topic_context": ""}
    if source_path.exists():
        parser.error("el archivo de entrada tiene que ser un PDF.")

    return {"pdf_path": None, "book_name": args.source, "pages": pages, "topic_context": ""}


def choose_pages(requested_pages: str, total_pages: int, config: dict) -> str:
    if requested_pages:
        return requested_pages

    max_full_pages = int(config.get("auto_full_pdf_max_pages", 40))
    if total_pages <= max_full_pages:
        return None

    return config.get("default_pages") or None


def build_notes(cli_notes: str, config: dict) -> str:
    default_notes = (config.get("default_notes") or "").strip()
    cli_notes = (cli_notes or "").strip()

    if default_notes and cli_notes:
        return f"{default_notes}\n\nNotas del usuario:\n{cli_notes}"
    return cli_notes or default_notes


def build_topic_text(topic: str, context: str) -> str:
    parts = [
        f"Tema recomendado: {topic}",
        "Usar este contexto como material fuente para generar posts tecnicos.",
    ]
    if context.strip():
        parts.extend(["", context.strip()])
    return "\n".join(parts)


def save_draft(
    output: str,
    book: str,
    pages: str,
    notes: str,
    source: str,
    drafts_dir: str,
) -> Path:
    created_at = datetime.now().isoformat(timespec="seconds")
    date_prefix = datetime.now().strftime("%Y-%m-%d-%H%M")
    pages_label = pages or "completo"
    filename = f"{date_prefix}-{slugify(book)}-{slugify(pages_label)}.md"
    path = Path(drafts_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    content = "\n".join([
        "---",
        f"book: {book}",
        f"pages: {pages_label}",
        f"source: {source or ''}",
        f"created_at: {created_at}",
        "---",
        "",
        "# Drafts",
        "",
        output.strip(),
        "",
        "# Publishing checklist",
        "",
        "- Elegir una opcion y ajustar la primera linea.",
        "- Publicar con 3-5 hashtags relevantes.",
        "- Responder comentarios durante la primera hora.",
        "- Comentar en 3 posts relevantes del mismo tema.",
        "",
    ])

    if notes:
        content += "\n# Notes\n\n" + notes.strip() + "\n"

    path.write_text(content, encoding="utf-8")
    return path


def generate_post(book: str, pages: str, notes: str, pdf_text: str = "") -> str:
    payload = {"book": book, "pages": pages or "completo", "notes": notes}
    if pdf_text:
        payload["ocr"] = False
        payload["pdf_text"] = pdf_text[:MAX_PDF_TEXT_CHARS]
    try:
        response = requests.post(get_webhook_url(), json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            return data[0].get("output", "")
        return data.get("output", "")
    except requests.exceptions.Timeout:
        print("Error: el webhook tardó demasiado en responder.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error al conectar con el webhook: {e}", file=sys.stderr)
        sys.exit(1)


def generate_post_with_retries(
    book: str,
    pages: str,
    notes: str,
    pdf_text: str,
    config: dict,
) -> str:
    max_attempts = max(1, int(config.get("max_generation_attempts", 1)))
    attempt_notes = notes

    for attempt in range(1, max_attempts + 1):
        output = generate_post(book, pages, attempt_notes, pdf_text)
        issues = find_output_issues(output, config)

        if not issues:
            return output

        if attempt == max_attempts:
            print(
                "Advertencia: el modelo devolvió una respuesta fuera de formato: "
                + "; ".join(issues),
                file=sys.stderr,
            )
            return output

        print(
            "El modelo devolvió una respuesta fuera de formato "
            f"({'; '.join(issues)}). Reintentando...",
            file=sys.stderr,
        )
        attempt_notes = (
            f"{notes}\n\n"
            "Corrección obligatoria para este intento: la respuesta anterior tuvo "
            f"estos problemas: {'; '.join(issues)}. "
            "Reescribí ambas opciones desde cero. Cada opcion debe ser mobile-first, "
            f"idealmente cerca de {config.get('target_post_chars', 1100)} caracteres. "
            f"No te vayas de {config.get('max_post_chars', 1800)} caracteres salvo que "
            "sea estrictamente necesario. "
            f"Usá maximo {config.get('max_hashtags', 5)} hashtags. "
            "No menciones páginas, capítulo, principio del libro, texto leído ni autor. "
            "Entrá directo en una sola idea técnica."
        )

    return output


def print_posts(output: str):
    for i, post in enumerate(split_posts(output), 1):
        print(f"\n{'='*60}")
        print(f"  OPCIÓN {i}")
        print(f"{'='*60}\n")
        print(post)
    print(f"\n{'='*60}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generador de posts de LinkedIn sobre ciberseguridad",
        epilog=(
            "Uso recomendado: python linkedin.py libro.pdf 1-40\n"
            "Si el PDF es corto y no pasás páginas, usa todo. "
            "Si es largo, usa default_pages de config.json."
        ),
    )
    parser.add_argument("source", nargs="?", help="Ruta al PDF o nombre del libro")
    parser.add_argument("page_range", nargs="?", help="Páginas leídas (ej: 1-40 o 10,15,20-30)")
    parser.add_argument("--topic", help="Tema para generar posts sin PDF")
    parser.add_argument("--context", default="", help="Contexto fuente para --topic")
    parser.add_argument("--book", help="Nombre del libro (compatibilidad)")
    parser.add_argument("--pdf", help="Ruta al PDF (compatibilidad)")
    parser.add_argument("--pages", default=None, help="Páginas leídas (compatibilidad)")
    parser.add_argument("--notes", default="", help="Notas adicionales (opcional)")
    parser.add_argument("--ocr-lang", default=DEFAULT_OCR_LANGUAGE, help="Idioma para OCR local en PDFs escaneados")
    parser.add_argument("--config", default="config.json", help="Archivo de configuración")
    args = parser.parse_args()

    load_env_file()
    config = load_config(args.config)
    resolved = resolve_input(args, parser)
    pages = resolved["pages"]
    notes = build_notes(args.notes, config)
    pdf_text = ""
    source_label = resolved["book_name"] or resolved["pdf_path"] or ""
    book_name = resolved["book_name"]
    topic_context = resolved.get("topic_context", "")

    if topic_context or args.topic:
        pdf_text = build_topic_text(book_name, topic_context)
        source_label = "topic"

    if resolved["pdf_path"]:
        pdf_path = resolved["pdf_path"]
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            print(f"No existe el PDF: {pdf_file}", file=sys.stderr)
            sys.exit(1)

        book_name = pdf_file.stem
        total_pages = get_pdf_page_count(pdf_path)
        pages = choose_pages(pages, total_pages, config)
        if pages:
            print(f"PDF de {total_pages} páginas. Usando páginas {pages}.")
        else:
            print(f"PDF de {total_pages} páginas. Usando todo el PDF.")

        label = f"páginas {pages}" if pages else "todo el PDF"
        print(f"\nExtrayendo {label}...")

        pdf_text = extract_pdf_text(pdf_path, pages)

        if not pdf_text.strip():
            print("PDF escaneado detectado, usando OCR local...")
            try:
                pdf_text = extract_pdf_ocr_text(pdf_path, pages, args.ocr_lang)
            except Exception as e:
                print(f"No se pudo hacer OCR local: {e}", file=sys.stderr)

            if pdf_text.strip():
                print(f"OCR local completado ({len(pdf_text.strip())} caracteres).")
            else:
                print("No se pudo extraer texto del PDF ni leerlo con OCR local.", file=sys.stderr)
                sys.exit(1)

    pages_label = pages or "completo"
    print(f"Generando posts para: {book_name} ({pages_label})...")
    output = generate_post_with_retries(book_name, pages, notes, pdf_text, config)

    if not output:
        print("No se recibió respuesta del webhook.", file=sys.stderr)
        sys.exit(1)

    print_posts(output)

    if config.get("save_drafts", True):
        draft_path = save_draft(
            output=output,
            book=book_name,
            pages=pages,
            notes=notes,
            source=source_label,
            drafts_dir=config.get("drafts_dir", "posts/drafts"),
        )
        print(f"Draft guardado en: {draft_path}")


if __name__ == "__main__":
    main()
