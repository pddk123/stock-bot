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
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def load_portfolio():
    """portfolio.txt에서 종목 코드를 읽어옴"""
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
    except Exception as e:
        print(f"Portfolio Load Error: {e}")
    return []

def get_market_sentiment():
    """시장 지수 분석"""
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=25)).strftime('%Y-%m-%d')
    report = "📊 시장 동향 브리핑\n"
    scores, total_chg = 0, 0
    
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            if len(df) < 2: continue
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            ma5 = df['Close'].rolling(5).mean().iloc[-1]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            gap_ma5 = (curr - ma5) / ma5 * 100
            
            trend = "과열" if gap_ma5 > 3 else "안정" if gap_ma5 > -2 else "침체"
            report += f"- {name}: {chg:+.2f}% ({trend})\n"
            if chg > -0.5: scores += 1
        except: continue
    
    avg_chg = total_chg / len(indices) if indices else 0
    if avg_chg >= 1.2: summary = "🚀 전체 시장이 뜨거운 '급등장'입니다!"
    elif avg_chg > 0: summary = "📈 훈풍이 부는 '상승세'를 보이고 있습니다."
    elif avg_chg <= -1.2: summary = "📉 하락세가 깊은 '급락장'입니다. 조심하세요."
    else: summary = "📉 차분하게 가라앉은 '하락세'를 보이고 있습니다."
    
    report += f"\n📢 총평: {summary}\n"
    return ("BULL" if scores >= 2 else "BEAR"), report

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down.replace(0, 0.001) # 0 나누기 방지
    return 100 - (100 / (1 + rs))

def analyze_stock(symbol, name, sentiment, is_portfolio=False):
    """개별 종목 기술적 분석 및 거래량 필터"""
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=130)).strftime('%Y-%m-%d'))
        if len(df) < 35: return None
        
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        
        curr, prev = df.iloc[-1], df.iloc[-2]
        
        # [거래량 하한선 필터]
        # 1. 오늘 거래량이 0인 종목은 즉시 제외
        if curr['Volume'] <= 0: return None
        
        vol_ma5_val = curr['Vol_MA5'] if curr['Vol_MA5'] > 0 else 1
        vol_ratio, rsi_val = curr['Volume'] / vol_ma5_val, curr['RSI']
        
        # 2. 거래량 하한선: 최근 5일 평균 거래량의 20% 미만인 소외주 제외
        # (매수하고 싶어도 호가가 비어있을 확률이 높음)
        if vol_ratio < 0.2: return None

        res = {'name': name, 'symbol': symbol, 'is_portfolio': is_portfolio, 
               'grade': None, 'desc': "", 'sell_desc': "", 'rsi': rsi_val}
        
        metrics = f"(RSI:{rsi_val:.1f}, 거래량:{vol_ratio:.1f}배)"

        if sentiment == "BULL":
            if rsi_val <= 30: res.update({'grade': 'S', 'desc': f"🔥 강한 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA20']) and (curr['Close'] > curr['MA20']) and (vol_ratio > 1.1):
                res.update({'grade': 'S', 'desc': f"🚀 20일선 골든크로스 {metrics}"})
            elif rsi_val <= 42: res.update({'grade': 'A', 'desc': f"✨ 단기 과매도 {metrics}"})
            elif (prev['Close'] < prev['MA10']) and (curr['Close'] > prev['MA10']) and (vol_ratio > 0.9):
                res.update({'grade': 'A', 'desc': f"📈 10일선 반등 {metrics}"})

        if rsi_val >= 80:
            res['sell_desc'] = f"🚨 즉시매도(극심과열) {metrics}"
        elif rsi_val >= 72:
            if curr['Close'] < curr['MA10']:
                res['sell_desc'] = f"📢 매도결행(추세이탈) {metrics}"
            else:
                res['sell_desc'] = f"⚠️ 매도주의(과열진입) {metrics}"
            
        return res if (res['grade'] or res['sell_desc'] or is_portfolio) else None
    except: return None

def main():
    print("Starting analysis...")
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    try:
        df_krx = fdr.StockListing('KRX')
    except Exception as e:
        print(f"Error fetching KRX listing: {e}")
        return

    cols = df_krx.columns
    marcap_col = 'MarCap' if 'MarCap' in cols else 'MarketCap' if 'MarketCap' in cols else None
    
    # [1. 기본 재무 필터]
    if marcap_col:
        mask = (df_krx[marcap_col] >= 200_000_000_000) # 시총 2,000억 이상
    else:
        mask = pd.Series(True, index=df_krx.index)
    
    if 'PBR' in cols:
        mask &= (df_krx['PBR'] >= 0.3)
        
    # [2. 스팩 및 제N호 종목 제외] - 이름으로 필터링
    mask &= ~(df_krx['Name'].str.contains('스팩|제\d+호', na=False))
    
    # [3. 적자 바이오 제외]
    is_red_bio = pd.Series(False, index=df_krx.index)
    if 'PER' in cols and 'Sector' in cols:
        bio_keywords = '의약|제약|바이오|생물|헬스케어'
        is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains(bio_keywords, na=False))
    
    healthy_stocks = df_krx[mask & ~is_red_bio]
    
    # 건실한 종목 상위 350개 분석
    total_market = healthy_stocks.sort_values(by=marcap_col if marcap_col else 'Code', ascending=False).head(350)
    name_map = dict(zip(df_krx['Code'], df_krx['Name']))
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    print(f"Analyzing {len(total_market)} stocks...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        p_futures = [executor.submit(analyze_stock, code, name_map.get(code, code), sentiment, True) for code in my_stock_codes]
        m_futures = [executor.submit(analyze_stock, row['Code'], row['Name'], sentiment, False) for _, row in total_market.iterrows()]
        
        for future in as_completed(p_futures + m_futures):
            r = future.result()
            if not r: continue
            
            if r['is_portfolio']:
                status = "✅ 유지"
                if r['sell_desc']: status = f"🚩 매도검토({r['sell_desc']})"
                elif r['grade']: status = f"💰 추가매수({r['grade']}: {r['desc']})"
                portfolio_res.append(f"- {r['symbol']}({r['name']}): {status}")
            else:
                if r['grade'] == 'S': s_list.append(f"- *{r['name']}*: {r['desc']}")
                elif r['grade'] == 'A': a_list.append(f"- {r['name']}: {r['desc']}")
                if r['sell_desc']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 리포트 조립
    report = f"{mkt_report}\n"
    report += "📁 내 보유 종목 현황\n" + ("\n".join(portfolio_res) if portfolio_res else "- 등록된 종목 없음") + "\n\n"
    report += "💎 S급 건실 후보 (스팩제외)\n" + ("\n".join(s_list[:10]) if s_list else "- 없음") + "\n\n"
    report += "✨ A급 관심 후보\n" + ("\n".join(a_list[:10]) if a_list else "- 없음") + "\n\n"
    report += "🔔 매도 알림\n" + ("\n".join(sell_list[:10]) if sell_list else "- 해당 없음")
    
    send_telegram_message(report)
    print("Done!")

if __name__ == "__main__":
    main()
