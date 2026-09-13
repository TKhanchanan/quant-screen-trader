"""PyInstaller entry point; retain the existing engine CLI without adding behavior."""

from quant_engine.__main__ import main

if __name__ == "__main__":
    main()
