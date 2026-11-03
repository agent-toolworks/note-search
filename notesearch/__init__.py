"""note-search: cross-project search over folders of Markdown notes."""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    with open(os.path.join(_ROOT, "VERSION")) as _fh:
        __version__ = _fh.read().strip()
except OSError:  # pragma: no cover
    __version__ = "unknown"
