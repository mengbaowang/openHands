"""
自定义异常类
统一错误处理策略
"""

class TradingBotException(Exception):
    """交易机器人基础异常"""
    pass


class ModelNotFoundException(TradingBotException):
    """模型未找到异常"""
    pass


class InsufficientFundsException(TradingBotException):
    """资金不足异常"""
    pass


class InvalidParameterException(TradingBotException):
    """无效参数异常"""
    pass


class APIException(TradingBotException):
    """API调用异常"""
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


class MarketDataException(APIException):
    """市场数据获取异常"""
    pass


class LLMException(APIException):
    """LLM调用异常"""
    pass


class DatabaseException(TradingBotException):
    """数据库操作异常"""
    pass


class ValidationException(TradingBotException):
    """数据验证异常"""
    pass


class RiskManagementException(TradingBotException):
    """风险管理异常"""
    pass

