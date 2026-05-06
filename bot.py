import pandas as pd
import FinanceDataReader as fdr
import os
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [텔레그램 설정] ---
# v5.9 코드처럼 환경변수를 사용하거나 직접 입력하세요.
TELEGRAM_TOKEN = '여기에_토큰_입력'
TELEGRAM_CHAT_ID = '여기에_채팅ID_입력'

# --- [v8.3 실전 엔진 설정값] ---
PORTFOLIO_FILE = 'my_portfolio.csv'
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10      # 10% 익절
ATR_MULTIPLIER = 2.0      # ATR 2배 손절
TIME_CUT_DAYS = 15        # 15일 타임컷
MOMENTUM_THRESHOLD = 0.03 # 지수 대비 +3% 모멘텀
RSI_OVERHEAT = 75         # RSI 과열 기준

def send_telegram_report(message):
    """분석 결과를 텔레그램으로 전송합니다."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        print("⚠️ 텔레그램 설정이 비어 있습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: 
        requests.post(url, json=payload, timeout=10)
    except Exception as e: 
        print(f"❌ 텔레그램 전송 실패: {e}")

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
    """비어있는 ATR을 계산하고 max_profit을 업데이트합니다."""
    for index, row in df.iterrows():
        if pd.isna(row['entry_atr']) or row['entry_atr'] == 0:
            start_search = (datetime.strptime(row['entry_date'], '%Y-%m-%d') - timedelta(days=40)).strftime('%Y-%m-%d')
            hist = fdr.DataReader(row['code'], start_search, row['entry_date'])
            atr_val = calculate_atr(hist).iloc[-1]
            df.at[index, 'entry_atr'] = atr_val
        
        curr_data = fdr.DataReader(row['code']).iloc[-1]
        current_profit = (curr_data['Close'] / row['entry_price']) - 1
        df.at[index, 'max_profit'] = max(row['max_profit'] if not pd.isna(row['max_profit']) else 0, current_profit)
    return df

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE):
        print(f"❌ '{PORTFOLIO_FILE}' 파일이 없습니다.")
        return

    # 종목명 매핑 데이터 로드
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))

    # 1. 데이터 로드 및 분리
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    cash_row = all_data[cash_mask]
    portfolio = all_data[~cash_mask].copy()
    
    current_cash = cash_row['qty'].iloc[0] if not cash_row.empty else 0
    portfolio = sync_portfolio(portfolio)

    # 리포트 생성용 문자열
    full_report = f"🌿 *스마트 피킹 리포트 v8.3*\n\n"

    # --- 1) 시장 지수 확인 ---
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    kospi['MA20'] = kospi['Close'].rolling(20).mean()
    market_alive = kospi['Close'].iloc[-1] > kospi['MA20'].iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    full_report += f"1) 시장 지수 확인 : 코스피 상태\n"
    full_report += f"━━━━━━━━━━━━━━━━━━━━\n"
    full_report += f"- 지수 추세: {'🚀 매수 가능' if market_alive else '⚖️ 관망 권장'}\n"
    full_report += f"- 지수 모멘텀: {idx_ret:.2%}\n"
    full_report += f"- 가용 현금: {current_cash:,.0f}원\n\n"

    # --- 2) 보유 종목 대응 리포트 ---
    full_report += f"2) 보유 종목 대응 리포트 :\n"
    full_report += f"━━━━━━━━━━━━━━━━━━━━\n"
    if portfolio.empty:
        full_report += "- 보유 중인 종목이 없습니다.\n"
    else:
        for index, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            if curr is None: continue
            
            p_name = name_map.get(row['code'], "알 수 없음")
            profit_rate = (curr['Close'] / row['entry_price']) - 1
            hold_days = (datetime.now() - datetime.strptime(row['entry_date'], '%Y-%m-%d')).days
            stop_price = row['entry_price'] - (ATR_MULTIPLIER * row['entry_atr'])
            if row['max_profit'] >= 0.03: stop_price = max(stop_price, row['entry_price'])

            signal = "KEEP"
            if profit_rate >= TARGET_PROFIT: signal = "🎯 익절(SELL)"
            elif curr['Close'] < stop_price: signal = "🚨 손절(SELL)"
            elif hold_days >= TIME_CUT_DAYS and profit_rate < 0.03: signal = "⏳ 타임컷(SELL)"
            
            full_report += f"*{p_name}* ({row['code']})\n"
            full_report += f"- 수익률: {profit_rate:.2%} (최고 {row['max_profit']:.2%})\n"
            full_report += f"- 제안: *{signal}*\n"
            full_report += f"----------------------------\n"

    # --- 3) 신규 종목 추천 ---
    full_report += f"\n3) 신규 종목 추천\n"
    full_report += f"━━━━━━━━━━━━━━━━━━━━\n"
    if not market_alive:
        full_report += "- 시장 하락으로 추천을 중단합니다.\n"
    elif len(portfolio) >= MAX_POSITIONS:
        full_report += "- 포트폴리오 슬롯이 가득 찼습니다.\n"
    else:
        top_stocks = krx.nlargest(250, 'Marcap')['Code'].tolist()
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(get_live_data, top_stocks))
            for i, res in enumerate(results):
                if res is not None and top_stocks[i] not in portfolio['code'].values:
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD):
                        if res['Close'] > res['MA20'] and res['RSI'] < RSI_OVERHEAT:
                            candidates.append({'code': top_stocks[i], 'mom': res['Momentum'], 'price': res['Close']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / (MAX_POSITIONS - len(portfolio)) if len(portfolio) < MAX_POSITIONS else 0
        
        for cand in candidates[:5]:
            c_name = name_map.get(cand['code'], "알 수 없음")
            full_report += f"✅ *{c_name}* ({cand['code']})\n"
            full_report += f"- 모멘텀: {cand['mom']:.2%} | 매수금: {buy_unit:,.0f}원\n"

    # 결과 출력 및 전송
    print(full_report)
    send_telegram_report(full_report)

    # 데이터 저장
    pd.concat([portfolio, cash_row]).to_csv(PORTFOLIO_FILE, index=False)

if __name__ == "__main__":
    run_bot()
