import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def get_market_sentiment():
    us_indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC'}
    us_score = 0
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    report = "🌍 *글로벌 시장 분석*\n"
    for name, ticker in us_indices.items():
        try:
            df_us = fdr.DataReader(ticker, start_date)
            change = (df_us['Close'].iloc[-1] - df_us['Close'].iloc[-2]) / df_us['Close'].iloc[-2] * 100
            if change > -0.5: us_score += 1
            report += f" - {name}: {change:+.2f}%\n"
        except: continue
    try:
        df_ko = fdr.DataReader('KS11', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
        is_ko_up = df_ko['Close'].iloc[-1] > df_ko['Close'].rolling(5).mean().iloc[-1]
        report += f" - KOSPI: {'상승추세 📈' if is_ko_up else '하향추세 📉'}\n"
    except: is_ko_up = False
    return ("BULL" if (us_score >= 1 and is_ko_up) else "BEAR"), report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_single_stock(symbol, name, sentiment, start_date, end_date):
    try:
        df = fdr.DataReader(symbol, start_date, end_date)
        if len(df) < 30: return None
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA10'] = df['Close'].rolling(window=10).mean()
        df['Vol_MA5'] = df['Volume'].rolling(window=5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr, prev = df.iloc[-1], df.iloc[-2]
        
        res = {'name': name, 'symbol': symbol, 'grade': None, 'type': None, 'sell_type': None, 'rsi': curr['RSI']}

        # 매수 로직 (BULL 시장에서만)
        if sentiment == "BULL":
            if curr['RSI'] <= 30:
                res.update({'grade': 'S', 'type': '과매도(강) 🧊'})
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (curr['Volume'] > df['Vol_MA5'].iloc[-1]):
                res.update({'grade': 'S', 'type': '20일선돌파 🚀'})
            elif curr['RSI'] <= 45:
                res.update({'grade': 'A', 'type': '과매도(약) 💧'})
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > prev['MA10']) and (curr['Volume'] > df['Vol_MA5'].iloc[-1] * 0.7):
                res.update({'grade': 'A', 'type': '10일선돌파 💨'})

        # [매도 로직 고도화]
        if curr['RSI'] >= 80:
            res['sell_type'] = f"🔥 즉시매도(극심과열:{curr['RSI']:.1f})"
        elif curr['RSI'] >= 70:
            if curr['Close'] < curr['MA10']:
                res['sell_type'] = f"📢 매도결행(추세이탈)"
            else:
                res['sell_type'] = f"⚠️ 매도주의(과열진입)"
        
        return res if (res['grade'] or res['sell_type']) else None
    except: return None

def main():
    sentiment, sentiment_report = get_market_sentiment()
    final_report = f"{'✅' if sentiment == 'BULL' else '⚠️'} *현재 시장 판단: [{'안정' if sentiment == 'BULL' else '위험'}]*\n{sentiment_report}\n"

    target_stocks = pd.concat([fdr.StockListing('KOSPI').head(200), fdr.StockListing('KOSDAQ').head(150)])
    s_list, a_list, sell_list = [], [], []
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(analyze_single_stock, row['Code'], row['Name'], sentiment, start_date, datetime.now().strftime('%Y-%m-%d')) for _, row in target_stocks.iterrows()]
        for future in as_completed(futures):
            r = future.result()
            if not r: continue
            if r.get('grade') == 'S': s_list.append(f"• *{r['name']}*({r['symbol']}) {r['type']}")
            elif r.get('grade') == 'A': a_list.append(f"• {r['name']}({r['symbol']}) {r['type']}")
            if r.get('sell_type'): sell_list.append(f"• {r['name']}({r['symbol']}) {r['sell_type']}")

    final_report += "💎 *[S급] 필승 후보 (비중↑)*\n" + ("\n".join(s_list[:15]) if s_list else "없음")
    final_report += "\n\n✨ *[A급] 관심 후보 (비중↓)*\n" + ("\n".join(a_list[:15]) if a_list else "없음")
    final_report += "\n\n🔔 *매도 추천 (리스크 관리)*\n" + ("\n".join(sell_list[:15]) if sell_list else "없음")
    
    send_telegram_message(final_report)

if __name__ == "__main__": main()
