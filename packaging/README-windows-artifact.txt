Argus for Windows
=================

This is an unsigned build (no paid Windows code-signing certificate) --
Windows will warn about it the first time you run it. That's expected,
not a sign anything's wrong.

To run it:

  1. Double-click "Argus.exe".
  2. Windows shows "Windows protected your PC" (SmartScreen).
  3. Click "More info".
  4. Click "Run anyway".

After that first time it opens normally. Note: this approval is tied to
this exact .exe -- a future version will trigger the same warning once.

Argus appears in the system tray (bottom-right, overflow area). Right-click
the icon to start/stop the server, open it in a browser, or quit.

Data (database, face crops, downloaded models) is stored in:
  %APPDATA%\Argus\

Models are downloaded automatically on first detection.
