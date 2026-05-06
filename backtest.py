import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v6.4 설정값] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000 # 테스트 시작 금액 3천만 원
FEE = 0.002
TARGET_PROFIT = 0.07 
STOP_LOSS = -0.10     # 지수 주의 국면 시 적용
TIME_CUT_DAYS = 30   
MAX_PER_STOCK = 10_000_000 # 한 종목 최대 1,000만 원

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

def get_stock_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 80: return None
        df['MA10'] = df['Close'].rolling(10).mean(); df['MA20'] = df['Close'].rolling(20).mean(); df['MA60'] = df['Close'].rolling(60).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close']); df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v6.4 (Dynamic Allocation & Conditional Stop) 백테스트: {START_DATE} ~ {END_DATE}")
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    top_stocks = fdr.StockListing('KRX').nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH; positions = {}; trade_log = []; history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]; tomorrow = trading_days[i+1]
        
        # 지수 상황 판단
        market_stable = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
        
        # [1] 매도 로직
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_price = df.loc[today]['Close']
            profit_rate = (curr_price / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            
            # 조건부 손절 활성화
            sell_trigger = False
            if profit_rate >= TARGET_PROFIT: sell_trigger = True # 익절
            elif hold_days >= TIME_CUT_DAYS: sell_trigger = True # 타임컷
            elif not market_stable and profit_rate <= STOP_LOSS: sell_trigger = True # 하락장 손절
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_price
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [2] 동적 포지션 계산 (PM's Logic)
        current_total_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        
        # 분할 개수 결정: 3천 이하는 3개, 그 이상은 1천만 원당 1개씩 추가
        num_splits = max(3, math.ceil(current_total_val / 10_000_000))
        buy_unit = min(MAX_PER_STOCK, current_total_val / num_splits)

        # [3] 매수 로직
        if len(positions) < num_splits:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] < 300: continue
                    grade = 'S' if (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 72) else None
                    if grade: candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates[:2]:
                if len(positions) >= num_splits or cash < buy_unit: break
                df = all_data[symbol]
                buy_price = df.loc[tomorrow]['Open']
                qty = (buy_unit * (1 - FEE)) // buy_price
                if qty > 0:
                    positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today}
                    cash -= (qty * buy_price) * (1 + FEE)

        history.append(current_total_val)

    # 결과 리포트
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    mdd = ((pd.Series(history) - pd.Series(history).cummax()) / pd.Series(history).cummax()).min() * 100
    print(f"\n🛡️ v6.4 세이프 스나이퍼 성적표")
    print(f"💰 최종 자산: {history[-1]:,.0f}원 (수익률: {final_return:.2f}%)")
    print(f"🎯 승률: {(len([r for r in trade_log if r > 0])/len(trade_log)*100):.2f}% | MDD: {mdd:.2f}% | 매매: {len(trade_log)}회")

if __name__ == "__main__":
    run_backtest()
