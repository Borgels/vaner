# Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
vaner init
vaner daemon start
vaner query "explain the auth flow"
vaner inspect --last
```

Note: `vaner daemon start` currently writes a status marker at `.vaner/runtime/daemon.pid`.
This is a lightweight v1 status file, not an operating-system process PID.
