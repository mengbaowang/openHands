"""
时区工具模块 - 数据库存UTC，展示用东八区（UTC+8）
"""
from datetime import datetime, timedelta

# 东八区偏移量
TIMEZONE_OFFSET = timedelta(hours=8)

def get_current_utc_time() -> datetime:
    """
    获取当前UTC时间（用于数据库存储）

    Returns:
        datetime: UTC时间
    """
    return datetime.utcnow()

def get_current_utc_time_str(format: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    获取当前UTC时间字符串（用于数据库存储）

    Args:
        format: 时间格式，默认 '%Y-%m-%d %H:%M:%S'

    Returns:
        str: 格式化的UTC时间字符串
    """
    return get_current_utc_time().strftime(format)

def get_current_beijing_time() -> datetime:
    """
    获取当前东八区时间（用于日志显示）

    Returns:
        datetime: 东八区当前时间
    """
    return datetime.utcnow() + TIMEZONE_OFFSET

def get_current_beijing_time_str(format: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    获取当前东八区时间字符串（用于日志显示）

    Args:
        format: 时间格式，默认 '%Y-%m-%d %H:%M:%S'

    Returns:
        str: 格式化的东八区时间字符串
    """
    return get_current_beijing_time().strftime(format)

def utc_to_beijing(utc_time_str: str, format: str = '%Y-%m-%d %H:%M:%S', iso_format: bool = True) -> str:
    """
    将UTC时间字符串转换为东八区时间字符串（用于API返回）

    Args:
        utc_time_str: UTC时间字符串
        format: 输入时间格式，默认 '%Y-%m-%d %H:%M:%S'
        iso_format: 是否返回ISO 8601格式（带时区标记），默认True

    Returns:
        str: 东八区时间字符串（ISO 8601格式：2025-10-21T18:20:20+08:00）
    """
    try:
        utc_time = datetime.strptime(utc_time_str, format)
        beijing_time = utc_time + TIMEZONE_OFFSET

        if iso_format:
            # 返回ISO 8601格式，明确标记东八区时区
            return beijing_time.strftime('%Y-%m-%dT%H:%M:%S') + '+08:00'
        else:
            # 返回普通格式
            return beijing_time.strftime(format)
    except:
        return utc_time_str  # 解析失败返回原值

def beijing_to_utc(beijing_time: datetime) -> datetime:
    """
    将东八区时间转换为UTC时间

    Args:
        beijing_time: 东八区时间

    Returns:
        datetime: UTC时间
    """
    return beijing_time - TIMEZONE_OFFSET

