#!/bin/sh
# Re-assert the hermes-lcm #588 mitigation on the runtime volume, then hand off
# to the stock dispatcher. `set -e` means a non-zero exit from the patcher
# aborts the boot rather than starting a gateway that will corrupt its DB.
# See docker/lcm-588-mitigation.py for the rationale.
set -e
/opt/hermes/.venv/bin/python /opt/hermes/docker/lcm-588-mitigation.py
exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"
