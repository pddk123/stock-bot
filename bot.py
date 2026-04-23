import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 환경 변수 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def load_portfolio():
    """portfolio.txt에서 종목 코드를 읽어옴 (# 주석 지원)"""
    codes = []
    try:
        if os.path.exists('portfolio.txt'):
            with open('portfolio.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    clean_line = line.split('#')[0].strip()
                    if not clean_line: continue
                    parts = clean_line.replace(',', ' ').split()
                    for code in parts:
                        if code.strip(): codes.append(code.strip())
            return list(set(codes))
    except: pass
    return []

def get_market_sentiment():
    """시장 동향 상세 분석 및 한줄평 요약"""
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    report = "📊 시장 동향 브리핑\n"
    scores, total_chg = 0, 0
    
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            ma5 = df['Close'].rolling(5).mean().iloc[-1]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            gap_ma5 = (curr - ma5) / ma5 * 100
            
            trend = "과열" if gap_ma5 > 3 else "안정" if gap_ma5 > -2 else "침체"
            report += f"- {name}: {chg:+.2f}% ({trend} / 5일선이격 {gap_ma5:+.1f}%)\n"
            if chg > -0.5: scores += 1
        except: continue
    
    avg_chg = total_chg / len(indices)
    if avg_chg >= 1.5: summary = "🚀 전체 시장이 뜨거운 '급등장'입니다!"
    elif avg_chg > 0: summary = "📈 훈풍이 부는 '상승세'를 보이고 있습니다."
    elif avg_chg <= -1.5: summary = "📉 하락세가 깊은 '급락장'입니다. 조심하세요."
    else: summary = "📉 차분하게 가라앉은 '하락세'를 보이고 있습니다."
    
    report += f"\n📢 총평: {summary}\n"
    sentiment = "BULL" if scores >= 2 else "BEAR"
    return sentiment, report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sentiment, is_portfolio=False):
    """개별 종목 고도화 분석 (매수/매도 로직)"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        vol_ratio, rsi_val = curr['Volume'] / curr['Vol_MA5'] if curr['Vol_MA5'] > 0 else 0, df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val}
        
        metrics = f"(RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"

        if sentiment == "BULL":
            if rsi_val <= 30: res.update({'grade': 'S', 'desc': f"🔥 강한 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (vol_ratio > 1.0):
                res.update({'grade': 'S', 'desc': f"🚀 20일선 골든크로스 {metrics}"})
            elif rsi_val <= 45: res.update({'grade': 'A', 'desc': f"✨ 단기 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > prev['MA10']) and (vol_ratio > 0.8):
                res.update({'grade': 'A', 'desc': f"📈 10일선 반등 {metrics}"})

        if rsi_val >= 80:
            res['sell_desc'] = f"🚨 즉시매도(극심과열) {metrics}"
        elif rsi_val >= 70:
            if curr['Close'] < curr['MA10']:
                res['sell_desc'] = f"📢 매도결행(추세이탈) {metrics}"
            else:
                res['sell_desc'] = f"⚠️ 매도주의(과열진입) {metrics}"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    # 1. KRX 전종목 리스팅 (재무 지표 포함)
    df_krx = fdr.StockListing('KRX')
    
    # 2. 건실한 기업 필터링 로직 (동근 님 맞춤형)
    # - 시가총액(MarCap) 2,000억 이상
    # - PBR 0.3 이상 (자산 가치 기반)
    # - [필터] 바이오 섹터이면서 적자(PER <= 0)인 종목 제외
    bio_keywords = '의약|제약|바이오|생물|헬스케어'
    mask = (df_krx['MarCap'] >= 200_000_000_000) & (df_krx['PBR'] >= 0.3)
    is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains(bio_keywords, na=False))
    
    healthy_stocks = df_krx[mask & ~is_red_bio]
    
    # 건실한 종목 중 시총 상위 350개를 분석 대상으로 선정
    total_market = healthy_stocks.sort_values(by='MarCap', ascending=False).head(350)
    name_map = dict(zip(df_krx['Code'], df_krx['Name']))
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # 보유 종목과 시장 종목 병렬 분석
        p_futures = [executor.submit(analyze_stock, code, name_map.get(code, code), sentiment, True) for code in my_stock_codes]
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], sentiment, False) for _, row in total_market.iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            
            if r['is_portfolio']:
                status = "✅ 보유유지"
                if r['sell_desc']: status = f"🚩 매도검토({r['sell_desc']})"
                elif r['grade']: status = f"💰 추가매수권장({r['grade']}급: {r['desc']})"
                portfolio_res.append(f"- {r['symbol']}({r['name']}): {status}")
            else:
                if r['grade'] == 'S': s_list.append(f"- *{r['name']}*: {r['desc']}")
                elif r['grade'] == 'A': a_list.append(f"- {r['name']}: {r['desc']}")
                if r['sell_desc']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 메시지 조립
    report = f"{mkt_report}\n"
    report += "📁 내 보유 종목 현황\n" + ("\n".join(portfolio_res) if portfolio_res else "- 등록된 종목 없음") + "\n\n"
    report += "💎 S급 건실 후보 (스윙 추천)\n" + ("\n".join(s_list[:10]) if s_list else "- 조건 충족 종목 없음") + "\n\n"
    report += "✨ A급 관심 후보\n" + ("\n".join(a_list[:10]) if a_list else "- 조건 충족 종목 없음") + "\n\n"
    report += "🔔 매도 알림 (과열/이탈)\n" + ("\n".join(sell_list[:10]) if sell_list else "- 해당 없음")
    
    send_telegram_message(report)

if __name__ == "__main__":
    main()
