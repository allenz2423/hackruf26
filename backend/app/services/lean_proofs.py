"""Run bounded Lean 4 proof checks for model tool calls."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path

from app.api.schemas import LeanProofResponse
from app.config import Settings

MAX_PROPOSITION_CHARS = 4_000
MAX_PROOF_CHARS = 8_000
MAX_DIAGNOSTIC_BYTES = 16_000
MAX_DIAGNOSTIC_CHARS = 4_000
LEAN_HEARTBEAT_LIMIT = 300_000
LEAN_PRELUDE = "import Mathlib.Tactic\n"
LEAN_CPU_LIMIT_SECONDS = 15
LEAN_FILE_LIMIT_BYTES = 2 * 1024 * 1024
OUTPUT_LIMIT_MARKER = "\nLean diagnostics were truncated because the output limit was reached."

_FORBIDDEN_INPUT = re.compile(
    r"(?i)(?:\b(?:run_tac|run_cmd|run_term_elab|elab|macro|syntax|"
    r"initialize|builtin_initialize|sorry|admit|axiom|unsafe|IO|System)\b|"
    r"#|--|/\-|\-/)"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class LeanRuntimeUnavailable(RuntimeError):
    """The Lean executable or its prepared Mathlib project is unavailable."""


class LeanProofBusy(RuntimeError):
    """All bounded Lean proof-check slots are currently occupied."""


class LeanProofInputError(ValueError):
    """A request contains unsupported or unsafe Lean syntax."""


class LeanProofService:
    """Check one proposition/proof pair using the local Lean + Mathlib project."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        lake_setting = settings.lean_lake_executable.strip()
        self._lake = shutil.which(lake_setting)
        if self._lake is None and lake_setting == "lake":
            elan_lake = Path.home() / ".elan" / "bin" / "lake"
            if elan_lake.is_file():
                self._lake = str(elan_lake)
        self._project_dir = settings.lean_project_dir_path.resolve()
        self._slots = asyncio.Semaphore(2)

    async def ensure_ready(self) -> None:
        """Fail startup when the configured Lean runtime cannot check Mathlib proofs."""
        if self._lake is None or not self._project_dir.is_dir():
            raise LeanRuntimeUnavailable
        status, _ = await self._run_source(
            f"{LEAN_PRELUDE}example : True := by trivial\n",
            timeout_seconds=max(30.0, self._settings.lean_timeout_seconds),
        )
        if status != "verified":
            raise LeanRuntimeUnavailable

    async def verify(self, proposition: str, proof: str) -> LeanProofResponse:
        """Check a bounded Lean proposition and its tactic proof."""
        source = self._proof_source(proposition, proof)
        status, diagnostics = await self._run_source(
            source,
            timeout_seconds=self._settings.lean_timeout_seconds,
        )
        return LeanProofResponse(status=status, diagnostics=diagnostics)

    @staticmethod
    def _proof_source(proposition: str, proof: str) -> str:
        proposition = proposition.strip()
        proof = proof.strip()
        if (
            not proposition
            or len(proposition) > MAX_PROPOSITION_CHARS
            or any(character in proposition for character in "\r\n\v\f\x85\u2028\u2029")
            or not proof
            or len(proof) > MAX_PROOF_CHARS
            or _CONTROL_CHARACTERS.search(proposition)
            or _CONTROL_CHARACTERS.search(proof)
            or "\t" in proposition
            or "\t" in proof
            or _FORBIDDEN_INPUT.search(proposition)
            or _FORBIDDEN_INPUT.search(proof)
        ):
            raise LeanProofInputError

        proof_body = "\n".join(f"  {line}" if line else "  " for line in proof.splitlines())
        return (
            LEAN_PRELUDE + f"set_option maxHeartbeats {LEAN_HEARTBEAT_LIMIT}\n"
            f"theorem screenTutorClaim : {proposition} := by\n"
            f"{proof_body}\n"
        )

    async def _run_source(
        self,
        source: str,
        *,
        timeout_seconds: float,
    ) -> tuple[str, str]:
        if self._lake is None or not self._project_dir.is_dir():
            raise LeanRuntimeUnavailable

        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=1.0)
        except TimeoutError:
            raise LeanProofBusy from None

        try:
            return await self._run_in_slot(source, timeout_seconds=timeout_seconds)
        finally:
            self._slots.release()

    async def _run_in_slot(
        self,
        source: str,
        *,
        timeout_seconds: float,
    ) -> tuple[str, str]:
        with tempfile.TemporaryDirectory(prefix="screen-tutor-lean-") as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "Proof.lean"
            source_path.write_text(source, encoding="utf-8")
            argv = self._command(source_path, timeout_seconds)

            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=self._project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError:
                raise LeanRuntimeUnavailable from None

            assert process.stdout is not None
            output_task = asyncio.create_task(self._read_output(process, process.stdout))
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except TimeoutError:
                self._kill_process_group(process)
                await process.wait()
                await output_task
                return "timed_out", "Lean proof checking exceeded the configured time limit."
            except asyncio.CancelledError:
                self._kill_process_group(process)
                await process.wait()
                await output_task
                raise

            output, output_limited = await output_task
            if output_limited:
                return "rejected", "Lean diagnostics exceeded the configured output limit."
            if process.returncode == 0:
                return "verified", "Lean accepted the formal proposition and proof."
            diagnostics = self._sanitize_diagnostics(output, temp_path, self._project_dir)
            return "rejected", diagnostics or "Lean could not verify the supplied proof."

    def _command(self, source_path: Path, timeout_seconds: float) -> list[str]:
        command = [self._lake, "env", "lean", str(source_path)]
        prlimit = shutil.which("prlimit")
        if prlimit is None:
            return command
        cpu_soft_limit = max(LEAN_CPU_LIMIT_SECONDS, int(timeout_seconds) + 1)
        return [
            prlimit,
            f"--cpu={cpu_soft_limit}:{cpu_soft_limit + 2}",
            f"--fsize={LEAN_FILE_LIMIT_BYTES}",
            "--core=0",
            "--nofile=64",
            *command,
        ]

    async def _read_output(
        self,
        process: asyncio.subprocess.Process,
        stream: asyncio.StreamReader,
    ) -> tuple[bytes, bool]:
        output = bytearray()
        limited = False
        while chunk := await stream.read(4_096):
            remaining = MAX_DIAGNOSTIC_BYTES - len(output)
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                output.extend(OUTPUT_LIMIT_MARKER.encode("utf-8"))
                limited = True
                self._kill_process_group(process)
                break
        return bytes(output), limited

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _sanitize_diagnostics(output: bytes, temp_path: Path, project_dir: Path) -> str:
        text = output.decode("utf-8", errors="replace")
        text = text.replace(str(temp_path), "<proof>")
        text = text.replace(str(project_dir), "<lean-project>")
        text = _ANSI_ESCAPE.sub("", text).strip()
        if len(text) > MAX_DIAGNOSTIC_CHARS:
            text = text[:MAX_DIAGNOSTIC_CHARS].rstrip() + "…"
        return text
