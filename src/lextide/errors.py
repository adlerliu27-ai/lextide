"""Safe user-facing errors. Never include upstream exception text or request URLs."""


class StudioError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class InputError(StudioError):
    def __init__(self, message: str):
        super().__init__("INVALID_INPUT", message, 2)


class ValidationError(StudioError):
    def __init__(self, message: str):
        super().__init__("VALIDATION_FAILED", message, 5)
