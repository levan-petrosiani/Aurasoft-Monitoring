class ConfigurationError(Exception):
    """
    A problem with the tool's own input (profile, scenario, certificate, variable) rather than with the bank's API.
    Scenarios that hit it end with outcome Error, never Failed.
    """


class OperationCancelled(Exception):
    """The run was cancelled (Ctrl+C or the portal's Cancel button)."""
