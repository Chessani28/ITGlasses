p = "backend/app/assistant/web_search.py"
s = open(p, encoding="utf-8").read()
marker = "@dataclass(frozen=True)"
idx = s.rfind(marker)
if idx == -1:
    raise SystemExit("marker not found")
s = s[:idx]
open(p, "w", encoding="utf-8").write(s)
print("truncated at", idx)
