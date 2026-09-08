class FDTError(ValueError):
    """A structured, user-correctable engine error."""
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}

    def as_dict(self) -> dict:
        return {"status": "error", "error": {"code": self.code, "message": str(self), "details": self.details}}
