"""
用户认证工具
密码加密、Session管理、权限验证
"""
from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash


def hash_password(password: str) -> str:
    """
    密码哈希
    
    Args:
        password: 明文密码
        
    Returns:
        哈希后的密码
    """
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """
    验证密码
    
    Args:
        password_hash: 哈希后的密码
        password: 明文密码
        
    Returns:
        是否匹配
    """
    return check_password_hash(password_hash, password)


def login_required(f):
    """
    登录验证装饰器
    保护需要登录才能访问的API
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized', 'message': '请先登录'}), 401
        return f(*args, **kwargs)
    return decorated_function


def get_current_user_id():
    """
    获取当前登录用户ID
    
    Returns:
        用户ID，未登录返回None
    """
    return session.get('user_id')


def set_current_user(user_id: int, username: str):
    """
    设置当前登录用户
    
    Args:
        user_id: 用户ID
        username: 用户名
    """
    session['user_id'] = user_id
    session['username'] = username


def clear_current_user():
    """清除当前登录用户"""
    session.pop('user_id', None)
    session.pop('username', None)

