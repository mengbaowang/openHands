"""SQLite 持久化层，负责用户、模型、交易、持仓、对话和账户历史。"""
import sqlite3
import json
import config
from datetime import datetime
from typing import List, Dict, Optional

class Database:
    def __init__(self, db_path: str = 'trading_bot.db'):
        self.db_path = db_path
        self._okx_trader = None
        
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_okx_trader(self):
        """Reuse a single OKXTrader instance so throttled logs actually work."""
        if self._okx_trader is not None:
            return self._okx_trader

        from services.exchanges.okx_adapter import OKXTrader
        self._okx_trader = OKXTrader()
        return self._okx_trader
    
    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Models table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                api_key TEXT NOT NULL,
                api_url TEXT NOT NULL,
                model_name TEXT NOT NULL,
                initial_capital REAL DEFAULT 10000,
                system_prompt TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')

        # 为已存在的models表添加user_id字段
        try:
            cursor.execute('ALTER TABLE models ADD COLUMN user_id INTEGER')
        except:
            pass  # 字段已存在

        # 为已存在的models表添加system_prompt字段
        try:
            cursor.execute('ALTER TABLE models ADD COLUMN system_prompt TEXT')
        except:
            pass  # 字段已存在
        
        # Portfolios table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                coin TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                side TEXT DEFAULT 'long',
                stop_loss REAL,
                take_profit REAL,
                entry_ord_id TEXT,
                okx_risk_algo_id TEXT,
                okx_risk_algo_cl_ord_id TEXT,
                trailing_tier REAL DEFAULT 0,
                peak_price REAL,
                peak_profit_pct REAL DEFAULT 0,
                last_profit_pct REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id),
                UNIQUE(model_id, coin, side)
            )
        ''')

        # 为已存在的表添加新字段（如果不存在）
        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN stop_loss REAL')
        except:
            pass  # 字段已存在

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN take_profit REAL')
        except:
            pass  # 字段已存在

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN entry_ord_id TEXT')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN okx_risk_algo_id TEXT')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN okx_risk_algo_cl_ord_id TEXT')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN trailing_tier REAL DEFAULT 0')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN peak_price REAL')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN peak_profit_pct REAL DEFAULT 0')
        except:
            pass

        try:
            cursor.execute('ALTER TABLE portfolios ADD COLUMN last_profit_pct REAL DEFAULT 0')
        except:
            pass
        
        # Trades table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                coin TEXT NOT NULL,
                signal TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                side TEXT DEFAULT 'long',
                pnl REAL DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')
        
        # Conversations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                user_prompt TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                cot_trace TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')
        
        # Account values history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # ============ Model Management ============
    
    def add_model(self, user_id: int, name: str, api_key: str, api_url: str,
                   model_name: str, initial_capital: float = 10000, system_prompt: str = None) -> int:
        """Add new trading model"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO models (user_id, name, api_key, api_url, model_name, initial_capital, system_prompt)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, name, api_key, api_url, model_name, initial_capital, system_prompt))
        model_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return model_id
    
    def get_model(self, model_id: int) -> Optional[Dict]:
        """Get model information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM models WHERE id = ?', (model_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_all_models(self, user_id: int = None) -> List[Dict]:
        """Get all trading models (optionally filtered by user_id)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if user_id:
            cursor.execute('SELECT * FROM models WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        else:
            cursor.execute('SELECT * FROM models ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_model_prompt(self, model_id: int, system_prompt: str):
        """Update model's system prompt"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE models
            SET system_prompt = ?
            WHERE id = ?
        ''', (system_prompt, model_id))
        conn.commit()
        conn.close()

    def delete_model(self, model_id: int):
        """Delete model and related data"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM models WHERE id = ?', (model_id,))
        cursor.execute('DELETE FROM portfolios WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM trades WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM conversations WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM account_values WHERE model_id = ?', (model_id,))
        conn.commit()
        conn.close()

    # ============ Portfolio Management ============
    
    def update_position(self, model_id: int, coin: str, quantity: float,
                       avg_price: float, leverage: int = 1, side: str = 'long',
                       stop_loss: float = None, take_profit: float = None,
                       entry_ord_id: str = None, okx_risk_algo_id: str = None,
                       okx_risk_algo_cl_ord_id: str = None, trailing_tier: float = 0,
                       peak_price: float = None, peak_profit_pct: float = None,
                       last_profit_pct: float = None):
        """Update position with stop loss and take profit"""
        conn = self.get_connection()
        cursor = conn.cursor()
        effective_peak_price = avg_price if peak_price is None else peak_price
        effective_peak_profit_pct = 0 if peak_profit_pct is None else peak_profit_pct
        effective_last_profit_pct = 0 if last_profit_pct is None else last_profit_pct
        cursor.execute('''
            INSERT INTO portfolios (
                model_id, coin, quantity, avg_price, leverage, side, stop_loss, take_profit,
                entry_ord_id, okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier,
                peak_price, peak_profit_pct, last_profit_pct, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(model_id, coin, side) DO UPDATE SET
                quantity = excluded.quantity,
                avg_price = excluded.avg_price,
                leverage = excluded.leverage,
                stop_loss = excluded.stop_loss,
                take_profit = excluded.take_profit,
                entry_ord_id = excluded.entry_ord_id,
                okx_risk_algo_id = excluded.okx_risk_algo_id,
                okx_risk_algo_cl_ord_id = excluded.okx_risk_algo_cl_ord_id,
                trailing_tier = excluded.trailing_tier,
                peak_price = excluded.peak_price,
                peak_profit_pct = excluded.peak_profit_pct,
                last_profit_pct = excluded.last_profit_pct,
                updated_at = CURRENT_TIMESTAMP
        ''', (
            model_id, coin, quantity, avg_price, leverage, side, stop_loss, take_profit,
            entry_ord_id, okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier,
            effective_peak_price, effective_peak_profit_pct, effective_last_profit_pct
        ))
        conn.commit()
        conn.close()

    def upsert_position_delta(self, model_id: int, coin: str, quantity_delta: float,
                              price: float, leverage: int = 1, side: str = 'long',
                              stop_loss: float = None, take_profit: float = None,
                              entry_ord_id: str = None, okx_risk_algo_id: str = None,
                              okx_risk_algo_cl_ord_id: str = None, trailing_tier: float = None,
                              peak_price: float = None, peak_profit_pct: float = None,
                              last_profit_pct: float = None) -> Dict:
        """Apply a quantity delta and keep a weighted average entry price."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT quantity, avg_price, leverage, stop_loss, take_profit, entry_ord_id,
                   okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier,
                   peak_price, peak_profit_pct, last_profit_pct
            FROM portfolios
            WHERE model_id = ? AND coin = ? AND side = ?
        ''', (model_id, coin, side))
        existing = cursor.fetchone()

        if existing:
            current_quantity = float(existing['quantity'])
            current_avg_price = float(existing['avg_price'])
            new_quantity = current_quantity + float(quantity_delta)

            if new_quantity <= 0:
                cursor.execute('''
                    DELETE FROM portfolios
                    WHERE model_id = ? AND coin = ? AND side = ?
                ''', (model_id, coin, side))
                conn.commit()
                conn.close()
                return {
                    'quantity': 0.0,
                    'avg_price': 0.0,
                    'leverage': leverage,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'entry_ord_id': entry_ord_id,
                    'okx_risk_algo_id': okx_risk_algo_id,
                    'okx_risk_algo_cl_ord_id': okx_risk_algo_cl_ord_id,
                    'trailing_tier': trailing_tier if trailing_tier is not None else 0,
                    'peak_price': peak_price if peak_price is not None else price,
                    'peak_profit_pct': peak_profit_pct if peak_profit_pct is not None else 0,
                    'last_profit_pct': last_profit_pct if last_profit_pct is not None else 0
                }

            weighted_avg_price = (
                (current_quantity * current_avg_price) + (float(quantity_delta) * float(price))
            ) / new_quantity
            cursor.execute('''
                UPDATE portfolios
                SET quantity = ?, avg_price = ?, leverage = ?, stop_loss = ?, take_profit = ?,
                    entry_ord_id = ?, okx_risk_algo_id = ?, okx_risk_algo_cl_ord_id = ?, trailing_tier = ?,
                    peak_price = ?, peak_profit_pct = ?, last_profit_pct = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE model_id = ? AND coin = ? AND side = ?
            ''', (
                new_quantity,
                weighted_avg_price,
                leverage,
                stop_loss if stop_loss is not None else existing['stop_loss'],
                take_profit if take_profit is not None else existing['take_profit'],
                entry_ord_id if entry_ord_id is not None else existing['entry_ord_id'],
                okx_risk_algo_id if okx_risk_algo_id is not None else existing['okx_risk_algo_id'],
                okx_risk_algo_cl_ord_id if okx_risk_algo_cl_ord_id is not None else existing['okx_risk_algo_cl_ord_id'],
                trailing_tier if trailing_tier is not None else existing['trailing_tier'],
                peak_price if peak_price is not None else existing['peak_price'],
                peak_profit_pct if peak_profit_pct is not None else existing['peak_profit_pct'],
                last_profit_pct if last_profit_pct is not None else existing['last_profit_pct'],
                model_id,
                coin,
                side
            ))
            conn.commit()
            conn.close()
            return {
                'quantity': new_quantity,
                'avg_price': weighted_avg_price,
                'leverage': leverage,
                'stop_loss': stop_loss if stop_loss is not None else existing['stop_loss'],
                'take_profit': take_profit if take_profit is not None else existing['take_profit'],
                'entry_ord_id': entry_ord_id if entry_ord_id is not None else existing['entry_ord_id'],
                'okx_risk_algo_id': okx_risk_algo_id if okx_risk_algo_id is not None else existing['okx_risk_algo_id'],
                'okx_risk_algo_cl_ord_id': okx_risk_algo_cl_ord_id if okx_risk_algo_cl_ord_id is not None else existing['okx_risk_algo_cl_ord_id'],
                'trailing_tier': trailing_tier if trailing_tier is not None else existing['trailing_tier'],
                'peak_price': peak_price if peak_price is not None else existing['peak_price'],
                'peak_profit_pct': peak_profit_pct if peak_profit_pct is not None else existing['peak_profit_pct'],
                'last_profit_pct': last_profit_pct if last_profit_pct is not None else existing['last_profit_pct']
            }

        cursor.execute('''
            INSERT INTO portfolios (
                model_id, coin, quantity, avg_price, leverage, side, stop_loss, take_profit,
                entry_ord_id, okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier,
                peak_price, peak_profit_pct, last_profit_pct, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            model_id, coin, float(quantity_delta), float(price), leverage, side, stop_loss, take_profit,
            entry_ord_id, okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier if trailing_tier is not None else 0,
            peak_price if peak_price is not None else float(price),
            peak_profit_pct if peak_profit_pct is not None else 0,
            last_profit_pct if last_profit_pct is not None else 0
        ))
        conn.commit()
        conn.close()
        return {
            'quantity': float(quantity_delta),
            'avg_price': float(price),
            'leverage': leverage,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'entry_ord_id': entry_ord_id,
            'okx_risk_algo_id': okx_risk_algo_id,
            'okx_risk_algo_cl_ord_id': okx_risk_algo_cl_ord_id,
            'trailing_tier': trailing_tier if trailing_tier is not None else 0,
            'peak_price': peak_price if peak_price is not None else float(price),
            'peak_profit_pct': peak_profit_pct if peak_profit_pct is not None else 0,
            'last_profit_pct': last_profit_pct if last_profit_pct is not None else 0
        }

    def reduce_position(self, model_id: int, coin: str, quantity_delta: float, side: str = 'long') -> Optional[Dict]:
        """Reduce a position quantity while preserving its average entry price."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT quantity, avg_price, leverage, stop_loss, take_profit, entry_ord_id,
                   okx_risk_algo_id, okx_risk_algo_cl_ord_id, trailing_tier,
                   peak_price, peak_profit_pct, last_profit_pct
            FROM portfolios
            WHERE model_id = ? AND coin = ? AND side = ?
        ''', (model_id, coin, side))
        existing = cursor.fetchone()

        if not existing:
            conn.close()
            return None

        current_quantity = float(existing['quantity'])
        remaining_quantity = current_quantity - float(quantity_delta)
        if remaining_quantity <= 0:
            cursor.execute('''
                DELETE FROM portfolios
                WHERE model_id = ? AND coin = ? AND side = ?
            ''', (model_id, coin, side))
            conn.commit()
            conn.close()
            return {
                'quantity': 0.0,
                'avg_price': float(existing['avg_price']),
                'leverage': int(existing['leverage']),
                'stop_loss': existing['stop_loss'],
                'take_profit': existing['take_profit'],
                'entry_ord_id': existing['entry_ord_id'],
                'okx_risk_algo_id': existing['okx_risk_algo_id'],
                'okx_risk_algo_cl_ord_id': existing['okx_risk_algo_cl_ord_id'],
                'trailing_tier': existing['trailing_tier'],
                'peak_price': existing['peak_price'],
                'peak_profit_pct': existing['peak_profit_pct'],
                'last_profit_pct': existing['last_profit_pct']
            }

        cursor.execute('''
            UPDATE portfolios
            SET quantity = ?, updated_at = CURRENT_TIMESTAMP
            WHERE model_id = ? AND coin = ? AND side = ?
        ''', (remaining_quantity, model_id, coin, side))
        conn.commit()
        conn.close()
        return {
            'quantity': remaining_quantity,
            'avg_price': float(existing['avg_price']),
            'leverage': int(existing['leverage']),
            'stop_loss': existing['stop_loss'],
            'take_profit': existing['take_profit'],
            'entry_ord_id': existing['entry_ord_id'],
            'okx_risk_algo_id': existing['okx_risk_algo_id'],
            'okx_risk_algo_cl_ord_id': existing['okx_risk_algo_cl_ord_id'],
            'trailing_tier': existing['trailing_tier'],
            'peak_price': existing['peak_price'],
            'peak_profit_pct': existing['peak_profit_pct'],
            'last_profit_pct': existing['last_profit_pct']
        }
    
    
    def close_position(self, model_id: int, coin: str, side: str = 'long'):
        """Close position"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM portfolios WHERE model_id = ? AND coin = ? AND side = ?
        ''', (model_id, coin, side))
        conn.commit()
        conn.close()

    def get_open_portfolio_rows(self, model_id: int) -> List[Dict]:
        """Return all locally tracked open portfolio rows for reconciliation."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM portfolios
            WHERE model_id = ? AND quantity > 0
            ORDER BY updated_at DESC
        ''', (model_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ============ Trade Records ============
    
    def add_trade(self, model_id: int, coin: str, signal: str, quantity: float,
                  price: float, leverage: int = 1, side: str = 'long', pnl: float = 0,
                  timestamp: str = None):
        """Add trade record"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if timestamp:
            cursor.execute('''
                INSERT INTO trades (model_id, coin, signal, quantity, price, leverage, side, pnl, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (model_id, coin, signal, quantity, price, leverage, side, pnl, timestamp))
        else:
            cursor.execute('''
                INSERT INTO trades (model_id, coin, signal, quantity, price, leverage, side, pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (model_id, coin, signal, quantity, price, leverage, side, pnl))
        conn.commit()
        conn.close()
    
    def get_trades(self, model_id: int, limit: int = 50) -> List[Dict]:
        """Get trade history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ============ Conversation History ============
    
    def add_conversation(self, model_id: int, user_prompt: str, 
                        ai_response: str, cot_trace: str = ''):
        """Add conversation record"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (model_id, user_prompt, ai_response, cot_trace)
            VALUES (?, ?, ?, ?)
        ''', (model_id, user_prompt, ai_response, cot_trace))
        conn.commit()
        conn.close()
    
    def get_conversations(self, model_id: int, limit: int = 20) -> List[Dict]:
        """Get conversation history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM conversations WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ============ Account Value History ============
    
    def record_account_value(self, model_id: int, total_value: float, 
                            cash: float, positions_value: float):
        """Record account value snapshot"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO account_values (model_id, total_value, cash, positions_value)
            VALUES (?, ?, ?, ?)
        ''', (model_id, total_value, cash, positions_value))
        conn.commit()
        conn.close()
    
    def get_account_value_history(self, model_id: int, limit: int = 100) -> List[Dict]:
        """Get account value history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM account_values WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ============ User Management ============

    def create_user(self, username: str, password_hash: str, email: str = None) -> int:
        """Create a new user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (username, password_hash, email)
            VALUES (?, ?, ?)
        ''', (username, password_hash, email))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get user by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, email, created_at FROM users')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


    def get_portfolio(self, model_id: int, current_prices: Dict = None) -> Dict:
        """从 OKX API 获取投资组合。"""
        try:
            okx_trader = self.get_okx_trader()
        except ImportError:
            print("[ERROR] okx_trader 导入失败")
            return {'cash': 0, 'positions': [], 'total_value': 0, 'positions_value': 0, 'realized_pnl': 0, 'unrealized_pnl': 0, 'frozen_margin': 0, 'wallet_balances': {}}
        except Exception as e:
            print(f"[ERROR] OKXTrader 初始化失败：{e}")
            return {'cash': 0, 'positions': [], 'total_value': 0, 'positions_value': 0, 'realized_pnl': 0, 'unrealized_pnl': 0, 'frozen_margin': 0, 'wallet_balances': {}}
        
        try:
            # 获取账户余额
            balance_data = okx_trader.get_balance()
            if not balance_data or 'error' in balance_data:
                print(f"[WARN] OKX 账户余额获取失败: {balance_data.get('error', '未知错误')}")
                return {'cash': 0, 'positions': [], 'total_value': 0, 'positions_value': 0, 'realized_pnl': 0, 'unrealized_pnl': 0, 'frozen_margin': 0, 'wallet_balances': {}}
            
            details = balance_data.get('details', [])
            
            # 1. 计算账户总值（所有币种的等值 USDT 之和）
            total_value = 0
            usdt_available = 0
            frozen_margin = 0
            wallet_balances = {}
            
            for item in details:
                ccy = item.get('ccy')
                eq_usd = float(item.get('eqUsd', 0))
                
                if eq_usd > 0:
                    total_value += eq_usd
                
                if ccy == 'USDT':
                    usdt_available = float(item.get('availEq', 0))
                    frozen_margin = float(item.get('frozenBal', 0))
                
                # 记录钱包余额
                wallet_balances[ccy] = {
                    'total': float(item.get('eq', 0)),
                    'available': float(item.get('availEq', 0)),
                    'frozen': float(item.get('frozenBal', 0))
                }
            
            # 2. 现金价值 = USDT 可用余额
            cash = usdt_available
            
            # print(f"[DEBUG] 账户总值: {total_value:.2f} USDT")
            # print(f"[DEBUG] 现金价值: {cash:.2f} USDT")
            # print(f"[DEBUG] 已用保证金: {frozen_margin:.2f} USDT")
            
            # 3. 获取合约持仓
            positions_data = okx_trader.get_positions()
            if 'error' in positions_data:
                positions_data = []
            
            positions_value = 0
            pos_details = []
            unrealized_pnl = 0
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE model_id = ?
            ''', (model_id,))
            realized_pnl = cursor.fetchone()['total_pnl']
            conn.close()
            
            for pos in positions_data:
                coin = pos['coin']
                if coin in current_prices:
                    current_price = current_prices[coin]
                    position_quantity = pos.get('coin_quantity', 0.0)
                    position_value = pos.get('notional_usdt', position_quantity * current_prices[coin])
                    positions_value += position_value
                    if pos['side'] == 'long':
                        pos_pnl = (current_price - pos['avg_price']) * position_quantity
                    else:
                        pos_pnl = (pos['avg_price'] - current_price) * position_quantity
                    unrealized_pnl += pos_pnl
                    
                    db_pos = self.get_position(model_id, coin, pos['side'])
                    pos_details.append({
                        'coin': coin,
                        'side': pos['side'],
                        'quantity': position_quantity,
                        'contracts': pos.get('contracts', pos.get('size')),
                        'avg_price': pos['avg_price'],
                        'current_price': current_price,
                        'leverage': pos['leverage'],
                        'pnl': pos_pnl,
                        'stop_loss': db_pos.get('stop_loss') if db_pos else None,
                        'take_profit': db_pos.get('take_profit') if db_pos else None,
                        'peak_price': db_pos.get('peak_price') if db_pos else None,
                        'peak_profit_pct': db_pos.get('peak_profit_pct') if db_pos else 0,
                        'last_profit_pct': db_pos.get('last_profit_pct') if db_pos else 0,
                        'value': position_value
                    })
            
            return {
                'cash': cash,
                'positions': pos_details,
                'total_value': total_value,
                'positions_value': positions_value,
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'frozen_margin': frozen_margin,
                'wallet_balances': wallet_balances
            }
            
        except Exception as e:
            print(f"[ERROR] 获取 OKX 投资组合失败：{e}")
            import traceback
            traceback.print_exc()
            return {'cash': 0, 'positions': [], 'total_value': 0, 'positions_value': 0, 'realized_pnl': 0, 'unrealized_pnl': 0, 'frozen_margin': 0, 'wallet_balances': {}}

    # ============ Position Management ============
    def get_position(self, model_id: int, coin: str, side: str) -> Optional[Dict]:
        """获取指定持仓（用于止盈止损）"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT stop_loss, take_profit, entry_ord_id, okx_risk_algo_id,
                   okx_risk_algo_cl_ord_id, trailing_tier,
                   peak_price, peak_profit_pct, last_profit_pct
            FROM portfolios
            WHERE model_id = ? AND coin = ? AND side = ?
        ''', (model_id, coin, side))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
