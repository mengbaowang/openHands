"""
Unified logging helpers.
"""
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

import config

_runtime_logging_configured = False
_runtime_log_writer = None


def _resolve_log_parts(log_file: str = None):
    """Resolve the log directory and file naming parts."""
    configured = Path(log_file or config.LOG_FILE)
    project_root = Path(__file__).resolve().parent.parent

    if configured.is_absolute():
        log_dir = configured.parent
        base_name = configured.stem or 'trading_bot'
        suffix = configured.suffix or '.log'
        return log_dir, base_name, suffix

    if configured.parent != Path('.'):
        log_dir = project_root / configured.parent
    else:
        log_dir = project_root / 'logs'

    base_name = configured.stem or 'trading_bot'
    suffix = configured.suffix or '.log'
    return log_dir, base_name, suffix


def get_daily_log_path(log_file: str = None, date_str: str = None) -> Path:
    """Return the log path for the given day."""
    log_dir, base_name, suffix = _resolve_log_parts(log_file)
    day = date_str or datetime.now().strftime('%Y-%m-%d')
    return log_dir / f'{base_name}-{day}{suffix}'


class DailyLogWriter:
    """Write logs into a file that rotates automatically each day."""

    def __init__(self, log_file: str = None):
        self.log_file = log_file
        self._lock = threading.Lock()
        self._current_date = None
        self._current_path = None
        self._handle = None

    def _ensure_handle(self):
        current_date = datetime.now().strftime('%Y-%m-%d')
        if self._handle is not None and self._current_date == current_date:
            return

        if self._handle is not None:
            self._handle.flush()
            self._handle.close()

        log_path = get_daily_log_path(self.log_file, current_date)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(log_path, mode='a', encoding='utf-8', buffering=1)
        self._current_date = current_date
        self._current_path = log_path

    def write(self, text: str):
        with self._lock:
            self._ensure_handle()
            self._handle.write(text)
            self._handle.flush()

    def flush(self):
        with self._lock:
            if self._handle is not None:
                self._handle.flush()

    @property
    def current_path(self):
        with self._lock:
            self._ensure_handle()
            return self._current_path


class TeeStream:
    """Mirror stdout/stderr to both terminal and a daily log file."""

    def __init__(self, original_stream, log_writer: DailyLogWriter):
        self.original_stream = original_stream
        self.log_writer = log_writer
        self._lock = threading.Lock()
        self.encoding = getattr(original_stream, 'encoding', 'utf-8')

    def _normalize_text(self, data):
        if isinstance(data, bytes):
            return data.decode(self.encoding or 'utf-8', errors='replace')
        if isinstance(data, str):
            return data
        return str(data)

    def write(self, data):
        if not data:
            return 0
        text = self._normalize_text(data)
        with self._lock:
            self.original_stream.write(text)
            self.original_stream.flush()
            self.log_writer.write(text)
        return len(text)

    def flush(self):
        with self._lock:
            self.original_stream.flush()
            self.log_writer.flush()

    def isatty(self):
        return getattr(self.original_stream, 'isatty', lambda: False)()

    def fileno(self):
        return self.original_stream.fileno()


def setup_runtime_logging(log_file: str = None):
    """
    Redirect all stdout/stderr prints to both console and a local daily log file.
    Safe to call multiple times; only the first call takes effect.
    """
    global _runtime_logging_configured, _runtime_log_writer

    if _runtime_logging_configured:
        return

    _runtime_log_writer = DailyLogWriter(log_file)

    if not isinstance(sys.stdout, TeeStream):
        sys.stdout = TeeStream(sys.stdout, _runtime_log_writer)
    if not isinstance(sys.stderr, TeeStream):
        sys.stderr = TeeStream(sys.stderr, _runtime_log_writer)

    _runtime_logging_configured = True


def setup_logger(name: str = 'trading_bot') -> logging.Logger:
    """Set up a named logger with console + daily file output."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, config.LOG_LEVEL))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)

    file_writer = DailyLogWriter(config.LOG_FILE)

    class DailyFileHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                file_writer.write(msg + '\n')
            except Exception:
                self.handleError(record)

    file_handler = DailyFileHandler()
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(config.LOG_FORMAT)
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
