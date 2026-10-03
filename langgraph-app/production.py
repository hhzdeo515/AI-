"""Run the stateful Flask application in one persistent Waitress process."""

from __future__ import annotations

import os

from lg_assistant import config
from lg_assistant.web.app import MAX_UPLOAD_MB, create_app


def _public_scheme() -> str:
    scheme = os.getenv("PUBLIC_SCHEME", "https").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("PUBLIC_SCHEME must be http or https.")
    return scheme


def create_production_app():
    """Refuse public access without a sufficiently long access token."""
    if len(config.ACCESS_TOKEN.strip()) < 24:
        raise RuntimeError("ACCESS_TOKEN must contain at least 24 characters for production.")
    return create_app(secure_cookie=_public_scheme() == "https")


def main() -> None:
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError:
        raise ValueError("PORT must be an integer between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")

    app = create_production_app()
    from waitress import serve

    # Tasks and progress are process-local: run one process and one replica.
    serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=8,
        url_scheme=_public_scheme(),
        max_request_body_size=MAX_UPLOAD_MB * 1024 * 1024,
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
