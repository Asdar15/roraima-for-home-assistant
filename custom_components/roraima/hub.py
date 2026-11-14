"""The pairing code of a Roraima Hub, read from the file the Hub writes: nobody types or sees it."""

from __future__ import annotations

import json
import logging
import os
import stat

from .const import CODE_PATTERN, HUB_FILE_MAX

_LOGGER = logging.getLogger(__name__)


def read_hub_code(path: str) -> str | None:
    """Return the code in the Hub file, or None when there is no Hub file or it is not valid.

    The file is a regular file of at most 4 KiB: {"v": 1, "code": "rha_<64 hex>"}. Its content never reaches the log.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as err:
        _LOGGER.warning("The Roraima Hub pairing file cannot be read (%s)", type(err).__name__)
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _LOGGER.warning("The Roraima Hub pairing file is not a regular file")
            return None
        raw = os.read(fd, HUB_FILE_MAX + 1)
    finally:
        os.close(fd)
    try:
        data = json.loads(raw.decode("utf-8")) if len(raw) <= HUB_FILE_MAX else None
    except (UnicodeDecodeError, ValueError):
        data = None
    code = data.get("code") if isinstance(data, dict) and data.get("v") == 1 else None
    if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
        _LOGGER.warning("The Roraima Hub pairing file is not in the expected form")
        return None
    return code
