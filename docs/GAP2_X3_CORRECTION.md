# GAP2 X3 scope correction

GAP2 X3 (`docs/GAP2_TODO.md`, G2R-003/G2AT-003) correctly moved the root-phase Bash startup behind `entrypoint-wrapper.py`, so Bash no longer imported hostile shell state before the sanitizer could run. Its historical completion write-up nevertheless states a broader invariant than that implementation provided.

The wrapper itself is Python. Before GAP3, the real Docker `ENTRYPOINT` launched it as `/usr/bin/python3 /usr/local/bin/mcp-entrypoint-wrapper.py`. Python startup therefore still honored interpreter startup state such as `PYTHONPATH`/`sitecustomize` before the wrapper's sanitization code executed. X3 closed the Bash-startup mechanism but did not isolate the privileged interpreter that ran the sanitizer.

GAP3 Y1 / G3R-001 / G3AT-001 closes that residual mechanism. The real image starts the wrapper with an absolute interpreter and isolated startup (`/usr/bin/python3 -I ...`), while retaining the Bash-state sanitization inside the wrapper. G3AT-001 exercises a hostile repository `sitecustomize.py`/`PYTHONPATH` through the real image entrypoint and also proves `container.sudo: false` still reaches the workspace command as the configured non-root uid.

Accordingly, X3's historical evidence remains valid for the Bash-state control it implemented, but the stronger statement that the privileged entrypoint inherited no interpreter state is valid only after GAP3 Y1. Python isolated startup is load-bearing for that stronger invariant; removing `-I` reopens G3AT-001.

This file is a correction to X3's completed historical claim, not a new GAP2 requirement or a rewrite of its original evidence.
