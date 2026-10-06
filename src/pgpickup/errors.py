"""Errors raised by config parsing and county lookups."""


class ConfigError(ValueError):
    """The local config or holiday file is not usable."""


class LookupError(RuntimeError):
    """A county collection-day lookup could not be completed."""
