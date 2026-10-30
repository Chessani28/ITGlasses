path = "d:/ITGlasses/frontend/voice-ui.mjs"
with open(path, "r", encoding="utf-8") as f:
    d = f.read()

# Fix all debugger issues: the $ helper is id => document.getElementById(id) without # prefix
# Line 44: fix voice-diagnostic-toggle in state method
old = '"voice-diagnostic-toggle").disabled = active;'
new = '"voice-diagnostic-toggle").disabled = active; $("voice-diagnostic-toggle").disabled = active;'
if old in d:
    d = d.replace(old, new)
    print("Fixed line 44")

# Fix all occurrences of $("#..." for the $ helper which takes ID without #
d = d.replace('$("#voice-diagnostic-toggle")', '$("voice-diagnostic-toggle")')
print("Fixed voice-diagnostic-toggle in all places")

d = d.replace('$("#voice-diag-feedback")', '$("voice-diag-feedback")')
print("Fixed voice-diag-feedback")

with open(path, "w", encoding="utf-8") as f:
    f.write(d)
print("Saved voice-ui.mjs")
