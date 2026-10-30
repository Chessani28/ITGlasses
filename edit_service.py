import re

with open('D:\ITGlasses\backend\app\assistant\service.py', 'r') as f:
    content = f.read()

# Find and replace the match_procedures function
old = """def match_procedures(procedures, message):
    """Return the procedures whose title is referenced by ``message``.

    """
    matched = []
    message = _fold(message)
    for p in procedures:
        title = _fold(p.get("title", ""))
        if title in message:
            matched.append(p)
    return matched"""

new = """def _strip_spanish_prefix(message):
    """Remove common Spanish prefixes like 'pasos de' from the message before matching.

    This allows procedure names in English to be matched even when surrounded by
    Spanish text (e.g., "pasos de WINDOWS DEPLOYMENT PROCEDURE" will match
    "WINDOWS DEPLOYMENT PROCEDURE").
    """
    if not message:
        return message
    # Spanish patterns that may precede procedure names
    spanish_patterns = [
        r"^pasos?\\s+de\\s+",  # "pasos de", "paso de"
        r"^pasos?\\s+a\\s+",   # "pasos a"
    ]
    text = message
    for pattern in spanish_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def match_procedures(procedures, message):
    """Return the procedures whose title is referenced by ``message``.

    Handles mixed-language messages where the procedure name is English
    but the surrounding text is Spanish (e.g., "pasos de WINDOWS DEPLOYMENT PROCEDURE").
    """
    matched = []
    # Strip Spanish prefixes that may precede the procedure name
    message = _strip_spanish_prefix(message)
    message = _fold(message)
    for p in procedures:
        title = _fold(p.get("title", ""))
        if title in message:
            matched.append(p)
    return matched"""

if old in content:
    content = content.replace(old, new)
    with open('D:\ITGlasses\backend\app\assistant\service.py', 'w') as f:
        f.write(content)
    print('Replacement successful')
else:
    print('Old text not found')
    # Let's print what we're looking for
    import subprocess
    result = subprocess.run(['grep', '-n', 'def match_procedures', 'D:\\ITGlasses\\backend\\app\\assistant\\service.py'], capture_output=True, text=True)
    print('Found at:', result.stdout)