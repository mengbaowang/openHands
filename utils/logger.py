"""
统一日志管理模块
"""
import logging
import sys
from pathlib import Path
import threading
import config

_runtime_logging_configured = False
_runtime_log_handle = None

class TeeStream:
    """Mirror stdout/stderr to both the terminal and a log file."""

    def __init__(self, original_stream, log_handle):
        self.original_stream = original_stream
        self.log_handle = log_handle
        self._lock = threading.Lock()
        self.encoding = getattr(original_stream, 'encoding', 'utf-8')

    def write(self, data):
        if not data:
            return 0
        with self._lock:
            self.original_stream.write(data)
            self.original_stream.flush()
            self.log_handle.write(data)
            self.log_handle.flush()
        return len(data)

    def flush(self):
        with self._lock:
            self.original_stream.flush()
            self.log_handle.flush()

    def isatty(self):
        return getattr(self.original_stream, 'isatty', lambda: False)()

    def fileno(self):
        return self.original_stream.fileno()

def setup_runtime_logging(log_file: str = None):
    """
    Redirect all stdout/stderr prints to both console and a local log file.
    Safe to call multiple times; only the first call takes effect.
    """
    global _runtime_logging_configured, _runtime_log_handle

    if _runtime_logging_configured:
        return

    log_path = Path(log_file or config.LOG_FILE)
    if not log_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent
        log_path = project_root / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _runtime_log_handle = open(log_path, mode='a', encoding='utf-8', buffering=1)

    if not isinstance(sys.stdout, TeeStream):
        sys.stdout = TeeStream(sys.stdout, _runtime_log_handle)
    if not isinstance(sys.stderr, TeeStream):
        sys.stderr = TeeStream(sys.stderr, _runtime_log_handle)

    _runtime_logging_configured = True

def setup_logger(name: str = 'trading_bot') -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        配置好的Logger实例
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加handler
    if logger.handlers:
        return logger
    
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    
    # 文件输出
    log_file = Path(config.LOG_FILE)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(config.LOG_FORMAT)
    file_handler.setFormatter(file_formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger


# 创建全局logger实例
logger = setup_logger()

