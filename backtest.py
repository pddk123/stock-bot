import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.3 설정값: 어댑티브 스윙 모드] ---
START_DATE = '2023-01-01' 
END_DATE = '2026-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002
TARGET_PROFIT = 0.10      # 수익을 10%로 높여 추세를 더 길게 추종
ATR_MULTIPLIER = 2.0      # 손절선을 ATR의 2배로 넓혀 노이즈 방어
TIME_CUT_DAYS = 15        # 타임컷 15일 (약 3주)로 여유 확보
MOMENTUM_THRESHOLD = 0.03 # 지수 대비 3% 이상 강한 종목 진입
RSI_OVERHEAT = 75         # RSI 75 이상은 과열로 판단하여 진입 금지

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

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
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000 # 억 단위
        
        return df.loc[START_DATE:]
    except:
        return None

def run_backtest():
    print(f"🚀 v8.3 어댑티브 퀀트 엔진 가동: {START_DATE} ~ {END_DATE}")
    
    # [1] 지수 및 종목 데이터 준비
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    kospi['Momentum'] = kospi['Close'].pct_change(20)
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    print("📦 전 종목 데이터 분석 중...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {qty, entry_price, entry_date, entry_atr, max_profit}}
    trade_log = []
    history = []
    trading_days = kospi.index

    # [2] 시뮬레이션 루프
    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # 시장 추세 필터 (단기 이평선 기준)
        market_is_alive = kospi.loc[today]['Close'] > kospi.loc[today]['MA20']
        idx_ret = kospi.loc[today]['Momentum']
        
        # --- 매도 로직 ---
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr = df.loc[today]
            profit_rate = (curr['Close'] / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            pos['max_profit'] = max(pos.get('max_profit', 0), profit_rate)
            
            # ATR 손절가 (2.0배로 넓혀서 변동성 수용)
            stop_price = pos['entry_price'] - (ATR_MULTIPLIER * pos['entry_atr'])
            
            # 본전 보존 (3% 수익 달성 후에는 원금 절대 사수)
            if pos['max_profit'] >= 0.03:
                stop_price = max(stop_price, pos['entry_price'])

            sell_trigger = False
            if profit_rate >= TARGET_PROFIT: sell_trigger = True
            elif curr['Close'] < stop_price: sell_trigger = True
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: sell_trigger = True
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                if sell_price > 0: # 데이터 무결성 체크
                    pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                    trade_log.append(pnl)
                    cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # --- 매수 로직 ---
        if market_is_alive and len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    # 1. 지수보다 강하고 2. 정배열이며 3. 과열(RSI)되지 않은 종목
                    if row['Amount'] >= 300 and row['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD):
                        if row['Close'] > row['MA20'] and row['RSI'] < RSI_OVERHEAT:
                            candidates.append((symbol, row['Momentum']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    if buy_price <= 0 or pd.isna(buy_price): continue
                    
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {
                            'qty': qty, 'entry_price': buy_price, 
                            'entry_date': today, 'entry_atr': df.loc[today]['ATR'],
                            'max_profit': 0
                        }
                        cash -= (qty * buy_price) * (1 + FEE)

        # 자산 기록
        curr_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    # [3] 결과 보고서
    if not history: return
    history_ser = pd.Series(history)
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"\n✨ v8.3 어댑티브 퀀트 결과 보고서")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 최종 금액: {history[-1]:,.0f}원 (수익률: {((history[-1]/INITIAL_CASH)-1)*100:.2f}%)")
    print(f"🎯 승률: {win_rate:.2f}% | MDD: {mdd:.2f}%")
    print(f"🔄 매매 횟수: {len(trade_log)}회")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
