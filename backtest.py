import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.2 설정값: 세이프티 퀀트] ---
START_DATE = '2025-01-01' 
END_DATE = '2026-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002
TARGET_PROFIT = 0.08 
TIME_CUT_DAYS = 10   
MOMENTUM_THRESHOLD = 0.05

def calculate_atr(df, n=14):
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_stock_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 100: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = calculate_atr(df)
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v8.2 세이프티 퀀트 실행: {START_DATE} ~ {END_DATE}")
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA60'] = kospi['Close'].rolling(60).mean() # 장기 추세 필터
    kospi['Momentum'] = kospi['Close'].pct_change(20)
    
    all_data = {}
    top_stocks = fdr.StockListing('KRX').nlargest(250, 'Marcap')['Code'].tolist()
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH; positions = {}; trade_log = []; history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]; tomorrow = trading_days[i+1]
        
        # [Safety 1] 지수 필터: 코스피가 60일선 아래면 하락장으로 판단
        market_is_cold = kospi.loc[today]['Close'] < kospi.loc[today]['MA60']
        
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            curr = df.loc[today]
            
            # [Safety 2] 0으로 나누기 방지
            entry_p = pos['entry_price'] if pos['entry_price'] > 0 else 1
            profit_rate = (curr['Close'] / entry_p) - 1
            hold_days = (today - pos['entry_date']).days
            pos['max_profit'] = max(pos.get('max_profit', 0), profit_rate)
            
            # ATR 손절 (1.2배로 강화)
            stop_price = pos['entry_price'] - (1.2 * pos['entry_atr'])
            if pos['max_profit'] >= 0.03: stop_price = max(stop_price, pos['entry_price'])

            sell_trigger = False
            if profit_rate >= TARGET_PROFIT: sell_trigger = True
            elif curr['Close'] < stop_price: sell_trigger = True
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: sell_trigger = True
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                if sell_price > 0: # 유효한 가격일 때만 정산
                    pnl = (sell_price / entry_p - 1) * 100 - (FEE * 2 * 100)
                    trade_log.append(pnl); cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        for s in to_sell: del positions[s]

        # [Safety 3] 매수 조건: 시장이 추울 때는 현금 관망
        if not market_is_cold and len(positions) < MAX_POSITIONS:
            candidates = []
            idx_ret = kospi.loc[today]['Momentum']
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    if row['Amount'] >= 300 and row['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD):
                        if row['Close'] > row['MA20']: candidates.append((symbol, row['Momentum']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    # [Safety 4] 비정상 데이터 스킵
                    if buy_price <= 0 or pd.isna(buy_price): continue
                    
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today, 'entry_atr': df.loc[today]['ATR'], 'max_profit': 0}
                        cash -= (qty * buy_price) * (1 + FEE)

        curr_total = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_total)

    # 결과 리포트
    history_ser = pd.Series(history)
    final_val = history[-1] if not pd.isna(history[-1]) else 0
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    print(f"\n🛡️ v8.2 세이프티 퀀트 성적표")
    print(f"💰 수익률: {((final_val/INITIAL_CASH)-1)*100:.2f}% | 승률: {win_rate:.2f}% | MDD: {mdd:.2f}% | 매매: {len(trade_log)}회")

if __name__ == "__main__":
    run_backtest()
