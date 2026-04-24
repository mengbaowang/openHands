"""
风险管理器
实现风险评分、仓位管理、最大回撤监控
"""
from typing import Dict, List
import config


class RiskManager:
    """风险管理器"""
    
    def __init__(self, db):
        self.db = db
    
    def calculate_risk_score(self, model_id: int, portfolio: Dict) -> Dict:
        """
        计算风险评分 (0-100分，分数越高风险越大)
        
        Args:
            model_id: 模型ID
            portfolio: 投资组合
            
        Returns:
            {
                'score': 风险评分,
                'level': 风险等级,
                'warnings': 警告列表
            }
        """
        score = 0
        warnings = []
        
        # 1. 集中度风险（单币种占比过高）
        if portfolio['positions']:
            total_value = portfolio['total_value']
            for pos in portfolio['positions']:
                position_value = pos['quantity'] * pos['avg_price']
                ratio = position_value / total_value if total_value > 0 else 0
                
                if ratio > config.MAX_POSITION_RATIO:
                    score += 30
                    warnings.append(f"⚠️ {pos['coin']}持仓占比过高({ratio:.1%})")
                    break
        
        # 2. 杠杆风险
        if portfolio['positions']:
            avg_leverage = sum(p['leverage'] for p in portfolio['positions']) / len(portfolio['positions'])
            if avg_leverage > 10:
                score += 25
                warnings.append(f"⚠️ 平均杠杆过高({avg_leverage:.1f}x)")
        
        # 3. 持仓数量风险
        position_count = len(portfolio['positions'])
        if position_count > 5:
            score += 15
            warnings.append(f"⚠️ 持仓数量过多({position_count}个)")
        
        # 4. 未实现亏损风险
        unrealized_pnl = portfolio.get('unrealized_pnl', 0)
        if unrealized_pnl < 0:
            loss_ratio = abs(unrealized_pnl) / portfolio['total_value'] if portfolio['total_value'] > 0 else 0
            if loss_ratio > 0.1:  # 未实现亏损超过10%
                score += 20
                warnings.append(f"⚠️ 未实现亏损较大({loss_ratio:.1%})")
        
        # 5. 最大回撤风险
        drawdown = self._calculate_max_drawdown(model_id)
        if drawdown > config.MAX_DRAWDOWN_WARNING:
            score += 30
            warnings.append(f"⚠️ 最大回撤过大({drawdown:.1%})")
        
        # 确定风险等级
        if score >= 70:
            level = '高风险'
        elif score >= 40:
            level = '中等风险'
        else:
            level = '低风险'
        
        return {
            'score': min(score, 100),
            'level': level,
            'warnings': warnings,
            'drawdown': drawdown
        }
    
    def _calculate_max_drawdown(self, model_id: int) -> float:
        """
        计算最大回撤
        
        Args:
            model_id: 模型ID
            
        Returns:
            最大回撤比例
        """
        history = self.db.get_account_value_history(model_id, limit=1000)
        
        if not history:
            return 0.0
        
        values = [h['total_value'] for h in reversed(history)]
        
        if not values:
            return 0.0
        
        peak = values[0]
        max_drawdown = 0.0
        
        for value in values:
            if value > peak:
                peak = value
            
            drawdown = (peak - value) / peak if peak > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)
        
        return max_drawdown
    
    def check_position_size(self, portfolio: Dict, coin: str, quantity: float, price: float) -> Dict:
        """
        检查仓位大小是否合理
        
        Args:
            portfolio: 投资组合
            coin: 币种
            quantity: 数量
            price: 价格
            
        Returns:
            {
                'allowed': 是否允许,
                'reason': 原因,
                'suggested_quantity': 建议数量
            }
        """
        position_value = quantity * price
        total_value = portfolio['total_value']
        
        if total_value <= 0:
            return {
                'allowed': False,
                'reason': '账户总值为0',
                'suggested_quantity': 0
            }
        
        ratio = position_value / total_value
        
        # 检查单币种持仓比例
        if ratio > config.MAX_POSITION_RATIO:
            suggested_quantity = (total_value * config.MAX_POSITION_RATIO) / price
            return {
                'allowed': False,
                'reason': f'单币种持仓不能超过{config.MAX_POSITION_RATIO:.0%}',
                'suggested_quantity': suggested_quantity
            }
        
        return {
            'allowed': True,
            'reason': '仓位合理',
            'suggested_quantity': quantity
        }
    
    def calculate_optimal_position_size(self, portfolio: Dict, risk_per_trade: float = None) -> float:
        """
        计算最优仓位大小（基于固定比例风险模型）
        
        Args:
            portfolio: 投资组合
            risk_per_trade: 单笔交易风险比例（默认使用配置）
            
        Returns:
            建议的仓位金额
        """
        if risk_per_trade is None:
            risk_per_trade = config.MAX_RISK_PER_TRADE
        
        return portfolio['total_value'] * risk_per_trade
    
    def should_pause_trading(self, model_id: int, portfolio: Dict) -> Dict:
        """
        判断是否应该暂停交易
        
        Args:
            model_id: 模型ID
            portfolio: 投资组合
            
        Returns:
            {
                'should_pause': 是否暂停,
                'reason': 原因
            }
        """
        # 检查最大回撤
        drawdown = self._calculate_max_drawdown(model_id)
        if drawdown > config.MAX_DRAWDOWN_CRITICAL:
            return {
                'should_pause': True,
                'reason': f'最大回撤超过{config.MAX_DRAWDOWN_CRITICAL:.0%}，暂停交易'
            }
        
        # 检查连续亏损
        recent_trades = self.db.get_trades(model_id, limit=5)
        if len(recent_trades) >= 5:
            losing_streak = all(t['pnl'] < 0 for t in recent_trades[:5])
            if losing_streak:
                return {
                    'should_pause': True,
                    'reason': '连续5笔亏损，暂停交易'
                }
        
        # 检查账户余额
        if portfolio['cash'] < portfolio['total_value'] * 0.1:
            return {
                'should_pause': True,
                'reason': '可用现金不足10%，暂停交易'
            }
        
        return {
            'should_pause': False,
            'reason': '风险可控'
        }
    
    def get_risk_metrics(self, model_id: int, portfolio: Dict) -> Dict:
        """
        获取完整的风险指标
        
        Args:
            model_id: 模型ID
            portfolio: 投资组合
            
        Returns:
            完整的风险指标字典
        """
        risk_score = self.calculate_risk_score(model_id, portfolio)
        pause_check = self.should_pause_trading(model_id, portfolio)
        
        return {
            'risk_score': risk_score['score'],
            'risk_level': risk_score['level'],
            'warnings': risk_score['warnings'],
            'max_drawdown': risk_score['drawdown'],
            'should_pause': pause_check['should_pause'],
            'pause_reason': pause_check['reason']
        }

