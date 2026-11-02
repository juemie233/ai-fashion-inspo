"""按天切分的日志文件 handler（`analyze-YYYY-MM-DD.log`）。

为什么不用 `TimedRotatingFileHandler`：它生成的是 `analyze.log.2026-08-22`，
而本项目希望文件名自带日期、可直接按天取用（`analyze-2026-08-22.log`）。

实现要点：
- 第一次写入时按**本地日期**建文件；跨天时自动关闭旧文件、开新文件；
- 日志 handler 自带锁，进程内多协程写安全；
- 文件写失败只吞掉（日志系统不能反过来把业务打挂）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


class DailyFileHandler(logging.Handler):
    """按本地日期写 `{prefix}-YYYY-MM-DD.log` 的文件 handler。"""

    def __init__(self, log_dir: Path | str, prefix: str = "analyze", level: int = logging.INFO):
        super().__init__(level)
        self.log_dir = Path(log_dir)
        self.prefix = prefix
        self._date = ""
        self._file = None

    def _path_for(self, day: str) -> Path:
        return self.log_dir / f"{self.prefix}-{day}.log"

    def _ensure_file(self) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        if self._file is not None and day == self._date:
            return
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file = open(
            self._path_for(day), "a", encoding="utf-8", errors="replace"
        )
        self._date = day

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_file()
            if self._file is None:
                return
            self._file.write(self.format(record) + "\n")
            self._file.flush()
        except Exception:  # noqa: BLE001 —— 日志失败不能影响业务
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        finally:
            self._file = None
            super().close()
