"""Broker-level readiness check for the invenio-worker containers.

Pings this pod's own Celery node and exits 0 only on its exact pong payload.
The serializer policy, failure contract and source notes live in
docs/worker-probe-findings.md.
"""

import math
import os
import socket
import sys

BROKER_ENV = "INVENIO_CELERY_BROKER_URL"
DEFAULT_TIMEOUT = 15.0
ACCEPT_CONTENT = ("msgpack", "json")
PONG = {"ok": "pong"}


def _timeout(argv):
    if not argv:
        return DEFAULT_TIMEOUT
    try:
        value = float(argv[0])
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _check(broker, node, timeout):
    """Return None on this pod's pong, else a short reason without the payload."""
    from celery import Celery

    app = Celery(broker=broker)
    # Set before the first app.control access: Control builds its mailbox,
    # and with it the reply consumer's accept list, on construction.
    app.conf.accept_content = list(ACCEPT_CONTENT)
    replies = app.control.inspect(timeout=timeout, destination=[node]).ping()
    if not isinstance(replies, dict):
        return "malformed reply from %s" % node
    if node not in replies:
        return "no reply from %s" % node
    if replies[node] != PONG:
        return "unexpected reply from %s" % node
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    timeout = _timeout(argv)
    if timeout is None:
        print("worker_healthcheck: timeout must be a positive finite number", file=sys.stderr)
        return 1
    broker = (os.environ.get(BROKER_ENV) or "").strip()
    if not broker:
        print("worker_healthcheck: %s is unset or empty" % BROKER_ENV, file=sys.stderr)
        return 1
    node = "celery@" + socket.gethostname()
    try:
        failure = _check(broker, node, timeout)
    except Exception as exc:  # any transport or decode failure must fail closed
        print("worker_healthcheck: ping failed: %s" % type(exc).__name__, file=sys.stderr)
        return 1
    if failure:
        print("worker_healthcheck: %s" % failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
