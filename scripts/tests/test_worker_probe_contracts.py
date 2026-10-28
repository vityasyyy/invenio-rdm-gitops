"""Contract tests for the worker healthcheck helper and its image packaging.

The helper exists because the probes must prove broker-level responsiveness
without importing the application, and because worker pidbox replies are
msgpack (Invenio sets ``CELERY_TASK_SERIALIZER = "msgpack"``) while kombu's
client mailbox accepts json only by default.

These tests cover the helper's behaviour, the Dockerfile copy, and invariants
of the unchanged manifests. The probe invocation itself is covered by
``test_worker_probe_wiring.py`` in the separate Deployment switch change.

The transport is modelled with a fake ``celery`` module: it refuses msgpack
unless the client policy includes it, reproducing the observed live failure
without a broker. The real library was checked once in a pod with the
``memory://`` transport and recorded in the findings document; one stdin run of
this exact source later answered in a live pod, while the installed-file and
kubelet exec paths remain rollout gates.
"""

import contextlib
import importlib.util
import io
import os
import socket
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "k8s/apps/invenio/resources/invenio-worker-deployment.yaml"
SCHEDULER = ROOT / "k8s/apps/invenio/resources/invenio-scheduler-deployment.yaml"
APP_CONFIG = ROOT / "k8s/apps/invenio/config/app-config.env"
DOCKERFILE = ROOT / "docker/ugm/Dockerfile"
HEALTHCHECK = ROOT / "docker/ugm/worker_healthcheck.py"

BROKER_ENV = "INVENIO_CELERY_BROKER_URL"
BROKER_URL = "redis://invenio-redis.invenio.svc.cluster.local:6379/0"
NODE = "celery@pod-a"
MSG_PACK = "application/x-msgpack"
YAML = "application/yaml"


def load_healthcheck():
    spec = importlib.util.spec_from_file_location("worker_healthcheck_probe", HEALTHCHECK)
    module = importlib.util.module_from_spec(spec)
    # Keep the build context clean: bytecode would land in docker/ugm/.
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class FakeContentDisallowed(Exception):
    """Stands in for kombu.exceptions.ContentDisallowed."""


def make_fake_celery(replies=None, exc=None, content_type=MSG_PACK):
    """Build a `celery` module whose reply consumer enforces the accept policy."""
    created = []

    class Inspect:
        def __init__(self, app):
            self.app = app

        def ping(self):
            if exc is not None:
                raise exc
            accepted = self.app.conf.accept_content
            if content_type == MSG_PACK and "msgpack" not in accepted:
                raise FakeContentDisallowed("Refusing to deserialize msgpack")
            if content_type == YAML and "yaml" not in accepted:
                raise FakeContentDisallowed("Refusing to deserialize yaml")
            return replies

    class Control:
        def __init__(self, app):
            self.app = app
            self.calls = []

        def inspect(self, timeout=None, destination=None):
            self.calls.append((timeout, destination))
            return Inspect(self.app)

    class Celery:
        def __init__(self, broker=None):
            self.broker = broker
            # Celery's client-side default, and the cause of the live failure.
            self.conf = types.SimpleNamespace(accept_content=["json"])
            self.control = Control(self)
            created.append(self)

    module = types.ModuleType("celery")
    module.Celery = Celery
    return module, created


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.module = load_healthcheck()

    def run_main(self, argv=(), env=None, replies=None, exc=None, content_type=MSG_PACK,
                 hostname="pod-a"):
        module, created = make_fake_celery(replies, exc, content_type)
        stderr = io.StringIO()
        env = {} if env is None else env
        with mock.patch.dict(sys.modules, {"celery": module}), \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(socket, "gethostname", return_value=hostname), \
                contextlib.redirect_stderr(stderr):
            code = self.module.main(list(argv))
        return code, stderr.getvalue(), created

    def test_msgpack_pong_is_accepted_under_the_verified_policy(self):
        code, stderr, created = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies={NODE: {"ok": "pong"}},
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        app = created[0]
        self.assertEqual(app.broker, BROKER_URL)
        self.assertEqual(app.conf.accept_content, ["msgpack", "json"])
        self.assertEqual(app.control.calls, [(5.0, [NODE])])

    def test_json_only_client_refuses_the_msgpack_reply(self):
        # Reproduces the live failure: default accept list, msgpack reply.
        module, _ = make_fake_celery(replies={NODE: {"ok": "pong"}})
        app = module.Celery(broker=BROKER_URL)
        with self.assertRaises(FakeContentDisallowed):
            app.control.inspect(timeout=1).ping()

    def test_error_payload_fails_closed(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies={NODE: {"error": "failed"}},
        )
        self.assertEqual(code, 1)
        self.assertIn("unexpected reply", stderr)
        self.assertNotIn("failed", stderr, "must not print payload contents")

    def test_non_mapping_payload_fails_closed(self):
        for payload in ("pong", ["ok", "pong"], None):
            with self.subTest(payload=payload):
                code, stderr, _ = self.run_main(
                    argv=["5"],
                    env={BROKER_ENV: BROKER_URL},
                    replies={NODE: payload},
                )
                self.assertEqual(code, 1)
                self.assertIn("unexpected reply", stderr)

    def test_malformed_replies_container_fails_closed(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies=[NODE],
        )
        self.assertEqual(code, 1)
        self.assertIn("malformed reply", stderr)

    def test_extra_payload_fields_are_rejected(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies={NODE: {"ok": "pong", "unexpected": "field"}},
        )
        self.assertEqual(code, 1)
        self.assertIn("unexpected reply", stderr)
        self.assertNotIn("field", stderr)

    def test_no_reply_fails_closed(self):
        code, stderr, _ = self.run_main(
            argv=["5"], env={BROKER_ENV: BROKER_URL}, replies={}
        )
        self.assertEqual(code, 1)
        self.assertIn("no reply", stderr)
        self.assertNotIn(BROKER_URL, stderr)

    def test_reply_from_another_node_fails_closed(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies={"celery@pod-b": {"ok": "pong"}},
        )
        self.assertEqual(code, 1)
        self.assertIn("no reply", stderr)

    def test_missing_broker_fails_without_creating_a_client(self):
        code, stderr, created = self.run_main(argv=["5"], env={})
        self.assertEqual(code, 1)
        self.assertIn(BROKER_ENV, stderr)
        self.assertEqual(created, [], "must not fall back to a default broker")

    def test_empty_broker_fails_without_creating_a_client(self):
        code, _, created = self.run_main(argv=["5"], env={BROKER_ENV: "   "})
        self.assertEqual(code, 1)
        self.assertEqual(created, [])

    def test_unsafe_content_is_not_accepted(self):
        self.assertEqual(self.module.ACCEPT_CONTENT, ("msgpack", "json"))
        for unsafe in ("pickle", "yaml", "*", "application/yaml"):
            self.assertNotIn(unsafe, self.module.ACCEPT_CONTENT)

    def test_yaml_reply_is_refused(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            replies={NODE: {"ok": "pong"}},
            content_type=YAML,
        )
        self.assertEqual(code, 1)
        self.assertIn("ContentDisallowed", stderr)

    def test_decode_refusal_fails_closed(self):
        code, stderr, _ = self.run_main(
            argv=["5"],
            env={BROKER_ENV: BROKER_URL},
            exc=FakeContentDisallowed("Refusing to deserialize msgpack"),
        )
        self.assertEqual(code, 1)
        self.assertIn("ContentDisallowed", stderr)
        self.assertNotIn(BROKER_URL, stderr)

    def test_timeout_is_passed_through_and_a_stalled_ping_fails(self):
        code, stderr, created = self.run_main(
            argv=["30"],
            env={BROKER_ENV: BROKER_URL},
            exc=TimeoutError("no reply before timeout"),
        )
        self.assertEqual(code, 1)
        self.assertIn("TimeoutError", stderr)
        self.assertEqual(created[0].control.calls, [(30.0, [NODE])])

    def test_invalid_timeout_fails_closed(self):
        code, stderr, created = self.run_main(argv=["soon"], env={BROKER_ENV: BROKER_URL})
        self.assertEqual(code, 1)
        self.assertIn("timeout", stderr)
        self.assertEqual(created, [])

    def test_nonpositive_timeout_fails_before_creating_a_client(self):
        for value in ("0", "-5", "0.0"):
            with self.subTest(timeout=value):
                code, stderr, created = self.run_main(argv=[value], env={BROKER_ENV: BROKER_URL})
                self.assertEqual(code, 1)
                self.assertIn("timeout", stderr)
                self.assertEqual(created, [])

    def test_nonfinite_timeout_fails_before_creating_a_client(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(timeout=value):
                code, stderr, created = self.run_main(argv=[value], env={BROKER_ENV: BROKER_URL})
                self.assertEqual(code, 1)
                self.assertIn("timeout", stderr)
                self.assertEqual(created, [])


class ImagePackagingTests(unittest.TestCase):
    def test_helper_is_copied_into_the_image(self):
        self.assertTrue(HEALTHCHECK.is_file())
        self.assertIn(
            "COPY ./docker/ugm/worker_healthcheck.py ${INVENIO_INSTANCE_PATH}",
            DOCKERFILE.read_text(),
        )


class ProbeInvariantTests(unittest.TestCase):
    """Invariants of the unchanged manifests that this change must leave intact."""

    def setUp(self):
        deployment = yaml.safe_load(WORKER.read_text())
        containers = deployment["spec"]["template"]["spec"]["containers"]
        self.container = next(c for c in containers if c["name"] == "worker")

    def test_broker_env_exists_in_the_config_map(self):
        declared = set()
        for line in APP_CONFIG.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                declared.add(line.split("=", 1)[0].strip())
        self.assertIn(BROKER_ENV, declared)

    def test_liveness_and_readiness_timeouts_stay_under_their_periods(self):
        # Bounds a single probe's own back-to-back runs. It says nothing about
        # liveness and readiness overlapping each other, which they can still do.
        for name in ("livenessProbe", "readinessProbe"):
            probe = self.container[name]
            with self.subTest(probe=name):
                self.assertLessEqual(probe["timeoutSeconds"], probe["periodSeconds"])
                self.assertEqual(probe["failureThreshold"], 3)

    def test_scheduler_liveness_does_not_import_the_application(self):
        deployment = yaml.safe_load(SCHEDULER.read_text())
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        command = " ".join(container["livenessProbe"]["exec"]["command"])
        self.assertNotIn("celery -A", command)
        self.assertNotIn("inspect ping", command)


if __name__ == "__main__":
    unittest.main()
