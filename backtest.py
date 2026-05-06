# (핵심 변경 부분 위주로 업데이트)

# --- [v8.1 설정값: 슬로우 스윙 모드] ---
TIME_CUT_DAYS = 10      # 5일에서 10일로 연장 (매매 횟수 감소)
TARGET_PROFIT = 0.08    # 수익률을 조금 더 열어둠 (8%)
MOMENTUM_THRESHOLD = 0.05 # 지수 대비 5% 이상 강한 종목만 진입

# ... (데이터 로직 생략) ...

for i in range(len(trading_days) - 1):
    today = trading_days[i]
    tomorrow = trading_days[i+1]
    
    # 지수 수익률 계산 (상대 모멘텀용)
    idx_return = kospi.loc[today]['Close'] / kospi.iloc[max(0, i-20)]['Close'] - 1

    # [1] 매도 로직 (v8.1 버전)
    to_sell = []
    for symbol, pos in positions.items():
        curr = all_data[symbol].loc[today]
        profit_rate = (curr['Close'] / pos['entry_price']) - 1
        hold_days = (today - pos['entry_date']).days
        
        # 동적 손절가 (ATR 기반)
        stop_price = pos['entry_price'] - (1.5 * pos['entry_atr'])
        
        # 본전 보존 (수익이 3% 이상 났었다면 손절가를 본전으로 상향)
        if profit_rate >= 0.03:
            stop_price = max(stop_price, pos['entry_price'])

        sell_trigger = False
        if profit_rate >= TARGET_PROFIT: sell_trigger = True
        elif curr['Close'] < stop_price: sell_trigger = True
        elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: sell_trigger = True # 10일 지났는데 빌빌대면 매도
        
        if sell_trigger:
            # ... 매도 처리 ...

    # [2] 매수 로직 (진입 장벽 상향)
    if len(positions) < MAX_POSITIONS:
        candidates = []
        for symbol, df in all_data.items():
            if symbol not in positions and today in df.index:
                row = df.loc[today]
                # 상대적 모멘텀: 지수 수익률 + 5% 이상인 종목만
                if row['Amount'] >= 300 and row['Momentum'] > (idx_return + MOMENTUM_THRESHOLD):
                    if row['Close'] > row['MA20']:
                        candidates.append((symbol, row['Momentum']))
        
        # ... 상위 종목 매수 ...
