import os
import sys
from contextlib import contextmanager
from pathlib import Path

import requests

WEBHOOK_URL = "https://web.furycloud.io/api/proxy/verdi_flows/webhook/0ebf91dc-ff6f-421b-8cff-7fcf2d0f1e31"
DEFAULT_OCR_LANGUAGE = "eng"
MAX_PDF_TEXT_CHARS = 100000


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


def generate_post(book: str, pages: str, notes: str, pdf_text: str = "") -> str:
    payload = {"book": book, "pages": pages or "completo", "notes": notes}
    if pdf_text:
        payload["ocr"] = False
        payload["pdf_text"] = pdf_text[:MAX_PDF_TEXT_CHARS]
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=120)
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


def print_posts(output: str):
    parts = output.split("---")
    for i, part in enumerate(parts, 1):
        post = part.strip()
        if not post:
            continue
        print(f"\n{'='*60}")
        print(f"  OPCIÓN {i}")
        print(f"{'='*60}\n")
        print(post)
    print(f"\n{'='*60}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generador de posts de LinkedIn sobre ciberseguridad")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book", help="Nombre del libro (puede ser informal, ej: 'el Paar')")
    group.add_argument("--pdf", help="Ruta al PDF")
    parser.add_argument("--pages", default=None, help="Páginas (ej: 1-50). Si no se especifica, usa todo el PDF")
    parser.add_argument("--notes", default="", help="Notas adicionales (opcional)")
    parser.add_argument("--ocr-lang", default=DEFAULT_OCR_LANGUAGE, help="Idioma para OCR local en PDFs escaneados")
    args = parser.parse_args()

    pdf_text = ""
    book_name = args.book

    if args.pdf:
        book_name = args.pdf.split("/")[-1].replace(".pdf", "")
        label = f"páginas {args.pages}" if args.pages else "todo el PDF"
        print(f"\nExtrayendo {label}...")

        pdf_text = extract_pdf_text(args.pdf, args.pages)

        if not pdf_text.strip():
            print("PDF escaneado detectado, usando OCR local...")
            try:
                pdf_text = extract_pdf_ocr_text(args.pdf, args.pages, args.ocr_lang)
            except Exception as e:
                print(f"No se pudo hacer OCR local: {e}", file=sys.stderr)

            if pdf_text.strip():
                print(f"OCR local completado ({len(pdf_text.strip())} caracteres).")
            else:
                print("No se pudo extraer texto del PDF ni leerlo con OCR local.", file=sys.stderr)
                sys.exit(1)

    pages_label = args.pages or "completo"
    print(f"Generando posts para: {book_name} ({pages_label})...")
    output = generate_post(book_name, args.pages, args.notes, pdf_text)

    if not output:
        print("No se recibió respuesta del webhook.", file=sys.stderr)
        sys.exit(1)

    print_posts(output)


if __name__ == "__main__":
    main()
