"""Cooperative pipeline handoffs stop a provider turn without consuming a user approval."""


class StageHandoff(Exception):
    """End this provider turn so the supervisor can checkpoint and schedule another stage."""
