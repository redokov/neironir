"""FastAPI application entry point."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    app = FastAPI(title="neironir", version="0.0.1")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
