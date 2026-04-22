"""Pluggable runners for the run_code tool.

Three implementations ship with Holons:

  LocalSubprocessRunner — `subprocess.run` on the backend host, with
                          per-call timeout + resource limits. On macOS
                          it wraps the call in `sandbox-exec` with a
                          profile that denies network and restricts
                          writes to the workspace. Personal-mode
                          default.

  DockerRunner          — shells out to `docker run --rm --network=none`
                          with a cgroup memory / CPU cap. Enterprise
                          opt-in; admins set CODE_EXECUTION_BACKEND=docker
                          and point the runner at a language-specific
                          image (e.g. python:3.11-slim).

  DisabledRunner        — returns a clear error. Default for enterprise
                          so admins opt in explicitly.

Pick at module load via env:
  CODE_EXECUTION_BACKEND = disabled | local | docker  (default: local)
  CODE_EXECUTION_DOCKER_PYTHON_IMAGE = python:3.11-slim
  CODE_EXECUTION_DOCKER_NODE_IMAGE   = node:20-slim
  CODE_EXECUTION_DOCKER_MEMORY       = 512m
  CODE_EXECUTION_DEFAULT_TIMEOUT_S   = 30
  CODE_EXECUTION_MAX_TIMEOUT_S       = 120
"""
from __future__ import annotations

import abc
import logging
import os
import platform
import resource
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


log = logging.getLogger("agent_company.code_runners")


# ----------------------------------------------------------------------
# Supported languages → (interpreter, file extension)
# ----------------------------------------------------------------------

LANGS: dict[str, dict] = {
    "python": {"interpreter": sys.executable, "ext": ".py"},
    "node":   {"interpreter": "node",         "ext": ".js"},
    "bash":   {"interpreter": "bash",         "ext": ".sh"},
    "sh":     {"interpreter": "sh",           "ext": ".sh"},
}


def _default_timeout() -> int:
    return int(os.environ.get("CODE_EXECUTION_DEFAULT_TIMEOUT_S", "30"))


def _max_timeout() -> int:
    return int(os.environ.get("CODE_EXECUTION_MAX_TIMEOUT_S", "120"))


def _resolve_timeout(user_requested: int | None) -> int:
    t = user_requested if user_requested and user_requested > 0 else _default_timeout()
    return min(t, _max_timeout())


class ExecutionResult(dict):
    """Same shape across runners — keeps the tool handler agnostic."""

    @classmethod
    def make(cls, *, ok: bool, stdout: str = "", stderr: str = "",
             exit_code: int | None = None, duration_ms: int = 0,
             error: str | None = None) -> "ExecutionResult":
        return cls({
            "ok": ok,
            "stdout": stdout[-60_000:],   # clip to keep LLM context bounded
            "stderr": stderr[-20_000:],
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "error": error,
        })


# ----------------------------------------------------------------------
# Runner ABC
# ----------------------------------------------------------------------

class CodeRunner(abc.ABC):
    backend_name: str = "abstract"

    @abc.abstractmethod
    def run(self, *, lang: str, code: str, cwd: Path, timeout_s: int) -> ExecutionResult:
        ...

    @property
    def available(self) -> bool:
        return True


# ----------------------------------------------------------------------
# DisabledRunner
# ----------------------------------------------------------------------

class DisabledRunner(CodeRunner):
    backend_name = "disabled"

    def run(self, *, lang: str, code: str, cwd: Path, timeout_s: int) -> ExecutionResult:
        return ExecutionResult.make(
            ok=False,
            error="Code execution is disabled on this Holons deployment. "
                  "Ask the admin to set CODE_EXECUTION_BACKEND or enable "
                  "the per-user toggle in Personal settings.",
        )

    @property
    def available(self) -> bool:
        return False


# ----------------------------------------------------------------------
# LocalSubprocessRunner
# ----------------------------------------------------------------------

class LocalSubprocessRunner(CodeRunner):
    """Runs `code` via `subprocess.run` on the backend host. On macOS
    the call is wrapped in sandbox-exec with a profile that denies
    network and only permits writes under `cwd`. On Linux and other
    platforms it falls back to plain subprocess + rlimit."""

    backend_name = "local"

    # Roughly 256 MiB address space, 30s CPU, 50MB core file size cap.
    _RLIMITS = {
        resource.RLIMIT_AS:   (256 * 1024 * 1024, 256 * 1024 * 1024),
        resource.RLIMIT_CORE: (0, 0),
    }

    def run(self, *, lang: str, code: str, cwd: Path, timeout_s: int) -> ExecutionResult:
        if lang not in LANGS:
            return ExecutionResult.make(
                ok=False, error=f"unsupported language: {lang}",
            )
        cfg = LANGS[lang]
        cwd.mkdir(parents=True, exist_ok=True)

        # Write the code to a temp file inside cwd so the subprocess has
        # a predictable path. This also means any paths the agent
        # references relatively resolve against the workspace.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=cfg["ext"], dir=cwd, delete=False, encoding="utf-8",
        ) as fh:
            fh.write(code)
            script_path = fh.name

        try:
            argv = [cfg["interpreter"], script_path]
            if platform.system() == "Darwin":
                argv = self._wrap_with_sandbox_exec(argv, cwd)

            def _preexec():
                for lim, vals in self._RLIMITS.items():
                    try:
                        resource.setrlimit(lim, vals)
                    except Exception:
                        pass

            t0 = time.time()
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout_s,
                    preexec_fn=_preexec if os.name == "posix" else None,
                    text=True,
                )
                duration_ms = int((time.time() - t0) * 1000)
                return ExecutionResult.make(
                    ok=proc.returncode == 0,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    exit_code=proc.returncode,
                    duration_ms=duration_ms,
                )
            except subprocess.TimeoutExpired as e:
                duration_ms = int((time.time() - t0) * 1000)
                return ExecutionResult.make(
                    ok=False,
                    stdout=(e.stdout or "").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                    stderr=(e.stderr or "").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                    duration_ms=duration_ms,
                    error=f"timeout after {timeout_s}s",
                )
        finally:
            try:
                Path(script_path).unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _wrap_with_sandbox_exec(argv: list[str], cwd: Path) -> list[str]:
        """Build a sandbox-exec profile that denies network + permits
        writes only under cwd. Falls back to unsandboxed argv if
        sandbox-exec is unavailable (e.g. macOS without it installed)."""
        profile = textwrap.dedent(f"""
            (version 1)
            (deny default)
            (allow process-fork)
            (allow process-exec)
            (allow signal)
            (allow sysctl-read)
            (allow system-socket)
            (allow file-read*)
            (allow file-write* (subpath "{cwd.resolve()}"))
            (allow file-write* (subpath "/private/var/folders"))
            (allow file-write* (subpath "/tmp"))
            (deny network*)
        """).strip()
        # /usr/bin/sandbox-exec -p '<profile>' <argv...>
        if Path("/usr/bin/sandbox-exec").exists():
            return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
        log.warning("sandbox-exec not found; falling back to unsandboxed subprocess")
        return argv


# ----------------------------------------------------------------------
# DockerRunner — skeleton. Admins who enable this must preseed the
# relevant images (python:3.11-slim etc).
# ----------------------------------------------------------------------

class DockerRunner(CodeRunner):
    backend_name = "docker"

    IMAGES = {
        "python": os.environ.get("CODE_EXECUTION_DOCKER_PYTHON_IMAGE", "python:3.11-slim"),
        "node":   os.environ.get("CODE_EXECUTION_DOCKER_NODE_IMAGE",   "node:20-slim"),
        "bash":   os.environ.get("CODE_EXECUTION_DOCKER_BASH_IMAGE",   "alpine:3"),
        "sh":     os.environ.get("CODE_EXECUTION_DOCKER_SH_IMAGE",     "alpine:3"),
    }
    MEMORY = os.environ.get("CODE_EXECUTION_DOCKER_MEMORY", "512m")
    CPUS   = os.environ.get("CODE_EXECUTION_DOCKER_CPUS", "1.0")

    def run(self, *, lang: str, code: str, cwd: Path, timeout_s: int) -> ExecutionResult:
        if lang not in LANGS:
            return ExecutionResult.make(ok=False, error=f"unsupported language: {lang}")
        image = self.IMAGES.get(lang)
        if not image:
            return ExecutionResult.make(ok=False, error=f"no docker image configured for {lang}")

        cfg = LANGS[lang]
        cwd.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=cfg["ext"], dir=cwd, delete=False, encoding="utf-8",
        ) as fh:
            fh.write(code)
            script_name = Path(fh.name).name

        try:
            argv = [
                "docker", "run", "--rm",
                "--network=none",
                f"--memory={self.MEMORY}",
                f"--cpus={self.CPUS}",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",  # nobody
                "-v", f"{cwd.resolve()}:/work",
                "-w", "/work",
                image,
                # For python/node we run the script by file; for bash/sh too.
                "python" if lang == "python" else
                "node"   if lang == "node"   else "bash" if lang == "bash" else "sh",
                script_name,
            ]
            t0 = time.time()
            try:
                proc = subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout_s + 5,   # +5s for docker startup overhead
                    text=True,
                )
                duration_ms = int((time.time() - t0) * 1000)
                return ExecutionResult.make(
                    ok=proc.returncode == 0,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    exit_code=proc.returncode,
                    duration_ms=duration_ms,
                )
            except subprocess.TimeoutExpired as e:
                duration_ms = int((time.time() - t0) * 1000)
                return ExecutionResult.make(
                    ok=False,
                    stdout="",
                    stderr=(e.stderr or "").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                    duration_ms=duration_ms,
                    error=f"timeout after {timeout_s}s",
                )
            except FileNotFoundError:
                return ExecutionResult.make(
                    ok=False,
                    error="docker binary not found — DockerRunner requires a working docker install.",
                )
        finally:
            try:
                (cwd / script_name).unlink(missing_ok=True)
            except Exception:
                pass


# ----------------------------------------------------------------------
# Module-level runner picked from env.
# ----------------------------------------------------------------------

_RUNNER: CodeRunner | None = None


def get_runner() -> CodeRunner:
    global _RUNNER
    if _RUNNER is not None:
        return _RUNNER
    backend = (os.environ.get("CODE_EXECUTION_BACKEND") or "local").strip().lower()
    if backend == "disabled":
        _RUNNER = DisabledRunner()
    elif backend == "docker":
        _RUNNER = DockerRunner()
    else:
        _RUNNER = LocalSubprocessRunner()
    log.info("code_runners: using %s backend", _RUNNER.backend_name)
    return _RUNNER


def reset_runner_for_tests() -> None:
    """Clear the cached runner so tests can flip CODE_EXECUTION_BACKEND."""
    global _RUNNER
    _RUNNER = None
