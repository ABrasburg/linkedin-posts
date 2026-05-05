import argparse
import json
import sys
import requests

WEBHOOK_URL = "https://web.furycloud.io/api/proxy/verdi_flows/webhook/0ebf91dc-ff6f-421b-8cff-7fcf2d0f1e31"


def parse_args():
    parser = argparse.ArgumentParser(description="Generador de posts de LinkedIn sobre ciberseguridad")
    parser.add_argument("--book", required=True, help="Nombre del libro")
    parser.add_argument("--pages", required=True, help="Páginas leídas (ej: 1-50)")
    parser.add_argument("--notes", default="", help="Notas adicionales (opcional)")
    return parser.parse_args()


def generate_post(book: str, pages: str, notes: str) -> str:
    payload = {"book": book, "pages": pages, "notes": notes}
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
    print(f"\nGenerando posts para: {args.book} (páginas {args.pages})...")
    output = generate_post(args.book, args.pages, args.notes)
    if not output:
        print("No se recibió respuesta del webhook.", file=sys.stderr)
        sys.exit(1)
    print_posts(output)


if __name__ == "__main__":
    main()
