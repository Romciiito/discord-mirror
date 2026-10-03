from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web

from .web import create_app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    data = os.environ.get("DATA_DIR")
    if not data:
        data = str(Path(__file__).resolve().parent.parent / "data")
    app = create_app(data)
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
