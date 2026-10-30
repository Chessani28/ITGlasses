with open(r'd:\ITGlasses\backend\app\knowledge\service.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
# Print lines 220-240 (the relevance filter area)
for i in range(219, min(245, len(lines))):
    print(f'{i+1}: {lines[i]}', end='')