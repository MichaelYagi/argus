"""Entry point for the Linux CLI build -- starts the Argus server directly.
No tray icon; pystray has no reliable single tray protocol across Linux
desktop environments. Run the binary from a terminal or a systemd unit.
"""
from app.__main__ import main

if __name__ == "__main__":
    main()
