import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import os
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.8 백테스트 설정값] ---
START_DATE = '2024-01-01'
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10      # 10% 익절
ATR_MULTIPLIER = 2.0      # 손절선
TIME_CUT_DAYS = 40        # [중요] 한 달(20거래일) 타임컷
RSI_OVERHEAT = 75         # 과열 기준
MOMENTUM_GAP = 0.03       # 지수 대비 최소 초과 수익

# --- [Helper Functions] ---

def simulate_krx_warning(df, today_idx):
    """KRX 투자경고 기준 시뮬레이션 (단기/중기 폭등 필터)"""
    try:
        if today_idx < 20: return False
        curr_price = df['Close'].iloc[today_idx]
        # 5일간 60% 이상 상승 시 경고
        if (curr_price / df['Close'].iloc[today_idx-5]) - 1 >= 0.60: return True
        # 20일간 100% 이상 상승 시 경고
        if (curr_price / df['Close'].iloc[today_idx-20]) - 1 >= 1.00: return True
        return False
    except: return False

def get_backtest_data(symbol):
    """백테스트용 데이터 수집 및 지표 계산"""
    try:
        # 지표 계산을 위해 시작일보다 150일 전부터 데이터 로드
        df = fdr.DataReader(symbol, '2023-06-01', END_DATE)
        if len(df) < 100: return None
        
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        
        # ATR 계산
        tr = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        
        # RSI 계산
        delta = df['Close'].diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        down = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + up/down))
        
        return df
    except: return None

def get_energy_status(df, today_idx):
    """4단계 에너지 센서 (v8.8)"""
    try:
        curr = df.iloc[today_idx]
        prev_10d = df.iloc[today_idx-10:today_idx]
        price_high_10d = prev_10d['Close'].max()
        rsi_high_10d = prev_10d['RSI'].max()
        
        if curr['RSI'] > RSI_OVERHEAT: return "OVERHEATED"
        if curr['Close'] > price_high_10d and curr['RSI'] > rsi_high_10d: return "ACCELERATING"
        if curr['Close'] > price_high_10d and curr['RSI'] < rsi_high_10d: return "EXHAUSTED"
        return "STABLE"
    except: return "STABLE"

def run_backtest():
    print(f"## 🚀 v8.8 Hyper-Drive Backtest Report")
    print(f"**Period:** {START_DATE} ~ {END_DATE}")
    print(f"**Strategy:** Momentum + Warning Filter + Energy Sensor + Time-cut\n")
    
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    print("...데이터 로딩 및 지표 계산 중 (ThreadPoolExecutor)...")
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(get_backtest_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'price': p, 'atr': a, 'date': d}}
    history = []
    trade_log = []
    
    trading_days = kospi.index
    for i in range(len(trading_days)-1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # 1. 매도 로직
        to_sell = []
        for s, pos in positions.items():
            df = all_data[s]
            if today not in df.index: continue
            
            curr_row = df.loc[today]
            profit = (curr_row['Close'] / pos['price']) - 1
            hold_days = (today - pos['date']).days
            
            # 손절가 (ATR 기반)
            stop_price = pos['price'] - (ATR_MULTIPLIER * pos['atr'])
            
            # 익절(10%), 손절(ATR), 타임컷(20일)
            sell_trigger = False
            if profit >= TARGET_PROFIT: sell_trigger = True
            elif curr_row['Close'] < stop_price: sell_trigger = True
            elif hold_days >= TIME_CUT_DAYS: sell_trigger = True
            
            if sell_trigger:
                sell_p = df.loc[tomorrow, 'Open'] if tomorrow in df.index else curr_row['Close']
                pnl = (sell_p / pos['price'] - 1) * 100 - 0.4 # 수수료/세금 왕복 약 0.4%
                trade_log.append(pnl)
                cash += (sell_p * pos['qty']) * 0.998
                to_sell.append(s)
        for s in to_sell: del positions[s]

        # 2. 매수 로직
        market_alive = kospi.loc[today, 'Close'] > kospi.loc[today, 'MA20']
        idx_ret = kospi['Close'].pct_change(20).loc[today]
        
        if market_alive and len(positions) < MAX_POSITIONS:
            candidates = []
            for s, df in all_data.items():
                if s not in positions and today in df.index:
                    t_idx = df.index.get_loc(today)
                    row = df.iloc[t_idx]
                    
                    # [v8.8 필터링] 가상 경고 필터 + 모멘텀 + 에너지 센서
                    is_warning = simulate_krx_warning(df, t_idx)
                    energy = get_energy_status(df, t_idx)
                    
                    if not is_warning and row['Momentum'] > (idx_ret + MOMENTUM_GAP):
                        if row['RSI'] < RSI_OVERHEAT and row['Close'] > row['MA20']:
                            # 가속 중이거나 스테이블인 것만 매수
                            if energy in ["ACCELERATING", "STABLE"]:
                                candidates.append((s, row['Momentum']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for s, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[s]
                if tomorrow in df.index:
                    buy_p = df.loc[tomorrow, 'Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * 0.998) // buy_p
                    if qty > 0:
                        positions[s] = {'qty': qty, 'price': buy_p, 'atr': df.loc[today, 'ATR'], 'date': today}
                        cash -= (qty * buy_p) * 1.002

        curr_val = cash + sum([all_data[s].loc[today, 'Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    # 결과 분석
    history_ser = pd.Series(history)
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"### 📊 Performance Summary")
    print(f"| Metric | Value |")
    print(f"| :--- | :--- |")
    print(f"| **Total Return** | **{final_return:.2f}%** |")
    print(f"| **Win Rate** | {win_rate:.2f}% |")
    print(f"| **Max Drawdown (MDD)** | **{mdd:.2f}%** |")
    print(f"| **Total Trades** | {len(trade_log)} |")

if __name__ == "__main__":
    run_backtest() # [Fix] 함수명 수정 완료
