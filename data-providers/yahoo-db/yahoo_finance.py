"""
Yahoo Finance module for fetching stock market data.
Uses yfinance library to interact with Yahoo Finance API.
Includes retry mechanism with exponential backoff for transient failures.
"""

import time
import yfinance as yf
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# Default retry settings for Yahoo Finance API calls
YF_MAX_RETRIES = 3
YF_RETRY_DELAY_BASE = 1.0  # seconds, exponential backoff


class YahooFinanceClient:
    """Client for interacting with Yahoo Finance API via yfinance."""
    
    def __init__(self, interval: str = "1d", period: str = "1y",
                 max_retries: int = YF_MAX_RETRIES,
                 retry_delay: float = YF_RETRY_DELAY_BASE):
        """
        Initialize the Yahoo Finance client.
        
        Args:
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo).
            period: History period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max).
            max_retries: Maximum number of retry attempts for API calls.
            retry_delay: Base delay in seconds between retries (exponential backoff).
        """
        self.interval = interval
        self.period = period
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
    def get_nasdaq_tickers(self) -> List[str]:
        """
        Get a list of all tickers available on NASDAQ exchange.
        
        Note: yfinance doesn't provide a direct endpoint for this,
        so we use the NASDAQ traded list from a well-known source.
        
        Returns:
            List of ticker symbols.
        """
        try:
            # Use pandas to read NASDAQ traded symbols
            # This URL provides NASDAQ listed companies
            url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
            
            df = pd.read_csv(url, sep="|")
            # Remove the last row which contains file creation time
            df = df[:-1]
            
            # Get the symbol column
            tickers = df['Symbol'].tolist()
            logger.info(f"Retrieved {len(tickers)} NASDAQ tickers")
            return tickers
            
        except Exception as e:
            logger.error(f"Failed to fetch NASDAQ tickers: {e}")
            # Fallback: return some well-known NASDAQ tickers
            fallback_tickers = [
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA',
                'NFLX', 'ADBE', 'INTC', 'CSCO', 'CMCSA', 'PEP', 'AVGO',
                'TXN', 'QCOM', 'COST', 'TMUS', 'SBUX', 'AMGN', 'GILD',
                'ISRG', 'VRTX', 'REGN', 'LRCX', 'MU', 'ATVI', 'ADP',
                'MDLZ', 'BKNG', 'AMAT', 'ADI', 'ILMN', 'BIIB', 'CSX',
                'MNST', 'KDP', 'MRNA', 'KLAC', 'SNPS', 'CDNS', 'WDAY',
                'MELI', 'PANW', 'DXCM', 'TEAM', 'CRWD', 'FTNT', 'ANSS'
            ]
            logger.warning(f"Using fallback list of {len(fallback_tickers)} tickers")
            return fallback_tickers
    
    def get_nyse_tickers(self) -> List[str]:
        """
        Get a list of all tickers available on NYSE (New York Stock Exchange).
        
        Downloads the list of NYSE-listed companies from the official NASDAQ trader
        data source which includes NYSE listings.
        
        Returns:
            List of ticker symbols traded on NYSE.
        """
        try:
            # Use pandas to read NYSE symbols from NASDAQ trader data
            # This URL provides other (non-NASDAQ) listed companies including NYSE
            url = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
            
            df = pd.read_csv(url, sep="|")
            # Remove the last row which contains file creation time
            df = df[:-1]
            
            # Filter for NYSE exchange (Exchange column: 'N' = NYSE, 'A' = NYSE American/AMEX, etc.)
            # The 'Exchange' column contains: N=NYSE, A=NYSE American, P=NYSE Arca, Z=BATS, V=IEXG
            nyse_df = df[df['Exchange'] == 'N']
            
            # Get the ACT Symbol column (the actual trading symbol)
            tickers = nyse_df['ACT Symbol'].tolist()
            
            # Clean up symbols (remove any special characters or test symbols)
            cleaned_tickers = []
            for ticker in tickers:
                if isinstance(ticker, str) and ticker.strip():
                    # Skip test symbols (usually contain '$' or other special chars)
                    clean_ticker = ticker.strip()
                    if clean_ticker.isalpha() or (clean_ticker.replace('.', '').replace('-', '').isalnum()):
                        cleaned_tickers.append(clean_ticker)
            
            logger.info(f"Retrieved {len(cleaned_tickers)} NYSE tickers")
            return cleaned_tickers
            
        except Exception as e:
            logger.error(f"Failed to fetch NYSE tickers: {e}")
            # Fallback: return some well-known NYSE tickers
            fallback_tickers = [
                'JPM', 'JNJ', 'V', 'PG', 'UNH', 'HD', 'MA', 'DIS', 'BAC',
                'VZ', 'KO', 'MRK', 'PFE', 'WMT', 'ABT', 'CVX', 'XOM',
                'T', 'ABBV', 'TMO', 'NKE', 'MCD', 'DHR', 'ACN', 'LLY',
                'NEE', 'BMY', 'ORCL', 'UPS', 'PM', 'TXN', 'HON', 'UNP',
                'RTX', 'LOW', 'IBM', 'SPGI', 'CVS', 'GS', 'CAT', 'BLK',
                'DE', 'AXP', 'MMM', 'BA', 'GE', 'F', 'GM', 'C'
            ]
            logger.warning(f"Using fallback list of {len(fallback_tickers)} NYSE tickers")
            return fallback_tickers
    
    def get_nysearca_tickers(self) -> List[str]:
        """
        Get a list of all tickers available on NYSE Arca exchange.
        
        NYSE Arca is known for listing many ETFs including BITB, SPY, QQQ, etc.
        Downloads the list from the official NASDAQ trader data source.
        
        Returns:
            List of ticker symbols traded on NYSE Arca.
        """
        try:
            # Use pandas to read NYSE Arca symbols from NASDAQ trader data
            # This URL provides other (non-NASDAQ) listed companies
            url = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
            
            df = pd.read_csv(url, sep="|")
            # Remove the last row which contains file creation time
            df = df[:-1]
            
            # Filter for NYSE Arca exchange (Exchange column: 'P' = NYSE Arca)
            # The 'Exchange' column contains: N=NYSE, A=NYSE American, P=NYSE Arca, Z=BATS, V=IEXG
            nysearca_df = df[df['Exchange'] == 'P']
            
            # Get the ACT Symbol column (the actual trading symbol)
            tickers = nysearca_df['ACT Symbol'].tolist()
            
            # Clean up symbols (remove any special characters or test symbols)
            cleaned_tickers = []
            for ticker in tickers:
                if isinstance(ticker, str) and ticker.strip():
                    # Skip test symbols (usually contain '$' or other special chars)
                    clean_ticker = ticker.strip()
                    if clean_ticker.isalpha() or (clean_ticker.replace('.', '').replace('-', '').isalnum()):
                        cleaned_tickers.append(clean_ticker)
            
            logger.info(f"Retrieved {len(cleaned_tickers)} NYSE Arca tickers")
            return cleaned_tickers
            
        except Exception as e:
            logger.error(f"Failed to fetch NYSE Arca tickers: {e}")
            # Fallback: return some well-known NYSE Arca tickers (mostly ETFs)
            fallback_tickers = [
                'BITB', 'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'VEA',
                'VWO', 'EFA', 'EEM', 'GLD', 'SLV', 'USO', 'UNG', 'TLT',
                'IEF', 'LQD', 'HYG', 'JNK', 'XLF', 'XLE', 'XLK', 'XLV',
                'XLI', 'XLP', 'XLY', 'XLB', 'XLU', 'XLRE', 'VNQ', 'ARKK',
                'ARKW', 'ARKG', 'ARKF', 'ARKQ', 'IBIT', 'FBTC', 'BITO',
                'GBTC', 'ETHE', 'COIN', 'MARA', 'RIOT', 'CLSK', 'HUT'
            ]
            logger.warning(f"Using fallback list of {len(fallback_tickers)} NYSE Arca tickers")
            return fallback_tickers
    
    def get_ticker_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a ticker with retry on failure.
        
        Args:
            symbol: The ticker symbol.
            
        Returns:
            Dictionary containing ticker information, or None if failed.
        """
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                
                if not info or 'symbol' not in info:
                    logger.warning(f"No info available for {symbol} (attempt {attempt}/{self.max_retries})")
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    return None
                
                return {
                    'symbol': symbol,
                    'short_name': info.get('shortName'),
                    'long_name': info.get('longName'),
                    'sector': info.get('sector'),
                    'industry': info.get('industry'),
                    'market_cap': info.get('marketCap'),
                    'currency': info.get('currency'),
                    'exchange': info.get('exchange'),
                    'quote_type': info.get('quoteType'),
                    'country': info.get('country'),
                    'website': info.get('website'),
                    'description': info.get('longBusinessSummary'),
                    'employees': info.get('fullTimeEmployees')
                }
                
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get info for {symbol} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    time.sleep(delay)
        
        logger.error(f"All {self.max_retries} retries exhausted for get_ticker_info({symbol}): {last_error}")
        return None

    def search_symbols(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Look up symbols on Yahoo Finance by ticker or company name (single attempt, for type-ahead).

        Returns:
            List of dicts with symbol, short_name, long_name, exchange, quote_type.

        Raises:
            RuntimeError: If the installed yfinance has no search support.
        """
        search_cls = getattr(yf, 'Search', None)
        if search_cls is None:
            raise RuntimeError("Installed yfinance does not support search (upgrade yfinance)")
        search = search_cls(query, max_results=limit, news_count=0, lists_count=0,
                            include_cb=False, recommended=0, timeout=10)
        return [
            {
                'symbol': quote['symbol'],
                'short_name': quote.get('shortname'),
                'long_name': quote.get('longname'),
                'exchange': quote.get('exchDisp') or quote.get('exchange'),
                'quote_type': quote.get('quoteType'),
            }
            for quote in (search.quotes or [])[:limit]
            if quote.get('symbol')
        ]
    
    def get_historical_data(
        self, 
        symbol: str, 
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get historical stock data for a ticker with retry on failure.
        
        Args:
            symbol: The ticker symbol.
            start_date: Start date in 'YYYY-MM-DD' format (optional).
            end_date: End date in 'YYYY-MM-DD' format (optional).
            period: Period to fetch (e.g., '1y', '6mo'). Used if start_date is None.
            
        Returns:
            List of dictionaries containing historical data.
        """
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                ticker = yf.Ticker(symbol)
                
                if start_date:
                    if end_date is None:
                        end_date = datetime.now().strftime('%Y-%m-%d')
                        
                    history = ticker.history(
                        start=start_date,
                        end=end_date,
                        interval=self.interval
                    )
                else:
                    use_period = period or self.period
                    history = ticker.history(
                        period=use_period,
                        interval=self.interval
                    )
                
                if history.empty:
                    logger.warning(f"No historical data for {symbol} (attempt {attempt}/{self.max_retries})")
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    return []
                
                records = []
                for date, row in history.iterrows():
                    records.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'open': row.get('Open'),
                        'close': row.get('Close'),
                        'high': row.get('High'),
                        'low': row.get('Low'),
                        'volume': int(row.get('Volume', 0)) if pd.notna(row.get('Volume')) else 0,
                        'adj_close': row.get('Close')
                    })
                
                logger.info(f"Retrieved {len(records)} historical records for {symbol}")
                return records
                
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get historical data for {symbol} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    time.sleep(delay)
        
        logger.error(f"All {self.max_retries} retries exhausted for get_historical_data({symbol}): {last_error}")
        return []
    
    def get_historical_data_since(self, symbol: str, last_date: str) -> List[Dict[str, Any]]:
        """
        Get historical data for a ticker since a specific date.
        
        Args:
            symbol: The ticker symbol.
            last_date: The last recorded date in 'YYYY-MM-DD' format.
            
        Returns:
            List of dictionaries containing new historical data.
        """
        # Start from the day after the last recorded date
        start_date = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        
        if start_date >= end_date:
            logger.info(f"No new data to fetch for {symbol}, already up to date")
            return []
        
        return self.get_historical_data(symbol, start_date=start_date, end_date=end_date)
