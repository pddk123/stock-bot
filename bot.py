import FinanceDataReader as fdr
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, timezone
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

def get_krx_sectors():
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        url = 'https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        response = requests.get(url, headers=headers)
        df = pd.read_html(response.text, header=0)[0]
        df['종목코드'] = df['종목코드'].apply(lambda x: f"{x:06d}")
        return df[['종목코드', '업종']].rename(columns={'종목코드':'Code', '업종':'Sector'})
    except Exception as e:
        print(f"섹터 수집 오류: {e}")
        return pd.DataFrame(columns=['Code', 'Sector'])

def load_portfolio():
    codes = []
    if os.path.exists('portfolio.txt'):
        with open('portfolio.txt', 'r', encoding='utf-8') as f:
            for line in f:
                clean_line = line.split('#')[0].strip()
                if not clean_line: continue
                parts = clean_line.replace(',', ' ').split()
                for code in parts:
                    if code.strip(): codes.append(code.strip())
    return list(set(codes))

def get_market_sentiment():
    indices = {'Nasdaq': '^IXIC', 'S&P500': '^GSPC', 'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    scores, total_chg = 0, 0
    details = []
    for name, ticker in indices.items():
        try:
            df = fdr.DataReader(ticker, start_date)
            curr, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
            chg = (curr - prev) / prev * 100
            total_chg += chg
            details.append(f"- {name}: {chg:+.2f}%")
            if chg > -0.1: scores += 1
        except: continue
    
    avg_chg = total_chg / len(indices) if indices else 0
    reason = f"글로벌 지수 평균 {avg_chg:+.2f}% 등락 기반"
    if scores >= 3: status = "🚀 **강력 상승장**"
    elif scores >= 2: status = "📈 **완만한 상승장**"
    else: status = "📉 **주의/하락 구간**"
    return status, reason, "\n".join(details)

def calculate_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + (ema_up / ema_down)))

def analyze_stock(symbol, name, sector, is_portfolio=False):
    try:
        df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
        if len(df) < 30: return None
        
        # 지표 계산
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr = df.iloc[-1]
        rsi_val = df['RSI'].iloc[-1]

        # --- [오전 10시 전략: 시간 가중치 계산] ---
        # 한국 시간(KST) 기준 현재 시간 구하기
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        
        # 장 중(09:00 ~ 15:30) 경과 시간 계산
        m_start = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed = (now_kst - m_start).total_seconds() / 3600
        
        # 장 시작 전이면 0.1시간, 장 종료 후면 6.5시간으로 고정
        elapsed = max(0.1, min(elapsed, 6.5))
        
        # 시간 가중치 (총 6.5시간 / 경과 시간)
        time_weight = 6.5 / elapsed
        # 현재 거래량을 하루치로 환산한 예상 거래량 비율
        expected_vol_ratio = (curr['Volume'] / curr['Vol_MA5']) * time_weight
        # ------------------------------------------

        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': expected_vol_ratio, 'sell_desc': ""}

        # S급: RSI 적정 + 정배열 + 예상 거래량 1.5배 이상
        if (45 <= rsi_val <= 65) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (expected_vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        # A급: RSI 50이상 + MA10 돌파 + 예상 거래량 1.0배 이상
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (expected_vol_ratio >= 1.0):
            res.update({'grade': 'A'})

        if rsi_val >= 80: res['sell_desc'] = f"🔥 과열(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    mkt_status, mkt_reason, mkt_report = get_market_sentiment()
    my_codes = load_portfolio()
    
    krx_price = fdr.StockListing('KRX')
    krx_sector = get_krx_sectors()
    all_stocks = pd.merge(krx_price, krx_sector, on='Code', how='left').fillna({'Sector': '기타'})
    
    # 시총 1500억 이상 상위 350개로 압축
    robust_market = all_stocks[all_stocks['Marcap'] >= 150_000_000_000].sort_values(by='Marcap', ascending=False).head(350)
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        p_tasks = [executor.submit(analyze_stock, code, row['Name'], row['Sector'], True) 
                   for code in my_codes for _, row in all_stocks[all_stocks['Code']==code].iterrows()]
        m_tasks = [executor.submit(analyze_stock, row['Code'], row['Name'], row['Sector'], False) 
                   for _, row in robust_market.iterrows()]
        
        for future in as_completed(p_tasks + m_tasks):
            r = future.result()
            if not r: continue
            if r['is_portfolio']:
                status = "보유유지"
                if r['sell_desc']: status = f"🚨 매도검토({r['sell_desc']})"
                elif r['grade']: status = f"✅ 추가매수({r['grade']}급)"
                portfolio_res.append(f"- {r['name']}: {status}")
            else:
                if r['grade'] == 'S': s_list.append(r)
                elif r['grade'] == 'A': a_list.append(r)
                if r['sell_desc']: sell_list.append(f"- {r['name']}: {r['sell_desc']}")

    # 메시지 구성
    final_msg = f"🌿 **rootee님, 10시 전략 적용 리포트**\n\n"
    final_msg += f"📊 **시장 진단**: {mkt_status}\n🧐 **판단 근거**: {mkt_reason}\n{mkt_report}\n\n"
    
    combined = pd.DataFrame(s_list + a_list)
    if not combined.empty:
        # '기타' 섹터 제외하고 주도 섹터 추출
        filtered_sectors = combined[combined['sector'] != '기타']
        if not filtered_sectors.empty:
            top_s = filtered_sectors['sector'].value_counts().head(2).index.tolist()
            final_msg += f"🔥 **현재 주도 섹터**: {', '.join(top_s)}\n\n"

    final_msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    
    final_msg += "💎 **S급: 추세 폭발 초입**\n"
    if s_list:
        df_s = pd.DataFrame(s_list)
        # 섹터별로 거래량 비율이 가장 높은 종목을 대장주로 표시
        leaders = df_s.groupby('sector')['vol_ratio'].idxmax()
        for i, row in df_s.iterrows():
            leader_tag = " 🏆 **대장주**" if i in leaders.values else ""
            final_msg += f"- {row['name']}: (RSI:{row['rsi']:.1f}, 예상거래량:{row['vol_ratio']:.1f}배{leader_tag})\n"
    else: final_msg += "- 조건 충족 없음 (거래량/추세 미달)\n"
    
    final_msg += "\n✨ **A급: 안정적 추세안착**\n"
    if a_list:
        # A급은 상위 10개만 출력 (너무 많아지는 것 방지)
        for row in sorted(a_list, key=lambda x: x['vol_ratio'], reverse=True)[:10]:
            final_msg += f"- {row['name']}: (RSI:{row['rsi']:.1f}, 예상:{row['vol_ratio']:.1f}배)\n"
    else: final_msg += "- 없음\n"
    
    final_msg += "\n🔔 **익절/매도 검토 (리스크 관리)**\n" + ("\n".join(sell_list[:7]) if sell_list else "- 없음")
    
    send_telegram_message(final_msg)

if __name__ == "__main__":
    main()
