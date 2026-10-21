import os
from pathlib import Path

# Set only this process's temporary storage before loading the web framework.
temporary = Path(__file__).resolve().parents[1] / ".cache/tmp"
temporary.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(temporary)
os.environ["TMP"] = str(temporary)

import uvicorn

from backend.app.core.config import settings


if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host=settings.host, port=settings.port,
                access_log=False, log_level="critical")
