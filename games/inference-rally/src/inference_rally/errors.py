"""Project-specific exceptions, so callers can catch exactly what they expect."""


class InferenceRallyError(Exception):
    """Base class for every error raised by Inference Rally."""


class ConfigError(InferenceRallyError):
    """The racers / race configuration is missing or invalid."""


class InvalidMoveError(InferenceRallyError):
    """A model reply could not be parsed into a legal move."""


class DeciderError(InferenceRallyError):
    """A decider (model backend) failed to produce a reply."""


class DecisionTimeoutError(DeciderError):
    """The decider (e.g. the human player) did not answer in time."""
