"""Agent service: one LLM agent whose tools are discovered from and invoked through the gateway."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"
