import pandas as pd
import FinanceDataReader as fdr
import os
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- [환경변수 및 설정값] ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

PORTFOLIO_FILE = 'my_portfolio.csv'
MAX_POSITIONS = 5
TARGET_PROFIT = 0.10
ATR_MULTIPLIER = 2.0
TIME_CUT_DAYS = 15
MOMENTUM_THRESHOLD = 0.03 # 최소 모멘텀 (+3%)
MOMENTUM_CEILING = 0.50   # [신규] 최대 모멘텀 (+50%). 이 이상은 과열로 판단하여 진입 금지
RSI_OVERHEAT = 75

# [영구 제명 리스트] 숫자는 좋지만 '파두'처럼 히스토리가 싫은 종목들만 남겨두세요.
PERMANENT_BLACKLIST = ['440110'] # 파두

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
        df['Momentum'] = df['Close'].pct_change(20)
        df['ATR'] = pd.concat([df['High']-df['Low'], abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).rolling(14).mean()
        df['RSI'] = (lambda s: 100 - (100 / (1 + (s.diff().clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / -s.diff().clip(upper=0).ewm(alpha=1/14, adjust=False).mean()))))(df['Close'])
        df['Amount'] = (df['Close'] * df['Volume']) / 100_000_000
        return df.iloc[-1]
    except: return None

def sync_portfolio(df):
    for index, row in df.iterrows():
        if pd.isna(row.get('entry_atr')) or row.get('entry_atr') == 0:
            try:
                start_search = (datetime.strptime(row['entry_date'], '%Y-%m-%d') - timedelta(days=40)).strftime('%Y-%m-%d')
                hist = fdr.DataReader(row['code'], start_search, row['entry_date'])
                tr = pd.concat([hist['High']-hist['Low'], abs(hist['High']-hist['Close'].shift()), abs(hist['Low']-hist['Close'].shift())], axis=1).max(axis=1)
                df.at[index, 'entry_atr'] = tr.rolling(14).mean().iloc[-1]
            except: pass
        try:
            curr_price = fdr.DataReader(row['code']).iloc[-1]['Close']
            curr_profit = (curr_price / row['entry_price']) - 1
            df.at[index, 'max_profit'] = max(row['max_profit'] if not pd.isna(row.get('max_profit')) else 0, curr_profit)
        except: pass
    return df

def run_bot():
    if not os.path.exists(PORTFOLIO_FILE): return
    
    # 1. KRX 데이터 로드 (텍스트 필터에 의존하지 않음)
    krx = fdr.StockListing('KRX')
    safe_krx = krx[~krx['Code'].isin(PERMANENT_BLACKLIST)].copy()
    name_map = dict(zip(safe_krx['Code'], safe_krx['Name']))
    
    # 2. 포트폴리오 및 현금 확인
    all_data = pd.read_csv(PORTFOLIO_FILE, dtype={'code': str})
    cash_mask = all_data['code'].str.upper() == 'CASH'
    cash_row = all_data[cash_mask]
    portfolio = all_data[~cash_mask].copy()
    portfolio = sync_portfolio(portfolio)
    current_cash = cash_row['qty'].iloc[0] if not cash_row.empty else 0

    # 3. 시장 지수 확인
    kospi = fdr.DataReader('KS11', (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'))
    market_alive = kospi['Close'].iloc[-1] > kospi['Close'].rolling(20).mean().iloc[-1]
    idx_ret = (kospi['Close'].iloc[-1] / kospi['Close'].iloc[-21]) - 1
    
    report = f"🛡️ *Smart Picking v8.3 (Ultimate Auto)*\n\n"
    report += f"1) 시장 지수 : {'✅ 매수 가능' if market_alive else '⚠️ 관망'}\n"
    report += f"- 지수 모멘텀: {idx_ret:.2%} | 현금: {current_cash:,.0f}원\n\n"

    # 4. 보유 종목 리포트
    report += f"2) 보유 종목 리포트\n━━━━━━━━━━━━\n"
    if portfolio.empty: report += "- 보유 종목 없음\n"
    else:
        for idx, row in portfolio.iterrows():
            curr = get_live_data(row['code'])
            p_name = name_map.get(row['code'], row['code']) or row['code']
            profit = (curr['Close'] / row['entry_price']) - 1
            atr_val = row['entry_atr'] if not pd.isna(row['entry_atr']) else 0
            stop = row['entry_price'] - (ATR_MULTIPLIER * atr_val)
            if row['max_profit'] >= 0.03: stop = max(stop, row['entry_price'])
            
            signal = "KEEP"
            if profit >= TARGET_PROFIT: signal = "🎯 익절(SELL)"
            elif curr['Close'] < stop: signal = "🚨 손절(SELL)"
            report += f"*{p_name}* ({row['code']})\n- 수익: {profit:.2%} | 제안: *{signal}*\n----------------------------\n"

    # 5. 신규 종목 추천 (통계적 과열 차단 필터 적용)
    report += f"\n3) 안전 종목 추천 (과열 종목 자동 제외)\n━━━━━━━━━━━━\n"
    empty_slots = MAX_POSITIONS - len(portfolio)
    if not market_alive or empty_slots <= 0:
        report += "- 신규 매수 조건 미충족\n"
    else:
        top_codes = safe_krx.nlargest(250, 'Marcap')['Code'].tolist()
        candidates = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(get_live_data, top_codes))
            for i, res in enumerate(results):
                if res is not None and top_codes[i] not in portfolio['code'].values:
                    # [핵심 로직] 지수보다 강하되, 20일 수익률이 50%를 넘지 않는 '건강한 상승' 종목만 선택
                    if (res['Amount'] >= 300 and 
                        (idx_ret + MOMENTUM_THRESHOLD) < res['Momentum'] < MOMENTUM_CEILING and 
                        res['RSI'] < RSI_OVERHEAT):
                        candidates.append({'code': top_codes[i], 'mom': res['Momentum'], 'price': res['Close']})
        
        candidates.sort(key=lambda x: x['mom'], reverse=True)
        buy_unit = current_cash / empty_slots
        if not candidates:
            report += "- 추천 가능 종목 없음 (모두 과열 상태)\n"
        for cand in candidates[:5]:
            report += f"✅ *{name_map.get(cand['code'])}*\n- 모멘텀: {cand['mom']:.2%} | *매수금: {buy_unit:,.0f}원*\n"

    final_df = pd.concat([portfolio, cash_row])
    final_df.to_csv(PORTFOLIO_FILE, index=False)
    send_telegram_report(report)

if __name__ == "__main__":
    run_bot()
