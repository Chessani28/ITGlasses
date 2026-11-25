"""Carga de casos del examen: cada archivo *.json es una lista de casos; los casos malos se reportan sin
detener la carga del resto."""
import json
from dataclasses import dataclass
from pathlib import Path

from evals.checks import KNOWN_CHECKS

CHANNELS = ("text", "voice")


@dataclass
class Turn:
    message: str
    expect: dict | None


@dataclass
class Case:
    id: str
    category: str
    channel: str
    turns: list[Turn]
    file: str


def _case(raw, file):
    """(Case, None) o (None, motivo)."""
    if not isinstance(raw, dict):
        return None, "el caso no es un objeto"
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        return None, "falta id"
    category = raw.get("category")
    if not isinstance(category, str) or not category.strip():
        return None, "falta category"
    channel = raw.get("channel", "text")
    if channel not in CHANNELS:
        return None, f"channel inválido: {channel!r}"
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        return None, "turns vacío"
    parsed = []
    for index, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict) or not isinstance(turn.get("message"), str) or not turn["message"].strip():
            return None, f"turno {index} sin message"
        expect = turn.get("expect")
        if expect is not None:
            if not isinstance(expect, dict):
                return None, f"turno {index}: expect no es un objeto"
            unknown = sorted(set(expect) - KNOWN_CHECKS)
            if unknown:
                return None, f"turno {index}: revisión desconocida {', '.join(unknown)}"
        parsed.append(Turn(turn["message"], expect))
    return Case(case_id, category, channel, parsed, file), None


def load_cases(paths):
    """Los casos válidos de los *.json de los directorios dados (en orden) y los errores
    "<archivo>:<id>: <motivo>"."""
    cases, errors, seen = [], [], set()
    for directory in paths:
        for path in sorted(Path(directory).glob("*.json")):
            try:
                raw_cases = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}:-: no se pudo leer ({type(exc).__name__})")
                continue
            if not isinstance(raw_cases, list):
                errors.append(f"{path.name}:-: el archivo no es una lista de casos")
                continue
            for raw in raw_cases:
                case, reason = _case(raw, path.name)
                label = raw.get("id", "?") if isinstance(raw, dict) else "?"
                if case is None:
                    errors.append(f"{path.name}:{label}: {reason}")
                elif case.id in seen:
                    errors.append(f"{path.name}:{case.id}: id duplicado")
                else:
                    seen.add(case.id)
                    cases.append(case)
    return cases, errors
