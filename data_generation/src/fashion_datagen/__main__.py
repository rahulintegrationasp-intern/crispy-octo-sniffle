"""Allow ``python -m fashion_datagen``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
