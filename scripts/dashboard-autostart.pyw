"""Console-free entry point for the current user's sign-in task."""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    # A background start should not open a browser on every login/recovery.
    os.environ["RELAY_WEB_OPEN_BROWSER"] = "false"
    # pythonw has no console streams. Supply valid handles for logging and SDKs;
    # the relay's existing rotating file handler remains the diagnostic output.
    original_streams = {name: getattr(sys, name) for name in ("stdin", "stdout", "stderr")}
    with ExitStack() as stack:
        try:
            for name, original in original_streams.items():
                if original is None:
                    mode = "r" if name == "stdin" else "w"
                    setattr(sys, name, stack.enter_context(open(os.devnull, mode, encoding="utf-8")))
            from kindroid_relay.cli import main as relay_main

            return relay_main(["serve"])
        finally:
            for name, original in original_streams.items():
                setattr(sys, name, original)


if __name__ == "__main__":
    raise SystemExit(main())
