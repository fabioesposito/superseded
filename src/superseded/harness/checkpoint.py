from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    issue_id: str
    stage: str
    timestamp: str
    completed_tasks: list[str] = field(default_factory=list)
    current_task: str = ""
    files_changed: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class CheckpointManager:
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)

    def _checkpoint_dir(self, issue_id: str) -> Path:
        return self.base_path / ".superseded" / "checkpoints" / issue_id

    def _checkpoint_path(self, issue_id: str, stage: str) -> Path:
        return self._checkpoint_dir(issue_id) / f"{stage}.json"

    def save(self, checkpoint: Checkpoint) -> None:
        path = self._checkpoint_path(checkpoint.issue_id, checkpoint.stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(checkpoint)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Saved checkpoint for %s/%s", checkpoint.issue_id, checkpoint.stage)

    def load(self, issue_id: str, stage: str) -> Checkpoint | None:
        path = self._checkpoint_path(issue_id, stage)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint(**data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Corrupt checkpoint %s: %s", path, e)
            return None

    def clear(self, issue_id: str, stage: str) -> None:
        path = self._checkpoint_path(issue_id, stage)
        if path.exists():
            path.unlink()
            logger.debug("Cleared checkpoint for %s/%s", issue_id, stage)

    def clear_issue(self, issue_id: str) -> None:
        checkpoint_dir = self._checkpoint_dir(issue_id)
        if checkpoint_dir.exists():
            for f in checkpoint_dir.glob("*.json"):
                f.unlink()
            checkpoint_dir.rmdir()
            logger.debug("Cleared all checkpoints for %s", issue_id)

    def has_checkpoint(self, issue_id: str, stage: str) -> bool:
        return self._checkpoint_path(issue_id, stage).exists()

    def validate_preconditions(
        self, issue_id: str, stage: str, expected_files: list[str] | None = None
    ) -> bool:
        """Validate that checkpoint preconditions still hold.

        Returns True if checkpoint is valid, False if stale.
        """
        checkpoint = self.load(issue_id, stage)
        if checkpoint is None:
            return False

        if expected_files:
            for f in expected_files:
                if not Path(f).exists():
                    logger.warning(
                        "Checkpoint precondition failed: %s no longer exists. Discarding checkpoint.",
                        f,
                    )
                    self.clear(issue_id, stage)
                    return False

        return True
