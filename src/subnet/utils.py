from typing import Literal, Any
import datetime
import sys


def iso_timestamp_now() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    iso_now = now.isoformat()
    return iso_now

"""
def log(
    msg: str,
    *values: object,
    sep: str | None = " ",
    end: str | None = "\n",
    file: Any | None = None,
    flush: Literal[False] = False,
):
    print(
        f"[{iso_timestamp_now()}] " + msg,
        *values,
        sep=sep,
        end=end,
        file=file,
        flush=flush,
    )
"""

# Define a class for the logger
class Logger:
    # Define color codes for different log levels
    COLORS = {
        "INFO": "\033[94m",    # Blue
        "WARNING": "\033[93m", # Yellow
        "ERROR": "\033[91m",   # Red
        "DEBUG": "\033[92m",   # Green
    }
    RESET_COLOR = "\033[0m"  # Reset color

    def __init__(self):
        pass

    def _log(self, level: str, msg: str, *values: object, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False):
        color = self.COLORS.get(level, "")
        timestamp = iso_timestamp_now()
        print(
            f"[{timestamp}] [{color}{level}{self.RESET_COLOR}] {msg}",
            *values,
            sep=sep,
            end=end,
            file=file or sys.stdout,
            flush=flush,
        )

    def info(self, msg: str, *values: object, **kwargs):
        self._log("INFO", msg, *values, **kwargs)

    def warning(self, msg: str, *values: object, **kwargs):
        self._log("WARNING", msg, *values, **kwargs)

    def error(self, msg: str, *values: object, **kwargs):
        self._log("ERROR", msg, *values, **kwargs)

    def debug(self, msg: str, *values: object, **kwargs):
        self._log("DEBUG", msg, *values, **kwargs)

log = Logger()