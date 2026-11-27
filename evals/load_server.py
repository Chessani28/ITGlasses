"""Backend aislado para la prueba de carga: lo arranca evals.load_test, nunca a mano.

Lee su carpeta de trabajo de ITGLASSES_LOAD_WORK y su puerto de ITGLASSES_PORT (8010). Usa el modelo real de
Ollama, pero sus usuarios, historial, dispositivos y respaldos viven en esa carpeta, y los manuales son una COPIA
del índice: nunca escribe en storage/ ni toca el puerto 8000 ni Caddy.
"""
import os
import sys
from pathlib import Path


def main():
    work = Path(os.environ["ITGLASSES_LOAD_WORK"])
    import uvicorn

    from backend.app.core.config import settings
    from backend.app.knowledge.embeddings import OllamaEmbeddings
    from backend.app.knowledge.grounding import EvidenceSelector
    from backend.app.knowledge.service import KnowledgeService
    from backend.app.main import create_app

    if settings.port == 8000:
        sys.exit("La prueba de carga nunca usa el puerto 8000.")
    knowledge = KnowledgeService(work, OllamaEmbeddings(settings.ollama_url),
                                 EvidenceSelector(settings.model, settings.ollama_url, settings.keep_alive))
    app = create_app(knowledge=knowledge)
    uvicorn.run(app, host="127.0.0.1", port=settings.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
