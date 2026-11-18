"""提供稳定机器可读代码的领域错误。"""
class MeridianError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self):
        return {"error": self.code, "message": str(self), "details": self.details}


def require(condition: bool, code: str, message: str, **details):
    if not condition:
        raise MeridianError(code, message, details=details)

