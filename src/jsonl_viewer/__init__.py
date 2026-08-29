"""Public facade for the isolated read-only JSONL viewer."""

from .contracts import ViewerHost, ViewerSpec
from .engine import view_jsonl

__all__ = ["ViewerHost", "ViewerSpec", "view_jsonl"]
