from __future__ import annotations

import inspect
import logging
import re
from typing import TYPE_CHECKING, Callable, List, Tuple, Union

from loguru import logger as default_logger

try:
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if TYPE_CHECKING:
    from loguru import Logger

class InterceptHandler(logging.Handler):
    """A logging handler that intercepts standard logging messages and redirects them to [loguru][loguru]."""

    def __init__(
        self,
        level: int = 0,
        logger: Logger = None,
        extra_patterns: List[Tuple[Union[str, Callable], int]] = [],
    ):
        """
        Args:
            level: The logging level threshold. Defaults to 0.
            logger: The loguru logger instance to use. If None, uses default logger.
            extra_patterns: List of pattern tuples to match and transform log messages.
                Each tuple contains a pattern (string regex or callable) and an optional level to set if matched.

        Example:
            ```python
            # Using regex patterns
            patterns = [
                (r"ERROR: (.*)", logging.ERROR),  # Match "ERROR: ..." and set ERROR level
                (r"WARNING: (.*)", logging.WARNING),  # Match "WARNING: ..." and set WARNING level
                (r"DEBUG: (.*)", logging.DEBUG),  # Match "DEBUG: ..." and set DEBUG level
            ]

            # Using callable patterns
            def parse_error(text):
                if "error" in text.lower():
                    return f"Found error: {text}"
                return None

            patterns = [
                (parse_error, logging.ERROR)  # Use custom function to detect errors
            ]

            handler = InterceptHandler(extra_patterns=patterns)
            ```
        """
        
        super().__init__(level)
        self.logger = logger if logger else default_logger
        self.extra_patterns = extra_patterns

    def emit(self, record):
        try:
            level = self.logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        text = record.getMessage()
        if RICH_AVAILABLE:
            text = Text.from_ansi(text).plain
        for p, l in self.extra_patterns:
            if callable(p):
                try:
                    result = p(text)
                    if result:
                        text = result
                        if l:
                            level = logging.getLevelName(l)
                        break
                except:
                    pass
            else:
                match = re.search(p, text, re.IGNORECASE)
                if match:
                    try:
                        text = match.group(1)
                    except IndexError:
                        pass
                    finally:
                        if l:
                            level = logging.getLevelName(l)
                        break
        self.logger.opt(depth=depth, exception=record.exc_info).log(level, text)

def apply_logging_adapter(extra_patterns: List[Tuple[Union[str, Callable], int]] = [], level: int = 20):
    """Configure the [logging][logging] root logger to use InterceptHandler.

    Args:
        extra_patterns: See [InterceptHandler][(m).InterceptHandler.__init__].
        level: See [InterceptHandler][(m).InterceptHandler.__init__].
    """

    logging.basicConfig(handlers=[InterceptHandler(extra_patterns=extra_patterns)], level=level, force=True)
