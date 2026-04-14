# Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
vaner init
vaner prepare
vaner query "explain the auth flow"
vaner inspect --last
vaner run-eval
# optional long-running background daemon
vaner daemon start --no-once
```

`vaner daemon start --no-once` runs a background process and writes its real PID to `.vaner/runtime/daemon.pid`.
Use `vaner daemon status` and `vaner daemon stop` to manage it.
