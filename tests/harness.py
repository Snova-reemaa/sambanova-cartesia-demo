"""Start a server for a test to talk to, and tear it down again.

Offline by default: TEXT_ONLY stops it reaching Cartesia, and placeholder
credentials are enough because load_catalog already copes with a provider it
cannot reach. A test that genuinely needs the real APIs asks for them with
live=True, and the runner only schedules those when RUN_LIVE_TESTS is set.
"""

import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, "venv", "bin", "python")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

PASSPHRASE = "test-passphrase"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """A server on its own port, so a test never disturbs a running demo."""

    def __init__(self, live=False, **extra):
        self.port = free_port()
        self.live = live
        self.extra = extra
        self.proc = None
        self.log = None

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        env = dict(os.environ)
        env.update({
            "PORT": str(self.port),
            "HOST": "127.0.0.1",
            "DEMO_PASSPHRASE": PASSPHRASE,
            # Memory is per-run, so one test cannot see another's calls.
            "MEMORY_FILE": os.path.join("/tmp", f"voxnova-test-{self.port}.sqlite3"),
        })
        if not self.live:
            env["TEXT_ONLY"] = "1"
            # Placeholders: the catalog load fails and falls back, which is
            # exactly the path a test wants and costs nothing.
            env.setdefault("SAMBANOVA_API_KEY", "offline-test")
            env.setdefault("CARTESIA_API_KEY", "offline-test")
        env.update(self.extra)

        self.log = open(f"/tmp/voxnova-test-{self.port}.log", "w+")
        self.proc = subprocess.Popen(
            [PYTHON, "-u", "server.py"], cwd=ROOT, env=env,
            stdout=self.log, stderr=subprocess.STDOUT)

        deadline = time.time() + 40
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.output()}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.3):
                    return self
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"server never came up:\n{self.output()}")

    def output(self):
        if not self.log:
            return ""
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def tracebacks(self):
        return self.output().count("Traceback")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.log:
            self.log.close()
        return False


# A provider refusing to serve us is not a test result. It looks exactly
# like a failure — empty replies, no audio, nothing matching — and reporting
# it as one sends somebody hunting a bug in the agent. It is also not a
# pass. Every suite that spends money stops on these and says which.
# Both wordings: the provider's own, seen by a script calling the API
# directly, and the server's sanitised version, which is all a test driving
# the websocket ever gets. Missing the second meant the one a live suite
# actually encounters would have sailed through as an ordinary failure.
PAID_API_TROUBLE = (
    ("out of credit", ("402", "ran out of credits", "credit limit",
                       "upgrade your subscription", "balance_units",
                       "payment required", "insufficient",
                       "is out of credit")),
    ("authentication rejected", ("401", "invalid_api_key", "incorrect api key",
                                 "authentication_error", "unauthorized",
                                 "invalid authentication",
                                 "rejected our credentials")),
    ("rate limited", ("429", "rate limit", "too many requests",
                      "concurrency limit", "high demand",
                      "is busy right now")),
)


class ProviderRefused(RuntimeError):
    """A paid API would not serve us. Not a pass, not a fail."""


def paid_api_trouble(text):
    """Which kind of refusal this is, or None."""
    low = str(text or "").lower()
    for label, needles in PAID_API_TROUBLE:
        if any(n in low for n in needles):
            return label
    return None


def stop_if_refused(text, where=""):
    label = paid_api_trouble(text)
    if label:
        raise ProviderRefused(
            f"{label}{' — ' + where if where else ''}: {str(text)[:160]}")


async def sign_in(session, server):
    await session.post(f"{server.base}/login",
                       data={"passphrase": PASSPHRASE}, allow_redirects=False)
