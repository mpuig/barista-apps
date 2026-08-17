"""Mandatory Cloud-absent standalone harness.

Installs a process-wide guard that fails loudly if code tries to reach Barista
Cloud or import a proprietary module during a standalone conformance run. This
is what makes the standalone profile trustworthy: the offline claim is enforced,
not documented.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import Iterable


class StandaloneViolation(RuntimeError):
    """Raised when a standalone run touches Cloud DNS/endpoints or a proprietary import."""


def _host_matches(host: str, suffixes: Iterable[str]) -> bool:
    host = (host or "").lower().strip(".")
    for suffix in suffixes:
        suffix = suffix.lower().strip(".")
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def assert_no_proprietary_modules(modules: Iterable[str]) -> None:
    """Fail if a proprietary module is importable at all in this environment."""
    for name in modules:
        if name in sys.modules or importlib.util.find_spec(name) is not None:
            raise StandaloneViolation(
                f"proprietary module '{name}' is present in a standalone run"
            )


def install_guard(cloud_hosts: Iterable[str], proprietary_modules: Iterable[str]):
    """Install an audit hook that blocks Cloud network access and proprietary
    imports. Returns the hook (kept referenced so it is not collected)."""
    cloud_hosts = tuple(cloud_hosts)
    proprietary_modules = tuple(proprietary_modules)

    def hook(event: str, args: tuple) -> None:
        if event == "socket.getaddrinfo":
            host = args[0]
            if isinstance(host, bytes):
                host = host.decode("ascii", "ignore")
            if isinstance(host, str) and _host_matches(host, cloud_hosts):
                raise StandaloneViolation(f"standalone run resolved Cloud host '{host}'")
        elif event == "socket.connect":
            address = args[1] if len(args) > 1 else None
            if isinstance(address, tuple) and address and isinstance(address[0], str):
                if _host_matches(address[0], cloud_hosts):
                    raise StandaloneViolation(
                        f"standalone run connected to Cloud address '{address[0]}'"
                    )
        elif event == "import":
            module = args[0]
            if isinstance(module, str) and any(
                module == m or module.startswith(m + ".") for m in proprietary_modules
            ):
                raise StandaloneViolation(
                    f"standalone run imported proprietary module '{module}'"
                )

    sys.addaudithook(hook)
    return hook
