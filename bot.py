def main():
    sentiment, mkt_report = get_market_sentiment()
    my_stock_codes = load_portfolio()
    
    # 1. KRX 전종목 리스팅
    df_krx = fdr.StockListing('KRX')
    
    # [수정] 컬럼명이 'MarketCap'인지 'MarCap'인지 확인하여 유연하게 대응
    mcap_col = 'MarketCap' if 'MarketCap' in df_krx.columns else 'MarCap'
    
    # 2. 건실한 기업 필터링 로직
    # - 시가총액(mcap_col) 2,000억 이상
    # - PBR 0.3 이상
    # - 바이오 섹터이면서 적자인 종목 제외
    bio_keywords = '의약|제약|바이오|생물|헬스케어'
    
    # 컬럼 존재 여부를 확인하며 안전하게 마스크 생성
    mask = (df_krx[mcap_col] >= 200_000_000_000)
    if 'PBR' in df_krx.columns:
        mask &= (df_krx['PBR'] >= 0.3)
        
    is_red_bio = pd.Series(False, index=df_krx.index)
    if 'PER' in df_krx.columns and 'Sector' in df_krx.columns:
        is_red_bio = (df_krx['PER'] <= 0) & (df_krx['Sector'].str.contains(bio_keywords, na=False))
    
    healthy_stocks = df_krx[mask & ~is_red_bio]
    
    # 건실한 종목 중 상위 350개를 분석 대상으로 선정
    total_market = healthy_stocks.sort_values(by=mcap_col, ascending=False).head(350)
    
    # 이름 매핑
    name_map = dict(zip(df_krx['Code'], df_krx['Name']))
    
    # ... (이하 동일)
