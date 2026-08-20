"""The frozen bundle's entry point.

A module rather than a console-script shim because PyInstaller analyses a FILE, and pointing it
at the installed `keel` script would freeze whatever that script happened to be in the build
environment rather than this repository's CLI.
"""

from keel.cli import cli

if __name__ == "__main__":
    cli()
