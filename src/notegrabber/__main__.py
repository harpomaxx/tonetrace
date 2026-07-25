"""Allow `python -m notegrabber` to run the CLI."""

from .cli import main

raise SystemExit(main())
