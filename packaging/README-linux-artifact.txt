Argus for Linux (x86_64)
========================

This is a standalone CLI binary -- no Python installation required.
No tray icon on Linux; run it from a terminal or wrap it in a systemd unit.

To run:

  chmod +x argus
  ./argus

By default it binds to 127.0.0.1:8100. Options:

  ./argus --host 0.0.0.0   # expose on your LAN
  ./argus --port 9000       # use a different port

Then open http://localhost:8100 in a browser.

Note: this binary is built against the glibc version of the build machine
(Ubuntu 24.04 / glibc 2.39). It may not run on significantly older distributions.

Data (database, face crops, downloaded models) is stored relative to the
working directory you launch it from, in data/ and models/.
Models are downloaded automatically on first detection.
