"""Resumen, comparación y reporte Markdown de una corrida del examen."""
import math
import statistics

LIVE_NOTE = "Latencias medidas con el backend activo."


def _p90(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def summarize(results, load_errors, backend_live):
    """Aciertos y latencias (mediana y p90 por turno) totales y por categoría; casos fallidos con su motivo."""
    categories = {}
    for result in results:
        entry = categories.setdefault(result["category"], {"passed": 0, "cases": 0, "_latencies": []})
        entry["cases"] += 1
        entry["passed"] += bool(result["passed"])
        entry["_latencies"].extend(turn["latency_ms"] for turn in result["turns"])
    for entry in categories.values():
        latencies = entry.pop("_latencies")
        entry["median_ms"] = round(statistics.median(latencies)) if latencies else 0
        entry["p90_ms"] = round(_p90(latencies)) if latencies else 0
    return {
        "total": {"passed": sum(bool(result["passed"]) for result in results), "cases": len(results)},
        "categories": dict(sorted(categories.items())),
        "failed": [{"id": result["id"],
                    "failures": [failure for turn in result["turns"] for failure in turn["failures"]]}
                   for result in results if not result["passed"]],
        "passed_ids": [result["id"] for result in results if result["passed"]],
        "load_errors": list(load_errors),
        "backend_live": bool(backend_live),
    }


def compare(current, previous):
    """Casos que pasaron de pasar a fallar y al revés (solo los presentes en ambas corridas)."""
    if not previous:
        return {"newly_failing": [], "newly_passing": []}
    before_pass = set(previous.get("passed_ids", []))
    before_fail = {item["id"] for item in previous.get("failed", [])}
    now_pass = set(current.get("passed_ids", []))
    now_fail = {item["id"] for item in current.get("failed", [])}
    return {"newly_failing": sorted(now_fail & before_pass), "newly_passing": sorted(now_pass & before_fail)}


def to_markdown(summary, comparison):
    total = summary["total"]
    lines = ["# Examen del cerebro", "", f"**Total:** {total['passed']}/{total['cases']}", ""]
    if summary.get("backend_live"):
        lines += [LIVE_NOTE, ""]
    lines += ["| Categoría | Aciertos | Mediana (ms) | p90 (ms) |", "|---|---|---|---|"]
    for name, entry in summary["categories"].items():
        lines.append(f"| {name} | {entry['passed']}/{entry['cases']} | {entry['median_ms']} | {entry['p90_ms']} |")
    if comparison["newly_failing"] or comparison["newly_passing"]:
        lines += ["", "## Cambios contra la corrida anterior", ""]
        lines += [f"- Ahora falla: {case_id}" for case_id in comparison["newly_failing"]]
        lines += [f"- Ahora pasa: {case_id}" for case_id in comparison["newly_passing"]]
    if summary["failed"]:
        lines += ["", "## Fallidos", ""]
        lines += [f"- **{item['id']}**: {'; '.join(item['failures'])}" for item in summary["failed"]]
    if summary["load_errors"]:
        lines += ["", "## Errores de carga", ""]
        lines += [f"- {error}" for error in summary["load_errors"]]
    return "\n".join(lines) + "\n"
