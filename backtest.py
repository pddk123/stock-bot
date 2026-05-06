import pandas as pd
import FinanceDataReader as fdr
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v7.0 설정값: 무제한 보유 모드] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000 # 1,000만 원 시작
MAX_POSITIONS = 5
FEE = 0.002
TARGET_PROFIT = 0.10      # 10% 익절 목표

def calculate_rsi_wilder(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

def get_stock_data(symbol):
    try:
        # 데이터는 분석을 위해 시작일 120일 전부터 가져옴
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 80: return None
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['RSI'] = calculate_rsi_wilder(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        return df.loc[START_DATE:]
    except: return None

def run_backtest():
    print(f"🚀 v7.0 (10% Target & Infinite Hold) 백테스트: {START_DATE} ~ {END_DATE}")
    
    # 지수 데이터 로드 (매수 시점 판단용)
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    
    # 시총 상위 250종목 (우량주 유니버스)
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty': n, 'entry_price': p, 'entry_date': d}}
    trade_log = []
    history = []
    trading_days = kospi.index

    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # [1] 매도 로직 (오직 10% 익절만 존재)
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr_price = df.loc[today]['Close']
            profit_rate = (curr_price / pos['entry_price']) - 1
            
            # 목표 수익 10% 달성 시에만 매도
            if profit_rate >= TARGET_PROFIT:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr_price
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl)
                cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # [2] 매수 로직 (빈 슬롯이 있으면 즉시 채움)
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    # 스마트 피킹 S급 조건 (Amount 300억 이상)
                    if row['Amount'] >= 300 and (row['Close'] > row['MA10'] > row['MA20']) and (45 <= row['RSI'] <= 72):
                        candidates.append((symbol, row['Vol_Ratio']))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    # 남은 현금을 빈 슬롯 수로 나누어 투자 (한도 없음)
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {'qty': qty, 'entry_price': buy_price, 'entry_date': today}
                        cash -= (qty * buy_price) * (1 + FEE)

        # [3] 자산 기록 (보유 종목의 현재가 가치 합산)
        current_val = cash
        for symbol, pos in positions.items():
            price = all_data[symbol].loc[today]['Close'] if today in all_data[symbol].index else pos['entry_price']
            current_val += price * pos['qty']
        history.append(current_val)

    # [4] 최종 결과 도출
    if not history: return
    history_ser = pd.Series(history)
    final_val = history[-1]
    total_return = ((final_val / INITIAL_CASH) - 1) * 100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"\n🏆 v7.0 가치 투자 스나이퍼 결과 보고서")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 시작 금액: {INITIAL_CASH:,.0f}원")
    print(f"💰 최종 금액: {final_val:,.0f}원")
    print(f"📈 누적 수익률: {total_return:.2f}%")
    print(f"🎯 매도 성공률: {win_rate:.2f}%")
    print(f"📉 MDD (최대 낙폭): {mdd:.2f}%")
    print(f"🔄 매매 완료 횟수: {len(trade_log)}회")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
