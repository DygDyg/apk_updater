from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from app.api.app_factory import create_app

        return create_app
    raise AttributeError(name)
