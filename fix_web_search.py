p = "backend/app/assistant/web_search.py"
s = open(p, encoding="utf-8").read()
lines = s.split(chr(10))
hits = [i + 1 for i, r in enumerate(lines) if "@dataclass(frozen=True)" in r]
print("dataclass lines:", hits)
print("total lines:", len(lines))
