import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import linkedin


DEFAULT_CONFIG = {
    "output_dir": "weekly-plans",
    "progress_reports_dir": "weekly-plans/progress",
    "top_recommendations": 3,
    "post_recommendations": 1,
    "focus_tags": ["iam", "appsec", "offsec", "detection", "programming", "genai"],
    "trend_cards": [],
}


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return DEFAULT_CONFIG.copy()

    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    return config


def score_card(card: dict, focus_tags: list[str]) -> int:
    card_tags = set(card.get("tags", []))
    focus = set(focus_tags)
    return int(card.get("trend_score", 0)) + (len(card_tags & focus) * 10)


def select_cards(config: dict) -> list[dict]:
    focus_tags = config.get("focus_tags", [])
    cards = config.get("trend_cards", [])
    ranked = sorted(
        cards,
        key=lambda card: (score_card(card, focus_tags), card.get("title", "")),
        reverse=True,
    )
    return ranked[: int(config.get("top_recommendations", 3))]


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def source_list(sources: list[dict]) -> str:
    lines = []
    for source in sources:
        name = source.get("name", "Fuente")
        url = source.get("url", "")
        lines.append(f"- {name}: {url}" if url else f"- {name}")
    return "\n".join(lines)


def read_done_text(done: str, done_file: str | None) -> str:
    parts = []

    if done_file:
        if done_file == "-":
            file_text = sys.stdin.read()
        else:
            path = Path(done_file)
            if not path.exists():
                raise FileNotFoundError(f"No existe el archivo de avances: {path}")
            file_text = path.read_text(encoding="utf-8")
        if file_text.strip():
            parts.append(file_text.strip())

    if done and done.strip():
        parts.append(done.strip())

    return "\n\n".join(parts)


def find_latest_plan(output_dir: str) -> Path | None:
    path = Path(output_dir)
    if not path.exists():
        return None

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    candidates = [
        plan
        for plan in path.glob("*.md")
        if date_pattern.fullmatch(plan.stem)
    ]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def resolve_plan_path(output_dir: str, plan_date: str | None) -> Path | None:
    if plan_date:
        path = Path(output_dir) / f"{plan_date}.md"
        return path if path.exists() else None

    today_path = Path(output_dir) / f"{date.today().isoformat()}.md"
    if today_path.exists():
        return today_path

    return find_latest_plan(output_dir)


def card_context(card: dict) -> str:
    parts = [
        f"Tendencia: {card.get('title')}",
        f"Por que importa ahora: {card.get('why_now')}",
        f"Por que encaja con Agustin: {card.get('why_you')}",
        "",
        "Lecturas sugeridas:",
        bullet_list(card.get("reading", [])),
        "",
        "Demo o codigo sugerido:",
        card.get("build", ""),
        "",
        "Angulos posibles para LinkedIn:",
        bullet_list(card.get("linkedin_angles", [])),
        "",
        "Video sugerido:",
        card.get("video", ""),
        "",
        "Fuentes:",
        source_list(card.get("sources", [])),
    ]
    return "\n".join(parts)


def cards_context(cards: list[dict]) -> str:
    return "\n\n---\n\n".join(card_context(card) for card in cards)


def completed_work_context(done_text: str, cards: list[dict], plan_path: Path | None) -> str:
    parts = [
        "Trabajo realizado esta semana:",
        done_text,
        "",
        (
            "Usar el trabajo realizado como fuente principal. No inventar avances, "
            "resultados, lecturas ni codigo que no aparezcan ahi."
        ),
    ]

    if plan_path:
        parts.extend([
            "",
            f"Plan semanal base: {plan_path}",
        ])

    if cards:
        parts.extend([
            "",
            "Contexto del plan semanal para elegir angulo y vocabulario:",
            cards_context(cards),
        ])

    return "\n".join(parts)


def generate_drafts(cards: list[dict], args, weekly_config: dict) -> list[dict]:
    linkedin.load_env_file()
    linkedin_config = linkedin.load_config(args.linkedin_config)
    drafts = []

    for card in cards[: int(weekly_config.get("post_recommendations", 1))]:
        notes = (
            "Generar posts a partir de esta recomendacion semanal. "
            "No mencionar que viene de un agente, plan semanal o radar de tendencias. "
            "Elegir una sola idea fuerte y conectarla con IAM, AppSec, OffSec, "
            "Detection o backend segun corresponda."
        )
        output = linkedin.generate_post_with_retries(
            book=card.get("title", "Tema semanal"),
            pages="recomendacion semanal",
            notes=notes,
            pdf_text=card_context(card),
            config=linkedin_config,
        )
        draft_path = linkedin.save_draft(
            output=output,
            book=card.get("title", "Tema semanal"),
            pages="recomendacion semanal",
            notes=notes,
            source=", ".join(source.get("url", "") for source in card.get("sources", [])),
            drafts_dir=linkedin_config.get("drafts_dir", "posts/drafts"),
        )
        drafts.append({"title": card.get("title", "Tema semanal"), "path": str(draft_path)})

    return drafts


def generate_done_drafts(
    done_text: str,
    cards: list[dict],
    plan_path: Path | None,
    args,
) -> list[dict]:
    linkedin.load_env_file()
    linkedin_config = linkedin.load_config(args.linkedin_config)
    notes = (
        "Generar posts a partir de trabajo real hecho esta semana. "
        "No mencionar que viene de un agente, plan semanal o reporte de avances. "
        "No inventar resultados: si el usuario dice que leyo, codeo, probo o anoto algo, "
        "usar eso como evidencia concreta. "
        "Elegir una sola idea fuerte y convertirla en un post tecnico, cercano y publicable."
    )
    output = linkedin.generate_post_with_retries(
        book="Trabajo semanal",
        pages="avance semanal",
        notes=notes,
        pdf_text=completed_work_context(done_text, cards, plan_path),
        config=linkedin_config,
    )
    draft_path = linkedin.save_draft(
        output=output,
        book="Trabajo semanal",
        pages="avance semanal",
        notes=notes,
        source=str(plan_path or ""),
        drafts_dir=linkedin_config.get("drafts_dir", "posts/drafts"),
    )
    return [{"title": "Trabajo semanal", "path": str(draft_path)}]


def render_plan(plan_date: str, cards: list[dict], drafts: list[dict]) -> str:
    lines = [
        f"# Plan semanal - {plan_date}",
        "",
        "## Foco",
        "",
        "Convertir tendencias utiles en lectura, codigo, posts y videos cortos.",
        "",
        "## Recomendaciones",
        "",
    ]

    for index, card in enumerate(cards, 1):
        lines.extend([
            f"### {index}. {card.get('title')}",
            "",
            f"**Por que ahora:** {card.get('why_now')}",
            "",
            f"**Por que encaja:** {card.get('why_you')}",
            "",
            "**Leer:**",
            bullet_list(card.get("reading", [])),
            "",
            f"**Codear:** {card.get('build')}",
            "",
            "**Posts posibles:**",
            bullet_list(card.get("linkedin_angles", [])),
            "",
            f"**Video:** {card.get('video')}",
            "",
            "**Fuentes:**",
            source_list(card.get("sources", [])),
            "",
        ])

    if drafts:
        lines.extend(["## Drafts generados", ""])
        for draft in drafts:
            lines.append(f"- {draft['title']}: {draft['path']}")
        lines.append("")

    lines.extend([
        "## Checklist semanal",
        "",
        "- Elegir 1 lectura principal.",
        "- Codear una demo chica o escribir un ejemplo reproducible.",
        "- Publicar 1 post corto en LinkedIn.",
        "- Grabar 1 clase o video corto relacionado con programacion.",
        "- Guardar notas y links para reciclar en contenido largo.",
        "",
    ])
    return "\n".join(lines)


def render_progress_report(
    report_date: str,
    done_text: str,
    cards: list[dict],
    drafts: list[dict],
    plan_path: Path | None,
) -> str:
    lines = [
        f"# Avance semanal - {report_date}",
        "",
    ]

    if plan_path:
        lines.extend([
            f"Plan base: {plan_path}",
            "",
        ])

    lines.extend([
        "## Trabajo realizado",
        "",
        done_text.strip(),
        "",
        "## Angulos relacionados",
        "",
    ])

    for card in cards:
        lines.append(f"- {card.get('title')}: {card.get('why_you')}")
    lines.append("")

    if drafts:
        lines.extend(["## Drafts generados", ""])
        for draft in drafts:
            lines.append(f"- {draft['title']}: {draft['path']}")
        lines.append("")
    else:
        lines.extend([
            "## Drafts generados",
            "",
            "No se generaron drafts en esta corrida.",
            "",
        ])

    lines.extend(["## Siguiente paso", ""])
    if drafts:
        lines.append("- Elegir el draft mas natural y ajustarle la primera linea antes de publicar.")
    else:
        lines.append("- Generar drafts con el mismo avance sacando `--no-generate-posts`.")
    lines.extend([
        "- Guardar links, codigo o notas que puedan servir para un segundo post.",
        "",
    ])
    return "\n".join(lines)


def save_plan(content: str, output_dir: str, plan_date: str) -> Path:
    path = Path(output_dir) / f"{plan_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def save_progress_report(content: str, progress_reports_dir: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    path = Path(progress_reports_dir) / f"{timestamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Agente semanal de contenido tecnico")
    parser.add_argument("--config", default="weekly_agent_config.json")
    parser.add_argument("--linkedin-config", default="config.json")
    parser.add_argument(
        "--generate-posts",
        action="store_true",
        help="En modo plan, generar drafts desde la recomendacion principal",
    )
    parser.add_argument("--done", default="", help="Resumen de lo que hiciste esta semana")
    parser.add_argument("--done-file", help="Archivo con avances de la semana. Usar '-' para stdin")
    parser.add_argument("--plan-date", help="Fecha del plan base a usar, formato YYYY-MM-DD")
    parser.add_argument(
        "--no-generate-posts",
        action="store_true",
        help="En modo avances, guardar reporte sin llamar al webhook",
    )
    args = parser.parse_args()

    weekly_config = load_config(args.config)
    cards = select_cards(weekly_config)

    try:
        done_text = read_done_text(args.done, args.done_file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if done_text:
        plan_path = resolve_plan_path(
            weekly_config.get("output_dir", "weekly-plans"),
            args.plan_date,
        )
        drafts = (
            []
            if args.no_generate_posts
            else generate_done_drafts(done_text, cards, plan_path, args)
        )
        report = render_progress_report(
            report_date=date.today().isoformat(),
            done_text=done_text,
            cards=cards,
            drafts=drafts,
            plan_path=plan_path,
        )
        report_path = save_progress_report(
            report,
            weekly_config.get("progress_reports_dir", "weekly-plans/progress"),
        )
        print(f"Reporte de avance guardado en: {report_path}")
        if plan_path:
            print(f"Plan base usado: {plan_path}")
        else:
            print("Aviso: no se encontro un plan semanal base; se uso la config actual.")
        for draft in drafts:
            print(f"Draft guardado en: {draft['path']}")
        return

    plan_date = date.today().isoformat()
    drafts = generate_drafts(cards, args, weekly_config) if args.generate_posts else []
    plan = render_plan(plan_date, cards, drafts)
    plan_path = save_plan(plan, weekly_config.get("output_dir", "weekly-plans"), plan_date)
    print(f"Plan guardado en: {plan_path}")
    if drafts:
        for draft in drafts:
            print(f"Draft guardado en: {draft['path']}")


if __name__ == "__main__":
    main()
