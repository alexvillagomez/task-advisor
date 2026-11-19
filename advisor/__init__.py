"""Fast, local task advice from the repository's graded trials."""
__all__ = ["advise"]


def __getattr__(name):
    if name == "advise":
        from .core import advise
        return advise
    raise AttributeError(name)
