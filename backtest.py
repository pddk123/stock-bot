import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [설정값] ---
START_DATE = '2024-01-01'
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000
MAX_POSITIONS = 5
ATR_MULTIPLIER = 2.0
MOMENTUM_CEILING = 9.9  # 천장은 열어두되 '가상 경고 필터'로 제어

def simulate_krx_warning(df, today_idx):
    """KRX 투자경고 기준을 시뮬레이션하여 매수 적격 여부 판단"""
    try:
        if today_idx < 20: return False
        
        subset = df.iloc[:today_idx+1]
        curr_price = subset['Close'].iloc[-1]
        
        # 1. 5일간 60% 이상 상승 여부
        high_5d = subset['Close'].iloc[-6]
        if (curr_price / high_5d) - 1 >= 0.60: return True
        
        # 2. 20일간 100% 이상 상승 여부
        high_20d = subset['Close'].iloc[-21]
        if (curr_price / high_20d) - 1 >= 1.00: return True
        
        return False
    except: return True # 에러 발생 시 보수적으로 '경고'라 가정

def get_backtest_data(symbol):
    try:
        df = fdr.DataReader(symbol, '2023-06-01', END_DATE)
        if len(df) < 100: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = (pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)).rolling(14).mean()
        
        delta = df['Close'].diff()
        up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        down = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + up/down))
        return df
    except: return None

def run_backtest():
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=15) as ex:
        results = list(ex.map(get_backtest_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p, 'entry_atr': a}}
    history = []
    
    trading_days = kospi.index
    for i in range(len(trading_days)-1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # 1. 매도 로직 (익절/손절)
        to_sell = []
        for s, pos in positions.items():
            df = all_data[s]
            if today not in df.index: continue
            
            curr_p = df.loc[today, 'Close']
            profit = (curr_p / pos['entry_price']) - 1
            stop_p = pos['entry_price'] - (ATR_MULTIPLIER * pos['entry_atr'])
            
            if profit >= 0.10 or curr_p < stop_p:
                sell_p = df.loc[tomorrow, 'Open'] if tomorrow in df.index else curr_p
                cash += (sell_p * pos['qty']) * 0.998 # 세금/수수료
                to_sell.append(s)
        for s in to_sell: del positions[s]

        # 2. 매수 로직
        market_alive = kospi.loc[today, 'Close'] > kospi.loc[today, 'MA20']
        if market_alive and len(positions) < MAX_POSITIONS:
            cands = []
            for s, df in all_data.items():
                if s not in positions and today in df.index:
                    row = df.loc[today]
                    # [핵심] 가상 투자경고 필터 적용
                    is_warning = simulate_krx_warning(df, df.index.get_loc(today))
                    
                    if not is_warning and row['Momentum'] > 0.03 and row['RSI'] < 75:
                        if row['Close'] > row['MA20']:
                            cands.append((s, row['Momentum']))
            
            cands.sort(key=lambda x: x[1], reverse=True)
            for s, _ in cands:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[s]
                if tomorrow in df.index:
                    buy_p = df.loc[tomorrow, 'Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * 0.998) // buy_p
                    if qty > 0:
                        positions[s] = {'qty': qty, 'entry_price': buy_p, 'entry_atr': df.loc[today, 'ATR']}
                        cash -= (qty * buy_p) * 1.002

        curr_val = cash + sum([all_data[s].loc[today, 'Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    final_ret = ((history[-1]/INITIAL_CASH)-1)*100
    print(f"📊 Backtest Result (with Virtual Warning Filter)")
    print(f"Final Return: {final_ret:.2f}%")

if __name__ == "__main__":
    run_bot() # 실제 봇과 함께 실행하거나 백테스트만 별도 실행
