"""编排层，负责收集数据、调用 AI 并交给执行层落地。"""
import json
from typing import Dict

import config
from services.execution_service import ExecutionService
from utils.timezone import get_current_beijing_time_str


class TradingEngine:
    """Thin orchestration layer: market state -> AI decision -> execution service."""

    def __init__(self, model_id: int, db, market_fetcher, ai_trader):
        self.model_id = model_id
        self.db = db
        self.market_fetcher = market_fetcher
        self.ai_trader = ai_trader
        self.coins = config.SUPPORTED_COINS
        self.trading_mode = config.TRADING_MODE
        self._cycle_logs = []
        self.execution_service = ExecutionService(
            model_id=model_id,
            db=db,
            debug_log=self._debug_log
        )
        print(f"[INFO] 模型 {model_id} 已初始化，模式：{self.trading_mode}")

    def _debug_log(self, message: str) -> None:
        self._cycle_logs.append(message)

    def _flush_cycle_logs(self) -> None:
        if not self._cycle_logs:
            return

        print(f"[DEBUG] 模型 {self.model_id} 本轮摘要（{len(self._cycle_logs)} 条）")
        for item in self._cycle_logs:
            print(f"  {item}")
        self._cycle_logs = []

    def execute_trading_cycle(self) -> Dict:
        try:
            self._cycle_logs = []
            market_state = self._get_market_state()
            current_prices = {coin: market_state[coin]['price'] for coin in market_state}

            portfolio = self.execution_service.get_portfolio(current_prices)
            stop_results = self.execution_service.check_stop_loss_take_profit(portfolio, current_prices)

            account_info = self._build_account_info(portfolio)
            decisions, raw_response = self.ai_trader.make_decision(
                market_state, portfolio, account_info
            )

            if decisions:
                for coin, decision in decisions.items():
                    self._debug_log(f'决策 {coin}: {decision}')

            if decisions and len(decisions) > 0:
                self.db.add_conversation(
                    self.model_id,
                    user_prompt=self._format_prompt(market_state, portfolio, account_info),
                    ai_response=json.dumps(decisions, ensure_ascii=False),
                    cot_trace=raw_response[:2000] if raw_response else ''
                )
                print(f'[INFO] 模型 {self.model_id} AI 决策已存储，包含 ({len(decisions)} 个币种)')
            else:
                print(f'[WARN] 模型 {self.model_id} AI 决策为空，跳过对话记录存储')

            execution_results = self.execution_service.execute_decisions(decisions, market_state, portfolio)
            all_results = stop_results + execution_results

            self.execution_service.record_account_value(portfolio)
            updated_portfolio = self.execution_service.get_portfolio(current_prices)

            return {
                'success': True,
                'decisions': decisions,
                'executions': all_results,
                'portfolio': updated_portfolio
            }
        except Exception as e:
            self._flush_cycle_logs()
            print(f"[ERROR] Trading cycle failed (Model {self.model_id}): {e}")
            import traceback
            print(traceback.format_exc())
            return {'success': False, 'error': str(e)}
        finally:
            self._flush_cycle_logs()

    def _get_market_state(self) -> Dict:
        market_state = {}
        prices = self.market_fetcher.get_current_prices(self.coins)
        for coin in self.coins:
            if coin in prices:
                market_state[coin] = prices[coin].copy()
                timeframe_indicators = self.market_fetcher.get_multi_timeframe_indicators(coin)
                market_state[coin]['timeframes'] = timeframe_indicators
                market_state[coin]['indicators'] = timeframe_indicators.get('1h', {})
        return market_state

    def _build_account_info(self, portfolio: Dict) -> Dict:
        model = self.db.get_model(self.model_id)
        initial_capital = model['initial_capital']
        total_value = portfolio['total_value']
        total_return = ((total_value - initial_capital) / initial_capital) * 100
        return {
            'current_time': get_current_beijing_time_str(),
            'total_return': total_return,
            'initial_capital': initial_capital
        }

    def _format_prompt(self, market_state: Dict, portfolio: Dict, account_info: Dict) -> str:
        return f"Market State: {len(market_state)} coins, Portfolio: {len(portfolio['positions'])} positions"
