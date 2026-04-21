import FinanceDataReader as fdr
import pandas as pd
import requests
import json
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 텔레그램 설정 (깃허브 Secrets에서 가져옴)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 텔레그램 설정이 되어있지 않습니다.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"❌ 메시지 전송 실패: {e}")

def get_market_sentiment():
    """1단계: 시장 흐름 파악"""
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

    sentiment = "BULL" if (us_score >= 1 and is_ko_up) else "BEAR"
    return sentiment, report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_single_stock(symbol, name, sentiment, start_date, end_date):
    try:
        df = fdr.DataReader(symbol, start_date, end_date)
        if len(df) < 30: return None
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(window=5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr, prev = df.iloc[-1], df.iloc[-2]
        res = {'name': name, 'symbol': symbol, 'type': None, 'rsi': curr['RSI']}
        if sentiment == "BULL":
            if curr['RSI'] <= 30: res['type'] = 'BUY_OVERSOLD'
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (curr['Volume'] > curr['Vol_MA5']):
                res['type'] = 'BUY_BREAKOUT'
        if curr['RSI'] >= 70: res['type'] = 'SELL_OVERHEAT'
        return res if res['type'] else None
    except: return None

def main():
    sentiment, sentiment_report = get_market_sentiment()
    status_icon = "✅" if sentiment == "BULL" else "⚠️"
    final_report = f"{status_icon} *현재 시장 판단: [{'안정' if sentiment == 'BULL' else '위험'}]*\n\n"
    final_report += sentiment_report + "\n🔍 *종목 분석 결과*\n"

    k200 = fdr.StockListing('KOSPI').head(200)
    kd150 = fdr.StockListing('KOSDAQ').head(150)
    target_stocks = pd.concat([k200, kd150])

    buy_candidates, sell_candidates = [], []
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(analyze_single_stock, row['Code'], row['Name'], sentiment, start_date, end_date) for _, row in target_stocks.iterrows()]
        for future in as_completed(futures):
            res = future.result()
            if res:
                if res['type'] and res['type'].startswith('BUY'):
                    label = "📉과매도" if res['type'] == 'BUY_OVERSOLD' else "🚀돌파"
                    buy_candidates.append(f"• {res['name']}({res['symbol']}) {label}")
                else:
                    sell_candidates.append(f"• {res['name']}({res['symbol']}) (RSI:{res['rsi']:.1f})")

    final_report += "\n💎 *매수 후보*\n" + ("\n".join(buy_candidates[:15]) if buy_candidates else "없음")
    final_report += "\n\n🔥 *매도 주의*\n" + ("\n".join(sell_candidates[:15]) if sell_candidates else "없음")
    
    print(final_report)
    send_telegram_message(final_report)

if __name__ == "__main__":
    main()
