"""将 AI 恢复过程写入 UTF-8 JSON Lines 审计日志。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


class AITraceLogger:
    def __init__(self, path: str | Path = "reports/logs/ai_recovery.jsonl") -> None:
        """保存日志文件位置；每条事件独立追加，便于边运行边查看。"""
        self.path = Path(path)

    def record(self, *, run_id: str, step_name: str, event: str, **details: Any) -> None:
        """以 UTF-8 JSON Lines 追加事件；日志错误不改变设备操作结果。"""
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "step": step_name,
            "event": event,
            **details,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not write AI trace log (%s)", type(exc).__name__)
