import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.9-Alpha 설정값] ---
START_DATE = '2024-01-01'
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10
ATR_MULTIPLIER = 2.0
TIME_CUT_DAYS = 40
TRAILING_THRESHOLD = 0.03  # 3% 이상 수익 시 본전 사수 모드 발동
RSI_OVERHEAT = 75
MOMENTUM_GAP = 0.03

logging.basicConfig(level=logging.INFO)

def simulate_krx_warning(df, today_idx):
    try:
        if today_idx < 20: return False
        curr_p = df['Close'].iloc[today_idx]
        if (curr_p / df['Close'].iloc[today_idx-5]) - 1 >= 0.60: return True
        if (curr_p / df['Close'].iloc[today_idx-20]) - 1 >= 1.00: return True
        return False
    except: return False

def get_backtest_data(symbol):
    try:
        df = fdr.DataReader(symbol, '2023-06-01', END_DATE)
        if len(df) < 100: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        tr = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        delta = df['Close'].diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        down = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + up/down))
        return df
    except: return None

def get_energy_status(df, today_idx):
    try:
        curr = df.iloc[today_idx]
        prev_10d = df.iloc[today_idx-10:today_idx]
        if curr['RSI'] > RSI_OVERHEAT: return "OVERHEATED"
        if curr['Close'] > prev_10d['Close'].max() and curr['RSI'] > prev_10d['RSI'].max(): return "ACCELERATING"
        if curr['Close'] > prev_10d['Close'].max() and curr['RSI'] < prev_10d['RSI'].max(): return "EXHAUSTED"
        return "STABLE"
    except: return "STABLE"

def run_backtest():
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(get_backtest_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {qty, price, atr, date, max_profit}}
    history = []
    trade_log = []
    
    trading_days = kospi.index
    for i in range(len(trading_days)-1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        to_sell = []
        for s, pos in positions.items():
            df = all_data[s]
            if today not in df.index: continue
            
            row = df.loc[today]
            profit = (row['Close'] / pos['price']) - 1
            hold_days = (today - pos['date']).days
            
            # [핵심 로직] 최고 수익률 업데이트
            pos['max_profit'] = max(pos.get('max_profit', 0), profit)
            
            # 매도선 결정
            atr_stop = pos['price'] - (ATR_MULTIPLIER * pos['atr'])
            # 3% 넘겼으면 매수 가격(본전) 이하로는 안 본다!
            trailing_stop = pos['price'] if pos['max_profit'] >= TRAILING_THRESHOLD else -1
            
            stop_price = max(atr_stop, trailing_stop)
            
            sell_trigger = False
            reason = ""
            if profit >= TARGET_PROFIT: 
                sell_trigger = True; reason = "Target"
            elif row['Close'] < stop_price: 
                sell_trigger = True; reason = "Stop"
            elif hold_days >= TIME_CUT_DAYS: 
                sell_trigger = True; reason = "Time"
            
            if sell_trigger:
                sell_p = df.loc[tomorrow, 'Open'] if tomorrow in df.index else row['Close']
                pnl = (sell_p / pos['price'] - 1) * 100 - 0.4
                trade_log.append(pnl)
                cash += (sell_p * pos['qty']) * 0.998
                to_sell.append(s)
        for s in to_sell: del positions[s]

        # 매수 로직
        if (kospi.loc[today, 'Close'] > kospi.loc[today, 'MA20']) and len(positions) < MAX_POSITIONS:
            idx_ret = kospi['Close'].pct_change(20).loc[today]
            cands = []
            for s, df in all_data.items():
                if s not in positions and today in df.index:
                    t_idx = df.index.get_loc(today)
                    r = df.iloc[t_idx]
                    if not simulate_krx_warning(df, t_idx) and r['Momentum'] > (idx_ret + MOMENTUM_GAP):
                        if r['RSI'] < RSI_OVERHEAT and r['Close'] > r['MA20']:
                            if get_energy_status(df, t_idx) in ["ACCELERATING", "STABLE"]:
                                cands.append((s, r['Momentum']))
            
            cands.sort(key=lambda x: x[1], reverse=True)
            for s, _ in cands:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[s]
                if tomorrow in df.index:
                    buy_p = df.loc[tomorrow, 'Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * 0.998) // buy_p
                    if qty > 0:
                        positions[s] = {'qty': qty, 'price': buy_p, 'atr': df.loc[today, 'ATR'], 'date': today, 'max_profit': 0}
                        cash -= (qty * buy_p) * 1.002

        curr_val = cash + sum([all_data[s].loc[today, 'Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    # 결과 분석
    history_ser = pd.Series(history)
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    print(f"\n📊 [v8.9 Alpha] Backtest Result")
    print(f"Total Return: {final_return:.2f}%")
    print(f"MDD: {mdd:.2f}%")
    print(f"Total Trades: {len(trade_log)}")

if __name__ == "__main__":
    run_backtest()
