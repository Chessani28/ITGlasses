"""Backend logs: WARNING and above to the console (the start-backend.ps1 window), INFO and above to
logs\\backend.log (rotated: 1 MB x 5). The application's own lines carry codes, times and counts only, never a
question, an answer, a transcript or a prompt."""
import logging
import logging.handlers
from pathlib import Path

LOG_FILE = Path("logs") / "backend.log"
MAX_BYTES = 1_000_000
BACKUPS = 5
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_itglasses_handler"
# Third-party loggers that would repeat every request (URLs) at INFO; primp and ddgs would also write the public
# search query (the user's words) in its URLs.
QUIET = ("httpx", "httpcore", "multipart", "asyncio", "primp", "ddgs")


def configure_logging(root: Path, console=True) -> Path:
    """Idempotent: calling it again does not add handlers. Returns the log file path."""
    path = Path(root) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            logger.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                        encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _MARK, True)
    logger.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        stream.setFormatter(formatter)
        setattr(stream, _MARK, True)
        logger.addHandler(stream)
    logger.setLevel(logging.INFO)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    return path
