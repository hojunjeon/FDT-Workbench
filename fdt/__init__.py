"""KeyFin FDT: numeric-only, network-free financial scenario engine."""
__version__ = "0.1.0"
from .model import Twin
from .engine import Engine
from .errors import FDTError
__all__ = ["Twin", "Engine", "FDTError"]
