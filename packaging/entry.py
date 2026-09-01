"""The frozen bundle's entry point.

Calls `keel.cli.main`, not `cli` directly, so the frozen bundle gets the same closed-stdout
handling as the installed script (#663 -- the Windows release leg died on `brokers list | head -1`).

A module rather than a console-script shim because PyInstaller analyses a FILE, and pointing it
at the installed `keel` script would freeze whatever that script happened to be in the build
environment rather than this repository's CLI.
"""

from keel.cli import main

if __name__ == "__main__":
    main()
