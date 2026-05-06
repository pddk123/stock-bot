import pandas as pd
import FinanceDataReader as fdr
import numpy as np
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.1 설정값: 슬로우 스윙 모드] ---
START_DATE = '2024-01-01' 
END_DATE = '2025-01-01'
INITIAL_CASH = 10_000_000 
MAX_POSITIONS = 5
FEE = 0.002             # 수수료 + 세금 (0.2%)
TARGET_PROFIT = 0.08    # 목표 수익 8%
TIME_CUT_DAYS = 10      # 10거래일(약 2주) 타임컷
MOMENTUM_THRESHOLD = 0.05 # 지수 대비 5% 이상 강한 종목만 진입

def calculate_atr(df, n=14):
    """변동성 지표 ATR 계산 (손절선 산출용)"""
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_stock_data(symbol):
    """데이터 로드 및 보조지표 계산"""
    try:
        # 분석을 위해 시작일 120일 전부터 데이터 로드
        df = fdr.DataReader(symbol, (datetime.strptime(START_DATE, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d'), END_DATE)
        if len(df) < 100: return None
        
        df['MA20'] = df['Close'].rolling(20).mean()
        # 상대적 모멘텀: 최근 20거래일(1개월) 수익률
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = calculate_atr(df)
        # 거래대금 (억 단위)
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        # 이격도 및 거래량 비율 (필터용)
        df['Vol_Ratio'] = df['Volume'] / df['Volume'].shift(1).rolling(5).mean()
        
        return df.loc[START_DATE:]
    except:
        return None

def run_backtest():
    print(f"🚀 v8.1 슬로우 퀀트 엔진 백테스트 실행: {START_DATE} ~ {END_DATE}")
    
    # [1] 기본 데이터 준비
    kospi = fdr.DataReader('KS11', START_DATE, END_DATE)
    kospi['Momentum'] = kospi['Close'].pct_change(20) # 지수 모멘텀
    
    krx = fdr.StockListing('KRX')
    top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist() # 시총 상위 250개
    
    all_data = {}
    print("📦 종목 데이터 로딩 중...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(get_stock_data, top_stocks))
        for code, data in zip(top_stocks, results):
            if data is not None: all_data[code] = data

    # [2] 백테스트 변수 초기화
    cash = INITIAL_CASH
    positions = {} # {symbol: {'qty', 'entry_price', 'entry_date', 'entry_atr', 'max_profit'}}
    trade_log = []
    history = []
    trading_days = kospi.index

    # [3] 시뮬레이션 루프
    for i in range(len(trading_days) - 1):
        today = trading_days[i]
        tomorrow = trading_days[i+1]
        
        # 지수 수익률 (오늘 기준 최근 1개월)
        idx_ret = kospi.loc[today]['Momentum']
        
        # --- 매도 로직 ---
        to_sell = []
        for symbol, pos in positions.items():
            df = all_data[symbol]
            if today not in df.index: continue
            
            curr = df.loc[today]
            profit_rate = (curr['Close'] / pos['entry_price']) - 1
            hold_days = (today - pos['entry_date']).days
            
            # 보유 중 최고 수익률 업데이트
            pos['max_profit'] = max(pos.get('max_profit', 0), profit_rate)
            
            # ATR 기반 가변 손절가 (진입가 - 1.5*ATR)
            stop_price = pos['entry_price'] - (1.5 * pos['entry_atr'])
            
            # [신규] 본전 보존 로직: 수익이 3% 이상 났었다면 손절가를 본전으로 상향
            if pos['max_profit'] >= 0.03:
                stop_price = max(stop_price, pos['entry_price'])

            sell_trigger = False
            if profit_rate >= TARGET_PROFIT: # 1. 목표가 달성
                sell_trigger = True
            elif curr['Close'] < stop_price: # 2. 동적 손절선 이탈
                sell_trigger = True
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: # 3. 타임컷 (10일간 3% 미만 수익 시)
                sell_trigger = True
            
            if sell_trigger:
                sell_price = df.loc[tomorrow]['Open'] if tomorrow in df.index else curr['Close']
                pnl = (sell_price / pos['entry_price'] - 1) * 100 - (FEE * 2 * 100)
                trade_log.append(pnl)
                cash += (sell_price * pos['qty']) * (1 - FEE)
                to_sell.append(symbol)
        
        for s in to_sell: del positions[s]

        # --- 매수 로직 ---
        if len(positions) < MAX_POSITIONS:
            candidates = []
            for symbol, df in all_data.items():
                if symbol not in positions and today in df.index:
                    row = df.loc[today]
                    # 필터 1: 거래대금 300억 이상
                    # 필터 2: 지수보다 5% 이상 강한 종목 (상대적 모멘텀)
                    # 필터 3: 20일선 위 (추세 정배열)
                    if row['Amount'] >= 300 and row['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD):
                        if row['Close'] > row['MA20']:
                            candidates.append((symbol, row['Momentum']))
            
            # 모멘텀이 가장 강한 순서대로 정렬
            candidates.sort(key=lambda x: x[1], reverse=True)
            
            for symbol, _ in candidates:
                if len(positions) >= MAX_POSITIONS: break
                df = all_data[symbol]
                if tomorrow in df.index:
                    buy_price = df.loc[tomorrow]['Open']
                    buy_unit = cash / (MAX_POSITIONS - len(positions))
                    qty = (buy_unit * (1 - FEE)) // buy_price
                    if qty > 0:
                        positions[symbol] = {
                            'qty': qty, 
                            'entry_price': buy_price, 
                            'entry_date': today, 
                            'entry_atr': df.loc[today]['ATR'],
                            'max_profit': 0
                        }
                        cash -= (qty * buy_price) * (1 + FEE)

        # 일일 자산 가치 기록
        curr_val = cash + sum([all_data[s].loc[today]['Close'] * p['qty'] for s, p in positions.items() if today in all_data[s].index])
        history.append(curr_val)

    # [4] 결과 리포트
    history_ser = pd.Series(history)
    final_return = ((history[-1]/INITIAL_CASH)-1)*100
    win_rate = (len([r for r in trade_log if r > 0]) / len(trade_log) * 100) if trade_log else 0
    mdd = ((history_ser - history_ser.cummax()) / history_ser.cummax()).min() * 100
    
    print(f"\n🐢 v8.1 슬로우 퀀트 성적표")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 시작 금액: {INITIAL_CASH:,.0f}원")
    print(f"💰 최종 금액: {history[-1]:,.0f}원")
    print(f"📈 수익률: {final_return:.2f}%")
    print(f"🎯 승률: {win_rate:.2f}%")
    print(f"📉 MDD: {mdd:.2f}%")
    print(f"🔄 매매 횟수: {len(trade_log)}회")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    run_backtest()
