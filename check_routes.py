import sys
sys.path.insert(0, r"D:\ITGlasses\backend")
from backend.app.knowledge.routes import router
print("Knowledge routes:")
for r in router.routes:
    if hasattr(r, "path"):
        print(f"  {r.path}")