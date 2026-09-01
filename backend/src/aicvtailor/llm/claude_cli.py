"""Claude CLI adapter.

Shells out to the `claude` binary already installed on this machine. It exists
so a single tailoring run can be compared against the NIM path, which is why
the call signature is identical to every other provider.

Deliberately not done here: reusing a session token, scraping claude.ai, or
authenticating to the Anthropic API from application code. This runs the user's
own CLI as the user, or it reports itself unavailable.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import get_settings
from .base import (
    Availability,
    Completion,
    LLMError,
    ProviderUnavailable,
    Role,
    SchemaValidationError,
    Usage,
    extract_json,
    schema_instructions,
)
from .runlog import RunLog

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 180


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(self, *, runlog: RunLog | None = None, runner=None) -> None:
        self._settings = get_settings()
        self._runlog = runlog
        self._runner = runner or self._run_subprocess

    # -- plumbing ----------------------------------------------------------
    def _binary(self) -> str | None:
        return shutil.which(self._settings.claude_cli_path)

    def availability(self) -> Availability:
        if not self._settings.enable_claude_cli:
            return Availability(
                ok=False,
                detail="Disabled (ENABLE_CLAUDE_CLI=false).",
                fallback="Set ENABLE_CLAUDE_CLI=true to A/B a run against the local CLI.",
            )
        binary = self._binary()
        if not binary:
            return Availability(
                ok=False,
                detail=f"'{self._settings.claude_cli_path}' is not on PATH.",
                fallback="Other providers are unaffected.",
            )
        return Availability(ok=True, detail=f"Found at {binary}.")

    def model_for(self, role: Role) -> str:
        return "claude-cli"

    def _run_subprocess(self, prompt: str) -> str:
        binary = self._binary()
        if binary is None:
            raise ProviderUnavailable(
                f"'{self._settings.claude_cli_path}' is not on PATH"
            )
        result = subprocess.run(
            [binary, "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            raise LLMError(
                f"claude CLI exited {result.returncode}: {result.stderr.strip()[:400]}"
            )
        return result.stdout

    @staticmethod
    def _payload_text(stdout: str) -> str:
        """Pull the assistant text out of the CLI's JSON envelope.

        The envelope shape has changed across CLI versions, so this checks the
        known keys and falls back to the raw output rather than hard-failing on
        a format it has not seen.
        """
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout

        if isinstance(envelope, dict):
            for key in ("result", "text", "completion", "content"):
                value = envelope.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, list):  # content blocks
                    parts = [
                        block.get("text", "")
                        for block in value
                        if isinstance(block, dict)
                    ]
                    if any(parts):
                        return "".join(parts)
        return stdout

    # -- public API --------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        *,
        role: Role = Role.EXTRACTOR,
        stage: str = "llm",
    ) -> dict[str, Any] | str:
        result = self.complete_verbose(system, user, schema, role=role, stage=stage)
        if schema is None:
            return result.text
        assert result.parsed is not None
        return result.parsed

    def complete_verbose(
        self,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        *,
        role: Role = Role.EXTRACTOR,
        stage: str = "llm",
    ) -> Completion:
        if not self._settings.enable_claude_cli:
            raise ProviderUnavailable("claude CLI adapter is disabled")

        effective_system = (
            system if schema is None else f"{system}\n\n{schema_instructions(schema)}"
        )
        prompt = f"{effective_system}\n\n---\n\n{user}"

        started = time.perf_counter()
        attempts = 0
        error: str | None = None
        text = ""
        parsed: dict[str, Any] | None = None

        try:
            for repair in range(2):
                attempts += 1
                text = self._payload_text(self._runner(prompt))

                if schema is None:
                    break
                try:
                    parsed = schema.model_validate_json(extract_json(text)).model_dump()
                    break
                except (ValidationError, ValueError) as exc:
                    if repair == 1:
                        raise SchemaValidationError(
                            f"schema validation failed after one repair attempt: {exc}",
                            raw=text,
                        ) from exc
                    prompt = (
                        f"{prompt}\n\nYour previous response did not validate:\n{text}\n\n"
                        f"Error:\n{exc}\n\nReturn only the corrected JSON object."
                    )
        except LLMError as exc:
            error = str(exc)
            raise
        finally:
            if self._runlog is not None:
                self._runlog.call(
                    stage=stage,
                    provider=self.name,
                    model="claude-cli",
                    role=role.value,
                    system=effective_system,
                    user=user,
                    response=text,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=error,
                )

        return Completion(
            text=text,
            model="claude-cli",
            provider=self.name,
            role=role,
            usage=Usage(),
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            parsed=parsed,
        )
