import pandas as pd
import FinanceDataReader as fdr
import os
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [GitHub Secrets 환경변수 로드] ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- [v8.3 실전 엔진 설정값] ---
PORTFOLIO_FILE = 'my_portfolio.csv'
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10
ATR_MULTIPLIER = 2.0
TIME_CUT_DAYS = 15
MOMENTUM_THRESHOLD = 0.03 # 지수 대비 +3%
RSI_OVERHEAT = 75

def send_telegram_report(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=15)
    except: print("❌ 텔레그램 전송 실패")

def get_live_data(symbol):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d'))
        if len(df) < 50: return None
        df['MA20'] = df['Close'].rolling(20).mean()
        df['Momentum'] = df['Close'].pct_change(20) # 20일 모멘텀 계산
        df['ATR'] = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).rolling(14).mean()
        df['RSI'] = (lambda s: 100 - (100 / (1 + (s.diff().clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / -s.diff().clip(upper=0).ewm(alpha=1/14, adjust=False).mean()))))(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.iloc[-1]
    except: return None

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    krx = fdr.StockListing('KRX')
    name_map = dict(zip(krx['Code'], krx['Name']))
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    current_cash = all_data[cash_mask]['qty'].iloc[0] if any(cash_mask) else 0
    portfolio = all_data[~cash_mask].copy()

    # 시장 지수 확인
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    report = f"🌿 *Smart Picking v8.3 리포트*\n\n"
    report += f"1) 시장 지수 : {'✅ 매수 가능' if market_alive else '⚠️ 관망'}\n"
    report += f"- 지수 모멘텀: {idx_ret:.2%} | 현금: {current_cash:,.0f}원\n\n"

    # 2) 보유 종목 대응 (모멘텀 추가)
    report += f"2) 보유 종목 리포트\n━━━━━━━━━━━━\n"
    if portfolio.empty: report += "- 보유 종목 없음\n"
    else:
        for idx, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            p_name = name_map.get(row['code'], row['code'])
            profit = (curr['Close'] / row['entry_price']) - 1
            stop = max(row['entry_price'] - (ATR_MULTIPLIER * row['entry_atr']), row['entry_price'] if row['max_profit'] >= 0.03 else 0)
            signal = "KEEP"
            if profit >= TARGET_PROFIT: signal = "🎯 익절(SELL)"
            elif curr['Close'] < stop: signal = "🚨 손절(SELL)"
            
            report += f"*{p_name}* ({row['code']})\n"
            report += f"- 수익: {profit:.2%} | *모멘텀: {curr['Momentum']:.2%}*\n"
            report += f"- 제안: *{signal}* (손절가: {stop:,.0f})\n"
            report += f"----------------------------\n"

    # 3) 신규 종목 추천
    report += f"\n3) 신규 종목 추천 (남은 슬롯 채우기)\n━━━━━━━━━━━━\n"
    empty_slots = MAX_POSITIONS - len(portfolio)
    if not market_alive or empty_slots <= 0:
        report += "- 신규 매수 조건 미충족\n"
    else:
        top_codes = krx.nlargest(250, 'Marcap')['Code'].tolist()
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            for i, res in enumerate(results):
                if res is not None and top_codes[i] not in portfolio['code'].values:
                    if res['Amount'] >= 300 and res['Momentum'] > (idx_ret + MOMENTUM_THRESHOLD) and res['RSI'] < RSI_OVERHEAT:
                        candidates.append({'code': top_codes[i], 'mom': res['Momentum'], 'price': res['Close']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / empty_slots
        for cand in candidates[:5]:
            report += f"✅ *{name_map.get(cand['code'])}*\n- 모멘텀: {cand['mom']:.2%} | *매수금: {buy_unit:,.0f}원*\n"

    print(report)
    send_telegram_report(report)

if __name__ == "__main__":
    run_bot()
