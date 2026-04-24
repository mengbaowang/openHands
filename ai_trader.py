import json
from typing import Dict
from openai import OpenAI, APIConnectionError, APIError

class AITrader:
    def __init__(self, api_key: str, api_url: str, model_name: str, system_prompt: str = None):
        self.api_key = api_key
        self.api_url = api_url
        self.model_name = model_name
        self.system_prompt = system_prompt or self._get_default_prompt()
    
    def make_decision(self, market_state: Dict, portfolio: Dict,
                     account_info: Dict) -> tuple:
        """
        做出交易决策
        返回: (decisions: Dict, raw_response: str) 元组
        """
        prompt = self._build_prompt(market_state, portfolio, account_info)

        # 添加重试机制（最多3次）
        max_retries = 3
        last_raw_response = ''

        for attempt in range(max_retries):
            try:
                response = self._call_llm(prompt)
                last_raw_response = response  # 保存原始响应
                decisions = self._parse_response(response)

                # 如果解析成功且不为空，返回结果
                if decisions and len(decisions) > 0:
                    return decisions, response

                # 如果是最后一次尝试，返回空字典
                if attempt == max_retries - 1:
                    print(f'[ERROR] AI decision failed after {max_retries} attempts')
                    return {}, last_raw_response

                # 否则重试
                print(f'[WARN] AI returned empty decision, retrying ({attempt + 1}/{max_retries})...')

            except Exception as e:
                print(f'[ERROR] AI call failed (attempt {attempt + 1}/{max_retries}): {e}')
                if attempt == max_retries - 1:
                    return {}, last_raw_response

        return {}, last_raw_response
    
    def _build_prompt(self, market_state: Dict, portfolio: Dict,
                     account_info: Dict) -> str:
        # 使用自定义的system_prompt
        prompt = f"""{self.system_prompt}

MARKET DATA:
"""
        for coin, data in market_state.items():
            prompt += f"{coin}: ${data['price']:.2f} ({data['change_24h']:+.2f}%)\n"
            if 'indicators' in data and data['indicators']:
                indicators = data['indicators']
                prompt += f"  SMA7: ${indicators.get('sma_7', 0):.2f}, SMA14: ${indicators.get('sma_14', 0):.2f}, RSI: {indicators.get('rsi_14', 0):.1f}\n"
        
        prompt += f"""
ACCOUNT STATUS:
- Initial Capital: ${account_info['initial_capital']:.2f}
- Total Value: ${portfolio['total_value']:.2f}
- Cash: ${portfolio['cash']:.2f}
- Total Return: {account_info['total_return']:.2f}%

CURRENT POSITIONS:
"""
        if portfolio['positions']:
            for pos in portfolio['positions']:
                prompt += f"- {pos['coin']} {pos['side']}: {pos['quantity']:.4f} @ ${pos['avg_price']:.2f} ({pos['leverage']}x)\n"
        else:
            prompt += "None\n"

        prompt += """
OUTPUT FORMAT (JSON only):
```json
{
  "COIN": {
   "signal": "buy_to_enter|sell_to_enter|sell_to_close|buy_to_close|hold",
   "quantity": 0.5,
    "leverage": 10,
    "profit_target": 45000.0,
    "stop_loss": 42000.0,
    "confidence": 0.75,
    "reasoning": {
      "market_analysis": "Detailed market trend analysis",
      "technical_signals": "Key technical indicators analysis",
      "risk_assessment": "Risk evaluation",
      "decision_rationale": "Why this decision was made"
    },
    "justification": "Brief summary"
  }
}
```

Provide detailed reasoning for each decision. Analyze and output JSON only.
"""
        
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        try:
            base_url = self.api_url.rstrip('/')
            if not base_url.endswith('/v1'):
                if '/v1' in base_url:
                    base_url = base_url.split('/v1')[0] + '/v1'
                else:
                    base_url = base_url + '/v1'
            
            client = OpenAI(
                api_key=self.api_key,
                base_url=base_url
            )
            
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional cryptocurrency trader. Output JSON format only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=2000
            )
            
            return response.choices[0].message.content
            
        except APIConnectionError as e:
            error_msg = f"API connection failed: {str(e)}"
            print(f"[ERROR] {error_msg}")
            raise Exception(error_msg)
        except APIError as e:
            error_msg = f"API error ({e.status_code}): {e.message}"
            print(f"[ERROR] {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"LLM call failed: {str(e)}"
            print(f"[ERROR] {error_msg}")
            import traceback
            print(traceback.format_exc())
            raise Exception(error_msg)
    
    # def _parse_response(self, response: str) -> Dict:
    #     """智能解析AI响应，支持多种格式"""
    #     if not response or not response.strip():
    #         print('[WARN] Empty AI response')
    #         return {}

    #     original_response = response
    #     response = response.strip()

    #     # 记录原始响应（用于调试）
    #     print(f'[DEBUG] AI raw response length: {len(response)} chars')

    #     # 策略1: 提取思维链标签中的内容（如果存在）
    #     if '<think>' in response and '</think>' in response:
    #         # 提取思维链之后的内容
    #         parts = response.split('</think>')
    #         if len(parts) > 1:
    #             response = parts[1].strip()
    #             print('[DEBUG] Extracted content after </think> tag')

    #     # 策略2: 提取Markdown代码块中的JSON
    #     json_content = None
    #     if '```json' in response:
    #         try:
    #             json_content = response.split('```json')[1].split('```')[0].strip()
    #             print('[DEBUG] Extracted JSON from ```json block')
    #         except IndexError:
    #             pass
    #     elif '```' in response:
    #         try:
    #             json_content = response.split('```')[1].split('```')[0].strip()
    #             print('[DEBUG] Extracted content from ``` block')
    #         except IndexError:
    #             pass

    #     if json_content:
    #         response = json_content

    #     # 策略3: 尝试直接解析JSON
    #     try:
    #         decisions = json.loads(response)
    #         if isinstance(decisions, dict) and len(decisions) > 0:
    #             print(f'[SUCCESS] Parsed JSON with {len(decisions)} keys')
    #             return decisions
    #     except json.JSONDecodeError:
    #         pass

    #     # 策略4: 使用正则表达式提取JSON对象
    #     import re
    #     json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    #     matches = re.findall(json_pattern, response, re.DOTALL)

    #     for match in matches:
    #         try:
    #             decisions = json.loads(match)
    #             if isinstance(decisions, dict) and len(decisions) > 0:
    #                 print(f'[SUCCESS] Extracted JSON via regex with {len(decisions)} keys')
    #                 return decisions
    #         except json.JSONDecodeError:
    #             continue

    #     # 策略5: 智能文本解析 - 提取关键信息
    #     print('[WARN] JSON parsing failed, attempting intelligent text extraction')
    #     decisions = self._extract_from_text(original_response)

    #     if decisions and len(decisions) > 0:
    #         print(f'[SUCCESS] Extracted decisions from text: {list(decisions.keys())}')
    #         return decisions

    #     # 所有策略都失败
    #     print('[ERROR] All parsing strategies failed')
    #     print(f'[DATA] Response preview: {original_response[:500]}...')
    #     return {}

    # def _extract_from_text(self, text: str) -> Dict:
    #     """从纯文本中智能提取交易决策"""
    #     import re

    #     decisions = {}

    #     # 支持的币种列表
    #     coins = ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'XRP']

    #     # 信号关键词映射
    #     signal_keywords = {
    #         'buy': ['buy', '买入', 'long', '做多', 'enter long', 'buy_to_enter'],
    #         'sell': ['sell', '卖出', 'short', '做空', 'enter short', 'sell_to_enter'],
    #         'hold': ['hold', '持有', '观望', 'wait', 'no action'],
    #         'close': ['close', '平仓', 'exit', 'take profit', 'stop loss']
    #     }

    #     text_lower = text.lower()

    #     # 遍历每个币种，尝试提取决策
    #     for coin in coins:
    #         coin_lower = coin.lower()

    #         # 检查文本中是否提到这个币种
    #         if coin_lower not in text_lower and coin not in text:
    #             continue

    #         # 提取该币种相关的段落
    #         coin_section = self._extract_coin_section(text, coin)
    #         if not coin_section:
    #             continue

    #         coin_section_lower = coin_section.lower()

    #         # 识别信号
    #         signal = 'hold'
    #         for sig, keywords in signal_keywords.items():
    #             for keyword in keywords:
    #                 if keyword in coin_section_lower:
    #                     signal = sig
    #                     break
    #             if signal != 'hold':
    #                 break

    #         # 映射到标准信号
    #         signal_map = {
    #             'buy': 'buy_to_enter',
    #             'sell': 'sell_to_enter',
    #             'hold': 'hold',
    #             'close': 'close_position'
    #         }
    #         signal = signal_map.get(signal, 'hold')

    #         # 提取数量（如果有）
    #         quantity = self._extract_number(coin_section, r'quantity[:\s]+([0-9.]+)', 0.5)

    #         # 提取杠杆（如果有）
    #         leverage = int(self._extract_number(coin_section, r'leverage[:\s]+([0-9]+)', 10))

    #         # 提取目标价格
    #         profit_target = self._extract_number(coin_section, r'(?:profit.?target|target.?price)[:\s]+\$?([0-9.]+)', 0)

    #         # 提取止损价格
    #         stop_loss = self._extract_number(coin_section, r'(?:stop.?loss|stop)[:\s]+\$?([0-9.]+)', 0)

    #         # 提取信心指数
    #         confidence = self._extract_number(coin_section, r'confidence[:\s]+([0-9.]+)', 0.5)
    #         if confidence > 1:
    #             confidence = confidence / 100  # 转换百分比

    #         # 提取理由
    #         reasoning = self._extract_reasoning(coin_section)

    #         # 构建决策对象
    #         decisions[coin] = {
    #             'signal': signal,
    #             'quantity': quantity,
    #             'leverage': leverage,
    #             'profit_target': profit_target,
    #             'stop_loss': stop_loss,
    #             'confidence': confidence,
    #             'reasoning': reasoning,
    #             'justification': f'Extracted from text analysis for {coin}'
    #         }

    #     return decisions
    def _parse_response(self, response: str) -> Dict:
        """智能解析AI响应，支持多种格式"""
        if not response or not response.strip():
            print('[WARN] Empty AI response')
            return {}
        
        original_response = response
        response = response.strip()
        
        # 记录原始响应（用于调试）
        print(f'[DEBUG] AI raw response length: {len(response)} chars')
        
        # # 策略1: 提取思维链标签中的内容（如果存在）
        # if '' in response and '' in response:
        #     # 提取思维链之后的内容
        #     parts = response.split('</thought>')
        #     if len(parts) > 1:
        #         response = parts[1].strip()
        #         print('[DEBUG] Extracted content after tag')
        
        # 策略2: 提取Markdown代码块中的JSON
        json_content = None
        if '```json' in response:
            try:
                json_content = response.split('```json')[1].split('```')[0].strip()
                print('[DEBUG] 从```json```块提取的JSON内容')
            except IndexError:
                pass
        elif '```' in response:
            try:
                json_content = response.split('```')[1].split('```')[0].strip()
                print('[DEBUG] ```块中的内容')
            except IndexError:
                pass
        
        if json_content:
            response = json_content
        
        # 策略3: 尝试直接解析JSON
        try:
            decisions = json.loads(response)
            if isinstance(decisions, dict) and len(decisions) > 0:
                print(f'[SUCCESS] 解析后的决策数量: {len(decisions)} 个')
                # ===== 新增：格式转换 =====
                decisions = self._normalize_decision_format(decisions)
                # ========================
                return decisions
        except json.JSONDecodeError:
            pass
        
        # 策略4: 使用正则表达式提取JSON对象
        import re
        json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(json_pattern, response, re.DOTALL)
        for match in matches:
            try:
                decisions = json.loads(match)
                if isinstance(decisions, dict) and len(decisions) > 0:
                    print(f'[SUCCESS] 正则表达式提取的决策数量: {len(decisions)} 个')
                    # ===== 新增：格式转换 =====
                    decisions = self._normalize_decision_format(decisions)
                    # ========================
                    return decisions
            except json.JSONDecodeError:
                continue
        
        # 策略5: 智能文本解析 - 提取关键信息
        print('[WARN] 尝试文本解析...')
        decisions = self._extract_from_text(original_response)
        if decisions and len(decisions) > 0:
            print(f'[SUCCESS] 文本解析后的决策数量: {len(decisions)} 个')
            # ===== 新增：格式转换 =====
            decisions = self._normalize_decision_format(decisions)
            # ========================
            return decisions
        
        # 所有策略都失败
        print('[ERROR] 所有解析策略都失败')
        print(f'[DATA] 原始响应预览: {original_response[:500]}...')
        return {}

    def _normalize_decision_format(self, decisions: Dict) -> Dict:
        """标准化决策格式：自动识别并转换扁平格式到币种分组格式"""
        if not decisions or not isinstance(decisions, dict):
            return {}
        
        # 检查是否是扁平格式
        # 扁平格式特征：顶层键是 signal, quantity, leverage 等字段名
        flat_format_keys = {'signal', 'quantity', 'leverage', 'stop_loss', 'profit_target', 'confidence', 'reasoning', 'justification'}
        
        if flat_format_keys & decisions.keys():
            # 是扁平格式：只允许明确指定 coin 时转换，避免“同一决策下到所有币种”的灾难性风险。
            coin = str(decisions.get('coin', '')).upper().strip()
            if coin:
                normalized = {coin: decisions.copy()}
                normalized[coin].pop('coin', None)
                print(f'[WARN] 检测到扁平格式决策，已转换为单币种格式: {coin}')
                return normalized

            print('[ERROR] 检测到扁平格式决策但缺少 coin 字段，已拒绝该决策以避免误下单')
            return {}
        else:
            # 已经是币种分组格式，直接返回
            return decisions
            
    def _extract_coin_section(self, text: str, coin: str) -> str:
        """提取特定币种相关的文本段落"""
        import re

        # 尝试找到币种相关的段落
        patterns = [
            rf'{coin}[:\s]+([^\n]{{50,500}})',  # 币种后的内容
            rf'(?:^|\n)([^\n]*{coin}[^\n]{{50,500}})',  # 包含币种的行
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(0)

        return ''

    def _extract_number(self, text: str, pattern: str, default: float) -> float:
        """从文本中提取数字"""
        import re
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                pass
        return default

    def _extract_reasoning(self, text: str) -> Dict:
        """提取推理信息"""
        import re

        reasoning = {
            'market_analysis': '',
            'technical_signals': '',
            'risk_assessment': '',
            'decision_rationale': ''
        }

        # 尝试提取市场分析
        market_patterns = [
            r'market[:\s]+([^\n.]{20,200})',
            r'analysis[:\s]+([^\n.]{20,200})',
            r'trend[:\s]+([^\n.]{20,200})'
        ]

        for pattern in market_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                reasoning['market_analysis'] = match.group(1).strip()
                break

        # 如果没有找到结构化信息，使用整个文本
        if not reasoning['market_analysis']:
            reasoning['decision_rationale'] = text[:200].strip()

        return reasoning

    def _get_default_prompt(self) -> str:
        """返回默认的交易策略prompt"""
        return """You are a professional cryptocurrency trader. Analyze the market and make trading decisions.

TRADING RULES:
1. Signals: buy_to_enter (long), sell_to_enter (short), close_position, hold
2. Risk Management:
   - Max 3 positions
   - Risk 1-5% per trade
   - Use appropriate leverage (1-20x)
3. Position Sizing:
   - Conservative: 1-2% risk
   - Moderate: 2-4% risk
   - Aggressive: 4-5% risk
4. Exit Strategy:
   - Close losing positions quickly
   - Let winners run
   - Use technical indicators

Provide detailed reasoning for each decision. Analyze and output JSON only."""
