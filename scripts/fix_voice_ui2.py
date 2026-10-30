path = "d:/ITGlasses/frontend/voice-ui.mjs"
with open(path, "r", encoding="utf-8") as f:
    d = f.read()

# Fix all remaining $("#id") patterns to $("id") 
# The $ helper is id => document.getElementById(id), no # prefix
import re
d2 = re.sub(r'\$\(\\"([a-z][a-z0-9-]*)\\)', r'$("\1")', d)
if d2 != d:
    d = d2
    print("Fixed all remaining $("#...") patterns")

with open(path, "w", encoding="utf-8") as f:
    f.write(d)
print("Saved voice-ui.mjs")
