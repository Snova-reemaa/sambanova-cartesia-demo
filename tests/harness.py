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


async def sign_in(session, server):
    await session.post(f"{server.base}/login",
                       data={"passphrase": PASSPHRASE}, allow_redirects=False)
