import sys
sys.path.insert(0, r"D:\ITGlasses\backend")
from backend.app.main import create_app
from fastapi.testclient import TestClient

# Create the app
app = create_app()
client = TestClient(app)

# List all routes
print("Available routes:")
for route in app.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        methods = ", ".join(route.methods)
        print(f"  {methods:10} {route.path}")