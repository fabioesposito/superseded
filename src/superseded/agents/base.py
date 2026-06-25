from __future__ import annotations

import shutil
from abc import ABC, abstractmethod


class Agent(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def build_command(self, prompt: str | None = None) -> list[str]: ...

    @abstractmethod
    def parse_output(self, raw: str, pass_name: str) -> list[dict]: ...

    @property
    def supports_stdin(self) -> bool:
        """Whether the agent CLI supports receiving the prompt via stdin."""
        return True

    def is_available(self) -> bool:
        binary = self.build_command()[0]
        return shutil.which(binary) is not None
