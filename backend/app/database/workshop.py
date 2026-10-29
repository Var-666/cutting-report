"""Compatibility facade; new code uses the focused repositories directly."""
from app.database.identity_security import AuthenticationError, hash_password, verify_password
from app.database.workshop_errors import (
    ConfirmationConflictError, ConfirmationNotFoundError, ConfirmationPermissionError,
    DailyReportSourceMismatchError, DailyReportScopeMismatchError,
    DailyReportRecordMismatchError, DailyReportExistsError,
)
from app.database.users import UserRepository
from app.database.sessions import SessionRepository
from app.database.workshop_reports import WorkshopReportRepository
from app.database.confirmations import ConfirmationRepository


class WorkshopRepository(WorkshopReportRepository, ConfirmationRepository, UserRepository, SessionRepository):
    """Preserve existing callers during the repository split; no business logic here."""
    def initialize(self) -> None:
        from app.database.migrations import apply_migrations
        apply_migrations(self.database, names=("002_workshop",))
