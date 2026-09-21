"""Typed failures used to fail closed at pipeline boundaries."""


class ReconciliationError(Exception):
    """Base exception for expected pipeline failures."""


class ConfigurationError(ReconciliationError):
    """Configuration is missing, invalid, or unsafe."""


class SourceIncompleteError(ReconciliationError):
    """The source website could not be proven complete."""


class AuthenticationError(ReconciliationError):
    """CRM authentication failed."""


class RemoteAPIError(ReconciliationError):
    """A remote system returned an unusable response."""


class DataValidationError(ReconciliationError):
    """Source data failed structural validation."""


class SourceError(ReconciliationError):
    """A website or API could not be read reliably."""


class ValidationError(DataValidationError):
    """A completeness or structural invariant failed."""
