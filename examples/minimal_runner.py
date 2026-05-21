"""Minimal offline Sisypy runner using the public CLI helper.

Run from the Sisypy repository root after `pip install -e .`, or with
`PYTHONPATH=.` during local development:

    PYTHONPATH=. python examples/minimal_runner.py --help
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sisypy import FakeProjectAdapter, cli


def main(argv: list[str] | None = None) -> dict[str, Any]:
    """Run the shared Sisypy CLI with the no-op adapter."""

    return cli(
        FakeProjectAdapter(name="minimal", repo_root=Path.cwd()),
        argv=sys.argv[1:] if argv is None else argv,
    )


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2, default=str))
