Argus for macOS
===============

This is an unsigned build (not notarized) -- macOS Gatekeeper will block it
on the first open. That's expected, not a sign anything's wrong.

To run it the first time:

  1. Double-click Argus.app -- Gatekeeper refuses with "can't be opened".
  2. Right-click (or Control-click) Argus.app -> Open.
  3. Click "Open" in the dialog that appears.

After that it opens normally like any other app.

Argus appears in the menu bar. Click the icon to start/stop the server,
open it in a browser, or quit.

Data (database, face crops, downloaded models) is stored in:
  ~/Library/Application Support/Argus/

Models are downloaded automatically on first detection.
