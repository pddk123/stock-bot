import pandas as pd
import FinanceDataReader as fdr
import os
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [v8.3 실전 엔진 설정값] ---
PORTFOLIO_FILE = 'my_portfolio.csv'
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10      # 10% 익절
ATR_MULTIPLIER = 2.0      # ATR 2배 손절
TIME_CUT_DAYS = 15        # 15일 타임컷
MOMENTUM_THRESHOLD = 0.03 # 지수 대비 +3% 모멘텀
RSI_OVERHEAT = 75         # RSI 75 이상 진입 금지

def calculate_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100.0 - (100.0 / (1.0 + (roll_up / roll_down)))

def calculate_atr(df, n=14):
    tr = pd.concat([df['High'] - df['Low'], 
                    abs(df['High'] - df['Close'].shift()), 
                    abs(df['Low'] - df['Close'].shift())], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_live_data(symbol):
    try:
        # 지표 계산을 위해 충분한 과거 데이터 로드
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d'))
        if len(df) < 50: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = calculate_atr(df)
        df['RSI'] = calculate_rsi(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.iloc[-1]
    except: return None

def sync_portfolio(df):
    """비어있는 ATR을 채우고 최고 수익률을 업데이트합니다."""
    for index, row in df.iterrows():
        # 1. ATR 자동 역추적 계산 (값이 없을 때만)
        if pd.isna(row['entry_atr']) or row['entry_atr'] == 0:
            start_search = (datetime.strptime(row['entry_date'], '%Y-%m-%d') - timedelta(days=40)).strftime('%Y-%m-%d')
            hist = fdr.DataReader(row['code'], start_search, row['entry_date'])
            atr_val = calculate_atr(hist).iloc[-1]
            df.at[index, 'entry_atr'] = atr_val
        
        # 2. 최고 수익률(max_profit) 업데이트
        curr_data = fdr.DataReader(row['code']).iloc[-1]
        current_profit = (curr_data['Close'] / row['entry_price']) - 1
        df.at[index, 'max_profit'] = max(row['max_profit'] if not pd.isna(row['max_profit']) else 0, current_profit)
    return df

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE):
        print(f"❌ '{PORTFOLIO_FILE}' 파일이 없습니다.")
        return

    # 1. 데이터 로드 및 분리
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    cash_row = all_data[cash_mask]
    portfolio = all_data[~cash_mask].copy()
    
    current_cash = cash_row['qty'].iloc[0] if not cash_row.empty else 0
    portfolio = sync_portfolio(portfolio)

    # --- 1) 시장 지수 확인 ---
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    market_alive = kospi['Close'].iloc[-1] > kospi['MA20'].iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    print(f"\n1) 시장 지수 확인 : 코스피 상태")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"- 현재 지수: {kospi['Close'].iloc[-1]:.2f}")
    print(f"- 지수 추세: {'✅ 매수 가능 (MA20 상회)' if market_alive else '⚠️ 관망 권장 (MA20 하회)'}")
    print(f"- 지수 모멘텀(1M): {idx_ret:.2%}")
    print(f"- 가용 현금: {current_cash:,.0f}원")

    # --- 2) 보유 종목 대응 리포트 ---
    print(f"\n2) 보유 종목 대응 리포트 :")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if portfolio.empty:
        print("- 현재 보유 중인 종목이 없습니다.")
    else:
        for index, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            if curr is None: continue
            
            profit_rate = (curr['Close'] / row['entry_price']) - 1
            hold_days = (datetime.now() - datetime.strptime(row['entry_date'], '%Y-%m-%d')).days
            
            # 본전 보전 및 동적 손절가 계산
            stop_price = row['entry_price'] - (ATR_MULTIPLIER * row['entry_atr'])
            if row['max_profit'] >= 0.03: stop_price = max(stop_price, row['entry_price'])

            # 매도 시그널 판단
            signal = "KEEP"
            if profit_rate >= TARGET_PROFIT: signal = "🎯 목표가 달성 (SELL)"
            elif curr['Close'] < stop_price: signal = "🚨 손절선 이탈 (SELL)"
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: signal = "⏳ 타임컷 대상 (SELL)"
            
            print(f"[{row['code']}] 수익률: {profit_rate:.2%}")
            print(f"   - 최고수익: {row['max_profit']:.2%} | 보유일: {hold_days}일")
            print(f"   - 현재가: {curr['Close']:,.0f} | 손절가: {stop_price:,.0f}")
            print(f"   - **최종 제안: {signal}**")
            print("-" * 30)

    # --- 3) 신규 종목 추천 ---
    print(f"\n3) 신규 종목 추천")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if not market_alive:
        print("- 시장 하락 추세로 인해 신규 추천을 중단합니다.")
    elif len(portfolio) >= MAX_POSITIONS:
        print("- 포트폴리오 슬롯이 가득 찼습니다. (최대 5개)")
    else:
        krx = fdr.StockListing('KRX')
        # 시총 상위 250개 중 거래대금 300억 이상 필터링
        top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
        
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(get_live_data, top_stocks))
            for i, res in enumerate(results):
                if res is not None and top_stocks[i] not in portfolio['code'].values:
                    # v8.3 필터: 지수보다 강함 + 정배열 + RSI 75미만
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD):
                        if res['Close'] > res['MA20'] and res['RSI'] < RSI_OVERHEAT:
                            candidates.append({'code': top_stocks[i], 'mom': res['Momentum'], 'price': res['Close']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        if not candidates:
            print("- 현재 조건에 맞는 강력한 주도주가 없습니다.")
        else:
            buy_unit = current_cash / (MAX_POSITIONS - len(portfolio))
            for cand in candidates[:5]:
                print(f"✅ 추천: {cand['code']} | 모멘텀: {cand['mom']:.2%}")
                print(f"   - 현재가: {cand['price']:,.0f} | 권장 매수금액: {buy_unit:,.0f}원")

    # 데이터 저장 (수정된 ATR, max_profit 반영)
    final_save = pd.concat([portfolio, cash_row])
    final_save.to_csv(PORTFOLIO_FILE, index=False)
    print(f"\n✅ 분석 완료 및 '{PORTFOLIO_FILE}' 업데이트됨.")

if __name__ == "__main__":
    run_bot()
