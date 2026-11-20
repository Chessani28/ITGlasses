"""Evaluación del "cerebro" de ITGlasses con Ollama real.

Lanza conversaciones típicas (manuales, preguntas generales, web y seguimientos)
directamente contra UnifiedAssistantService, sin pasar por HTTP ni por el token.
Solo LEE la base de conocimiento: no sube, borra ni modifica documentos.

Uso (desde la raíz del repo, con Ollama corriendo):

    python scripts/eval_brain.py --label nuevo
    python scripts/eval_brain.py --label viejo          # en la otra rama
    python scripts/eval_brain.py --compare .cache/eval-brain/viejo.json .cache/eval-brain/nuevo.json

Para correrlo en main (donde este archivo no existe), cópialo antes fuera del repo:

    git show claude/mejora-cerebro:scripts/eval_brain.py > %TEMP%\\eval_brain.py
    git checkout main
    python %TEMP%\\eval_brain.py --label viejo --root .

Preguntas propias: --questions mis_preguntas.json con el formato
    [{"name": "vpn", "turns": ["¿Qué es una VPN?", "¿y cómo la configuro?"]}, ...]
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_CONVERSATIONS = [
    {"name": "manual-pasos-completos", "turns": [
        "¿Cuáles son los pasos de WINDOWS DEPLOYMENT PROCEDURE?",
        "¿Cuál es el siguiente paso?",
        "¿Y el paso anterior?",
        "¿En qué documento viene eso?",
    ]},
    {"name": "manual-paso-por-numero", "turns": [
        "¿Cuáles son los pasos de COMPUTER PREPARATION?",
        "Dime el paso 4",
        "¿Cuál es el último paso?",
    ]},
    {"name": "manual-pregunta-abierta", "turns": [
        "¿Cómo preparo una computadora nueva según el manual?",
    ]},
    {"name": "manual-no-documentado", "turns": [
        "¿Cuál es el procedimiento de la empresa para resetear contraseñas de SAP?",
    ]},
    {"name": "general-conceptos", "turns": [
        "¿Qué es una VPN?",
        "¿Y en qué se diferencia de un proxy?",
        "¿Cuál me conviene para trabajar desde casa?",
    ]},
    {"name": "general-falla", "turns": [
        "Mi laptop no se conecta al wifi",
        "Ya reinicié el router y sigue igual",
        "¿Y si es la tarjeta de red?",
    ]},
    {"name": "general-larga", "turns": [
        "Explícame qué es DNS",
        "¿Y DHCP?",
        "¿Qué pasa si los dos fallan?",
        "¿Cómo reviso eso en Windows?",
        "¿Y en una Mac?",
        "Resúmeme todo lo que hablamos",
    ]},
    {"name": "web", "turns": [
        "¿Cuál es la última versión de Windows 11?",
        "¿Cuál es la versión más reciente de Python?",
    ]},
    {"name": "ambiguo", "turns": [
        "No funciona",
        "La impresora",
    ]},
    {"name": "social", "turns": ["Hola, buenas noches", "Gracias"]},
]


def build_service(root):
    sys.path.insert(0, str(root))
    from backend.app.core.config import load_settings
    from backend.app.providers.ollama import OllamaProvider
    from backend.app.knowledge.service import KnowledgeService
    from backend.app.knowledge.embeddings import OllamaEmbeddings
    from backend.app.knowledge.grounding import EvidenceSelector
    from backend.app.assistant.service import UnifiedAssistantService

    settings = load_settings(root / "config.toml")
    provider = OllamaProvider(settings)
    # Constructores compatibles con la versión vieja y la nueva.
    knowledge = KnowledgeService(root, OllamaEmbeddings(settings.ollama_url),
                                 EvidenceSelector(settings.model, settings.ollama_url))
    knowledge.writer = provider  # en la versión vieja este atributo no se usa
    return settings, provider, UnifiedAssistantService(knowledge, provider)


def speech(text, root):
    try:
        from backend.app.voice.normalize import normalize_for_speech
        return normalize_for_speech(text)
    except Exception:  # noqa: BLE001
        return text


async def run(root, conversations, label, out_dir):
    settings, provider, service = build_service(root)
    try:
        await provider.ensure_available()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Ollama o el modelo {settings.model} no están disponibles: {exc!r}")

    results = {"label": label, "model": settings.model, "started": datetime.now().isoformat(),
               "settings": {k: getattr(settings, k) for k in (
                   "context_length", "max_output_tokens", "keep_alive", "general_temperature",
                   "grounded_temperature", "top_p", "top_k", "min_p") if hasattr(settings, k)},
               "conversations": []}
    for conversation in conversations:
        session_id, turns = None, []
        print(f"\n=== {conversation['name']} ===")
        for message in conversation["turns"]:
            start = time.perf_counter()
            try:
                result = await service.chat(message, session_id=session_id)
                error = None
            except Exception as exc:  # noqa: BLE001
                result, error = {"answer": "", "route": "error", "sources": []}, repr(exc)
            seconds = round(time.perf_counter() - start, 2)
            session_id = result.get("session_id", session_id)
            answer = result.get("answer") or ""
            spoken = speech(answer, root)
            turns.append({"message": message, "route": result.get("route"), "seconds": seconds,
                          "answer": answer, "spoken": spoken, "spoken_chars": len(spoken),
                          "sources": len(result.get("sources") or []), "error": error})
            print(f"> {message}\n  [{result.get('route')}, {seconds}s] {spoken[:300]}")
        results["conversations"].append({"name": conversation["name"], "turns": turns})

    all_turns = [t for c in results["conversations"] for t in c["turns"]]
    results["summary"] = {
        "turns": len(all_turns),
        "errors": sum(1 for t in all_turns if t["error"]),
        "avg_seconds": round(sum(t["seconds"] for t in all_turns) / max(1, len(all_turns)), 2),
        "max_seconds": max((t["seconds"] for t in all_turns), default=0),
        "avg_spoken_chars": round(sum(t["spoken_chars"] for t in all_turns) / max(1, len(all_turns))),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResumen: {results['summary']}\nGuardado en {path}")


def compare(old_path, new_path, out_dir):
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    lines = [f"# Comparación: {old['label']} vs {new['label']}", "",
             "| Métrica | " + old["label"] + " | " + new["label"] + " |", "|---|---|---|"]
    for key in old["summary"]:
        lines.append(f"| {key} | {old['summary'][key]} | {new['summary'].get(key)} |")
    new_by_name = {c["name"]: c for c in new["conversations"]}
    for conversation in old["conversations"]:
        other = new_by_name.get(conversation["name"], {"turns": []})
        lines += ["", f"## {conversation['name']}"]
        for index, turn in enumerate(conversation["turns"]):
            match = other["turns"][index] if index < len(other["turns"]) else None
            lines += ["", f"**> {turn['message']}**", "",
                      f"- {old['label']} ({turn['route']}, {turn['seconds']}s): {turn['spoken']}"]
            if match:
                lines.append(f"- {new['label']} ({match['route']}, {match['seconds']}s): {match['spoken']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"comparacion-{old['label']}-vs-{new['label']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Comparación guardada en {path}")


def main():
    parser = argparse.ArgumentParser(description="Evalúa el cerebro de ITGlasses con Ollama real.")
    parser.add_argument("--label", default=datetime.now().strftime("eval-%Y%m%d-%H%M%S"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Raíz del repo ITGlasses (por defecto, la de este script).")
    parser.add_argument("--questions", type=Path, help="JSON con conversaciones propias.")
    parser.add_argument("--out", type=Path, help="Carpeta de resultados (por defecto .cache/eval-brain).")
    parser.add_argument("--compare", nargs=2, metavar=("VIEJO.json", "NUEVO.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    out_dir = (args.out or root / ".cache" / "eval-brain").resolve()
    if args.compare:
        compare(*args.compare, out_dir)
        return
    conversations = DEFAULT_CONVERSATIONS
    if args.questions:
        conversations = json.loads(args.questions.read_text(encoding="utf-8"))
    asyncio.run(run(root, conversations, args.label, out_dir))


if __name__ == "__main__":
    main()
