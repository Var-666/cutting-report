"""Domain failures shared by workshop services and repositories."""
class ConfirmationConflictError(ValueError):
    pass

class ConfirmationNotFoundError(ValueError):
    pass

class ConfirmationPermissionError(ValueError):
    pass

class DailyReportSourceMismatchError(ValueError):
    pass

class DailyReportScopeMismatchError(ValueError):
    pass

class DailyReportRecordMismatchError(ValueError):
    pass

class DailyReportExistsError(ValueError):
    pass
