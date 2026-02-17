try:
    from importlib.metadata import version
    __version__ = version('mojokernel')
except Exception:
    __version__ = "0.0.0.0"
