path = "d:/ITGlasses/frontend/styles.css"
with open(path, "r", encoding="utf-8") as f:
    d = f.read()

new_css = """/* Diagnostic mode styles */
.diagnostic-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 12px;
  font-size: 0.85rem;
  color: #888;
}
.diagnostic-toggle input {
  cursor: pointer;
}
.diagnostic-toggle .diagnostic-label {
  cursor: pointer;
}
.diagnostic-toggle input:checked ~ .diagnostic-label {
  color: #666;
}
.voice-diagnostic-section {
  margin-top: 12px;
  padding: 10px;
  background: #1a1d20;
  border-radius: 6px;
  border: 1px solid #333;
}
.voice-diagnostic-section h3 {
  margin: 0 0 8px 0;
  font-size: 0.9rem;
  color: #888;
}
.voice-diagnostic-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 0.85rem;
  color: #bbb;
}
.voice-diagnostic-item small {
  color: #666;
}
.voice-diagnostic-item strong {
  color: #ddd;
  text-align: right;
}
.voice-diagnostic-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 8px 0;
}
.voice-diagnostic-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  font-size: 0.85rem;
}
.voice-diagnostic-row input {
  width: 80px;
}
.voice-diagnostic-row button {
  margin-left: auto;
}
.voice-diagnostic-row audio {
  margin-left: auto;
}
"""

# Insert before .voice-panel rule
marker = ".voice-panel{margin:0 32px 16px"
if marker in d:
    d = d.replace(marker, new_css + "\n" + marker)
    with open(path, "w", encoding="utf-8") as f:
        f.write(d)
    print("Inserted diagnostic CSS before .voice-panel")
else:
    print("Marker not found")
    # Show last 100 chars
    print(repr(d[-200:]))
