import argparse
import json
import sys
import requests

WEBHOOK_URL = "https://web.furycloud.io/api/proxy/verdi_flows/webhook/0ebf91dc-ff6f-421b-8cff-7fcf2d0f1e31"


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
    try:
        from pypdf import PdfReader
    except ImportError:
        print("Instalá pypdf: pip install pypdf", file=sys.stderr)
        sys.exit(1)

    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    page_numbers = parse_pages(pages_str) if pages_str else list(range(1, total + 1))
    texts = []
    for n in page_numbers:
        if n < 1 or n > total:
            print(f"Advertencia: página {n} fuera de rango (el PDF tiene {total} páginas)", file=sys.stderr)
            continue
        texts.append(reader.pages[n - 1].extract_text() or "")
    return "\n\n".join(texts)


def parse_args():
    parser = argparse.ArgumentParser(description="Generador de posts de LinkedIn sobre ciberseguridad")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book", help="Nombre del libro (puede ser informal, ej: 'el Paar')")
    group.add_argument("--pdf", help="Ruta al PDF del libro")
    parser.add_argument("--pages", default=None, help="Páginas leídas (ej: 1-50 o 10,15,20-30). Si no se especifica, usa todo el PDF")
    parser.add_argument("--notes", default="", help="Notas adicionales (opcional)")
    return parser.parse_args()


def generate_post(book: str, pages: str, notes: str, pdf_text: str = "") -> str:
    payload = {"book": book, "pages": pages, "notes": notes}
    if pdf_text:
        payload["pdf_text"] = pdf_text[:8000]  # limit to avoid token overflow
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=60)
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
    args = parse_args()

    pdf_text = ""
    book_name = args.book

    if args.pdf:
        label = f"páginas {args.pages}" if args.pages else "todo el PDF"
        print(f"\nExtrayendo {label}...")
        pdf_text = extract_pdf_text(args.pdf, args.pages)
        if not pdf_text.strip():
            print("No se pudo extraer texto del PDF.", file=sys.stderr)
            sys.exit(1)
        book_name = args.pdf.split("/")[-1].replace(".pdf", "")

    pages_label = args.pages or "completo"
    print(f"Generando posts para: {book_name} (páginas {pages_label})...")
    output = generate_post(book_name, args.pages, args.notes, pdf_text)

    if not output:
        print("No se recibió respuesta del webhook.", file=sys.stderr)
        sys.exit(1)

    print_posts(output)


if __name__ == "__main__":
    main()
