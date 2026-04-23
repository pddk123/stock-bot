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

def get_krx_sectors():
    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        df = pd.read_html(url, header=0)[0]
        df['종목코드'] = df['종목코드'].apply(lambda x: f"{x:06d}")
        return df[['종목코드', '업종']].rename(columns={'종목코드':'Code', '업종':'Sector'})
    except:
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
    
    avg_chg = total_chg / len(indices)
    reason = f"글로벌 지수 평균 {avg_chg:+.2f}% 등락 및 주요 지수 {scores}/4 상승세 기반"
    if scores >= 3 and avg_chg > 0.4: status = "🚀 **강력 상승장**"
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
        df['MA10'], df['MA20'] = df['Close'].rolling(10).mean(), df['Close'].rolling(20).mean()
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['RSI'] = calculate_rsi(df['Close'])
        curr = df.iloc[-1]
        vol_ratio, rsi_val = curr['Volume'] / curr['Vol_MA5'], df['RSI'].iloc[-1]
        
        res = {'name': name, 'symbol': symbol, 'sector': sector, 'is_portfolio': is_portfolio, 
               'grade': None, 'rsi': rsi_val, 'vol_ratio': vol_ratio, 'sell_desc': ""}

        # S급: 추세 폭발 초입 (엄격한 AND 조건)
        if (45 <= rsi_val <= 62) and (curr['Close'] > curr['MA10'] > curr['MA20']) and (vol_ratio >= 1.5):
            res.update({'grade': 'S'})
        # A급: 안정적 추세안착 (강세 유지)
        elif (rsi_val > 50) and (curr['Close'] > curr['MA10']) and (vol_ratio >= 1.0):
            res.update({'grade': 'A'})

        if rsi_val >= 80: res['sell_desc'] = f"🔥 과열(RSI:{rsi_val:.1f})"
        return res
    except: return None

def main():
    mkt_status, mkt_reason, mkt_report = get_market_sentiment()
    my_codes = load_portfolio()
    
    # 350개로 압축 스캔
    all_stocks = pd.merge(fdr.StockListing('KRX'), get_krx_sectors(), on='Code', how='left').fillna({'Sector': '기타'})
    robust_market = all_stocks[all_stocks['Marcap'] >= 150_000_000_000].head(350)
    
    portfolio_res, s_list, a_list, sell_list = [], [], [], []
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        # 1. 포트폴리오 (무조건 분석)
        p_tasks = [executor.submit(analyze_stock, code, row['Name'], row['Sector'], True) 
                   for code in my_codes for _, row in all_stocks[all_stocks['Code']==code].iterrows()]
        # 2. 시장 스캐닝 (상위 350개)
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

    final_msg = f"🌿 **rootee님, 실시간 투자 리포트**\n\n"
    final_msg += f"📊 **시장 진단**: {mkt_status}\n🧐 **판단 근거**: {mkt_reason}\n{mkt_report}\n\n"
    
    # 주도 섹터 분석
    combined = pd.DataFrame(s_list + a_list)
    if not combined.empty and not combined[combined['sector'] != '기타'].empty:
        top_s = combined[combined['sector'] != '기타']['sector'].value_counts().head(2).index.tolist()
        final_msg += f"🔥 **현재 주도 섹터**: {', '.join(top_s)}\n\n"

    final_msg += "📁 **내 보유 종목**\n" + ("\n".join(portfolio_res) if portfolio_res else "- 없음") + "\n\n"
    
    # S급 (섹터 미표기, 대장주 마킹)
    final_msg += "💎 **S급: 추세 폭발 초입**\n"
    if s_list:
        df_s = pd.DataFrame(s_list)
        leaders = df_s.groupby('sector')['vol_ratio'].idxmax()
        for i, row in df_s.iterrows():
            leader_tag = " 🏆 **대장주**" if i in leaders.values else ""
            final_msg += f"- {row['name']}: (RSI:{row['rsi']:.1f}, 거래량:{row['vol_ratio']:.1f}배{leader_tag})\n"
    else: final_msg += "- 조건 충족 없음\n"
    
    # A급 (간결하게 10개만)
    final_msg += "\n✨ **A급: 안정적 추세안착**\n"
    if a_list:
        for row in a_list[:10]:
            final_msg += f"- {row['name']}: (RSI:{row['rsi']:.1f}, 거래량:{row['vol_ratio']:.1f}배)\n"
    else: final_msg += "- 조건 충족 없음\n"
    
    final_msg += "\n🔔 **익절/매도 검토 (리스크 관리)**\n" + ("\n".join(sell_list[:7]) if sell_list else "- 과열 종목 없음")
    
    send_telegram_message(final_msg)

if __name__ == "__main__":
    main()
