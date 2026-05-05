"""交易模型的绩效分析与报表生成模块。"""
from typing import Dict, List
from datetime import datetime, timedelta


class PerformanceAnalyzer:
    """绩效分析器"""
    
    def __init__(self, db):
        self.db = db
    
    def analyze_performance(self, model_id: int) -> Dict:
        """
        全面分析模型绩效
        
        Args:
            model_id: 模型ID
            
        Returns:
            完整的绩效分析报告
        """
        trades = self.db.get_trades(model_id, limit=1000)
        history = self.db.get_account_value_history(model_id, limit=1000)
        model = self.db.get_model(model_id)
        
        if not model:
            return {'error': 'Model not found'}
        
        initial_capital = model['initial_capital']
        
        return {
            'overview': self._calculate_overview(trades, history, initial_capital),
            'returns': self._calculate_returns(history, initial_capital),
            'risk_metrics': self._calculate_risk_metrics(trades, history, initial_capital),
            'trading_stats': self._calculate_trading_stats(trades),
            'monthly_performance': self._calculate_monthly_performance(trades, history),
            'coin_performance': self._calculate_coin_performance(trades)
        }
    
    def _calculate_overview(self, trades: List[Dict], history: List[Dict], 
                           initial_capital: float) -> Dict:
        """计算总览指标"""
        if not history:
            return {
                'total_return': 0,
                'total_pnl': 0,
                'current_value': initial_capital,
                'days_trading': 0
            }
        
        current_value = history[0]['total_value']
        total_return = ((current_value - initial_capital) / initial_capital) * 100
        total_pnl = current_value - initial_capital
        
        # 计算交易天数
        if len(history) > 1:
            first_date = datetime.fromisoformat(history[-1]['timestamp'])
            last_date = datetime.fromisoformat(history[0]['timestamp'])
            days_trading = (last_date - first_date).days
        else:
            days_trading = 0
        
        return {
            'total_return': total_return,
            'total_pnl': total_pnl,
            'current_value': current_value,
            'days_trading': days_trading,
            'initial_capital': initial_capital
        }
    
    def _calculate_returns(self, history: List[Dict], initial_capital: float) -> Dict:
        """计算收益率指标"""
        if not history:
            return {}
        
        values = [h['total_value'] for h in reversed(history)]
        
        # 日收益率
        daily_returns = []
        for i in range(1, len(values)):
            daily_return = (values[i] - values[i-1]) / values[i-1] if values[i-1] > 0 else 0
            daily_returns.append(daily_return)
        
        # 平均日收益率
        avg_daily_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        
        # 年化收益率（假设252个交易日）
        annualized_return = avg_daily_return * 252 * 100
        
        # 累计收益率
        cumulative_return = ((values[-1] - initial_capital) / initial_capital) * 100
        
        return {
            'avg_daily_return': avg_daily_return * 100,
            'annualized_return': annualized_return,
            'cumulative_return': cumulative_return
        }
    
    def _calculate_risk_metrics(self, trades: List[Dict], history: List[Dict], 
                                initial_capital: float) -> Dict:
        """计算风险指标"""
        if not history:
            return {}
        
        values = [h['total_value'] for h in reversed(history)]
        
        # 最大回撤
        max_drawdown = self._calculate_max_drawdown(values)
        
        # 波动率
        returns = []
        for i in range(1, len(values)):
            ret = (values[i] - values[i-1]) / values[i-1] if values[i-1] > 0 else 0
            returns.append(ret)
        
        if returns:
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            volatility = variance ** 0.5
            annualized_volatility = volatility * (252 ** 0.5) * 100
        else:
            avg_return = 0
            volatility = 0
            annualized_volatility = 0
        
        # 夏普比率（假设无风险利率为0）
        sharpe_ratio = (avg_return / volatility * (252 ** 0.5)) if volatility > 0 else 0
        
        # Sortino比率（只考虑下行波动）
        downside_returns = [r for r in returns if r < 0]
        if downside_returns:
            downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_volatility = downside_variance ** 0.5
            sortino_ratio = (avg_return / downside_volatility * (252 ** 0.5)) if downside_volatility > 0 else 0
        else:
            sortino_ratio = 0
        
        # Calmar比率（年化收益率 / 最大回撤）
        annualized_return = avg_return * 252
        calmar_ratio = (annualized_return / max_drawdown) if max_drawdown > 0 else 0
        
        return {
            'max_drawdown': max_drawdown * 100,
            'volatility': annualized_volatility,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'calmar_ratio': calmar_ratio
        }
    
    def _calculate_max_drawdown(self, values: List[float]) -> float:
        """计算最大回撤"""
        if not values:
            return 0.0
        
        peak = values[0]
        max_dd = 0.0
        
        for value in values:
            if value > peak:
                peak = value
            
            dd = (peak - value) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return max_dd
    
    def _calculate_trading_stats(self, trades: List[Dict]) -> Dict:
        """计算交易统计"""
        if not trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'avg_holding_time': 0
            }
        
        # 总交易次数
        total_trades = len(trades)
        
        # 盈利交易
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        
        # 胜率
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
        
        # 平均盈利/亏损
        avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # 盈亏比
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        # 最大单笔盈利/亏损
        max_win = max((t.get('pnl', 0) for t in trades), default=0)
        max_loss = min((t.get('pnl', 0) for t in trades), default=0)
        
        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_win': max_win,
            'max_loss': max_loss
        }
    
    def _calculate_monthly_performance(self, trades: List[Dict], 
                                      history: List[Dict]) -> List[Dict]:
        """计算月度绩效"""
        if not history:
            return []
        
        monthly_data = {}
        
        for record in history:
            timestamp = datetime.fromisoformat(record['timestamp'])
            month_key = timestamp.strftime('%Y-%m')
            
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    'month': month_key,
                    'values': []
                }
            
            monthly_data[month_key]['values'].append(record['total_value'])
        
        # 计算每月收益
        monthly_performance = []
        for month, data in sorted(monthly_data.items()):
            values = data['values']
            if len(values) > 1:
                monthly_return = ((values[0] - values[-1]) / values[-1]) * 100
            else:
                monthly_return = 0
            
            monthly_performance.append({
                'month': month,
                'return': monthly_return,
                'start_value': values[-1],
                'end_value': values[0]
            })
        
        return monthly_performance
    
    def _calculate_coin_performance(self, trades: List[Dict]) -> List[Dict]:
        """计算各币种绩效"""
        coin_stats = {}
        
        for trade in trades:
            coin = trade['coin']
            pnl = trade.get('pnl', 0)
            
            if coin not in coin_stats:
                coin_stats[coin] = {
                    'coin': coin,
                    'total_trades': 0,
                    'total_pnl': 0,
                    'winning_trades': 0,
                    'losing_trades': 0
                }
            
            coin_stats[coin]['total_trades'] += 1
            coin_stats[coin]['total_pnl'] += pnl
            
            if pnl > 0:
                coin_stats[coin]['winning_trades'] += 1
            elif pnl < 0:
                coin_stats[coin]['losing_trades'] += 1
        
        # 计算胜率
        for coin, stats in coin_stats.items():
            total = stats['total_trades']
            stats['win_rate'] = (stats['winning_trades'] / total * 100) if total > 0 else 0
        
        # 按总盈亏排序
        return sorted(coin_stats.values(), key=lambda x: x['total_pnl'], reverse=True)

