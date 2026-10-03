"""Run the stateful Flask application in one persistent Waitress process."""

from __future__ import annotations

import os
import signal

from lg_assistant import config
from lg_assistant.web.app import MAX_UPLOAD_MB, create_app


def _public_scheme() -> str:
    scheme = os.getenv("PUBLIC_SCHEME", "https").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("PUBLIC_SCHEME must be http or https.")
    return scheme


def create_production_app(*, start_workers: bool = False):
    """Refuse public access without a sufficiently long access token."""
    if len(config.ACCESS_TOKEN.strip()) < 24:
        raise RuntimeError("ACCESS_TOKEN must contain at least 24 characters for production.")
    try:
        workers = int(os.getenv("TASK_WORKERS", "2"))
    except ValueError:
        raise ValueError("TASK_WORKERS must be an integer between 1 and 16.") from None
    if not 1 <= workers <= 16:
        raise ValueError("TASK_WORKERS must be an integer between 1 and 16.")
    return create_app(secure_cookie=_public_scheme() == "https", start_workers=start_workers, task_workers=workers)


def main() -> None:
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError:
        raise ValueError("PORT must be an integer between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")

    from waitress import serve
    from lg_assistant.runtime import single_instance

    def stop(_signum, _frame):
        raise SystemExit(0)

    # Hold the volume lock before recovering tasks. SIGTERM waits for active workers.
    with single_instance(config.DATA_DIR):
        app = create_production_app(start_workers=True)
        signal.signal(signal.SIGTERM, stop)
        try:
            serve(
                app,
                host="0.0.0.0",
                port=port,
                threads=8,
                url_scheme=_public_scheme(),
                max_request_body_size=MAX_UPLOAD_MB * 1024 * 1024,
            )
        finally:
            app.extensions["task_manager"].shutdown(wait=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
