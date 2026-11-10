"""
Database module for managing SQLite database operations.
Handles ticker and historical data storage with proper indexing.
Uses a persistent connection with automatic reconnection and retry logic.
"""

import sqlite3
import os
import logging
import threading
import time
import functools
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Default retry settings
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # seconds, exponential backoff


def _with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator that retries a method on database errors, reconnecting if needed."""
    @functools.wraps(func)
    def wrapper(self: 'DatabaseManager', *args, **kwargs) -> T:
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._ensure_connected()
                return func(self, *args, **kwargs)
            except sqlite3.Error as e:
                last_error = e
                logger.warning(
                    f"Database error in {func.__name__} (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                # Force reconnect on next attempt
                self._force_reconnect()
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    time.sleep(delay)
        logger.error(f"All {MAX_RETRIES} retries exhausted for {func.__name__}: {last_error}")
        raise last_error
    return wrapper


class DatabaseManager:
    """Manages SQLite database operations for stock data with persistent connection."""
    
    def __init__(self, db_path: str):
        """
        Initialize the database manager.
        
        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._ensure_directory_exists()
        self.connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._connected = False
        
    def _ensure_directory_exists(self):
        """Ensure the database directory exists."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            logger.info(f"Created database directory: {db_dir}")
    
    def _ensure_connected(self):
        """Ensure the database connection is open; reconnect if needed."""
        with self._lock:
            if self.connection is not None:
                try:
                    # Lightweight connectivity check
                    self.connection.execute("SELECT 1")
                    return
                except sqlite3.Error:
                    logger.warning("Connection lost, will reconnect...")
                    self._close_connection_unlocked()

            self._open_connection_unlocked()

    def _open_connection_unlocked(self):
        """Open a new connection (caller must hold _lock)."""
        try:
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self._connected = True
            logger.info(f"Connected to database: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def _close_connection_unlocked(self):
        """Close the current connection (caller must hold _lock)."""
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
            self.connection = None
            self._connected = False

    def _force_reconnect(self):
        """Force close and reopen the database connection."""
        with self._lock:
            self._close_connection_unlocked()
            self._open_connection_unlocked()
            logger.info("Reconnected to database after error")
    
    def connect(self):
        """Establish connection to the database (kept for backward compatibility)."""
        self._ensure_connected()
    
    def disconnect(self):
        """Close the database connection."""
        with self._lock:
            self._close_connection_unlocked()
            logger.info("Disconnected from database")
    
    def shutdown(self):
        """Permanently close the database connection (call on application exit)."""
        self.disconnect()
    
    @_with_retry
    def initialize_tables(self):
        """Create database tables if they don't exist."""
        
        try:
            cursor = self.connection.cursor()
            
            # Create Tickers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    short_name TEXT,
                    long_name TEXT,
                    sector TEXT,
                    industry TEXT,
                    market_cap REAL,
                    currency TEXT,
                    exchange TEXT,
                    quote_type TEXT,
                    country TEXT,
                    website TEXT,
                    description TEXT,
                    employees INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create HistoricalData table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historical_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker_symbol TEXT NOT NULL,
                    date DATE NOT NULL,
                    open_price REAL,
                    close_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume INTEGER,
                    adj_close REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticker_symbol) REFERENCES tickers(symbol),
                    UNIQUE(ticker_symbol, date)
                )
            """)
            
            # Create indexes for efficient querying
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tickers_symbol 
                ON tickers(symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_historical_ticker_symbol 
                ON historical_data(ticker_symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_historical_date 
                ON historical_data(date)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_historical_ticker_date 
                ON historical_data(ticker_symbol, date)
            """)
            
            # Create UpdateHistory table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS update_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker_symbol TEXT NOT NULL,
                    update_type TEXT NOT NULL,
                    records_updated INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'success',
                    error_message TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticker_symbol) REFERENCES tickers(symbol)
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_history_ticker 
                ON update_history(ticker_symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_history_date 
                ON update_history(updated_at)
            """)
            
            self.connection.commit()
            logger.info("Database tables initialized successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize tables: {e}")
            raise
    
    @_with_retry
    def insert_ticker(self, ticker_data: Dict[str, Any]) -> bool:
        """
        Insert or update a ticker in the database.
        
        Args:
            ticker_data: Dictionary containing ticker information.
            
        Returns:
            True if successful, False otherwise.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO tickers (
                    symbol, short_name, long_name, sector, industry,
                    market_cap, currency, exchange, quote_type, country,
                    website, description, employees, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET
                    short_name = excluded.short_name,
                    long_name = excluded.long_name,
                    sector = excluded.sector,
                    industry = excluded.industry,
                    market_cap = excluded.market_cap,
                    currency = excluded.currency,
                    exchange = excluded.exchange,
                    quote_type = excluded.quote_type,
                    country = excluded.country,
                    website = excluded.website,
                    description = excluded.description,
                    employees = excluded.employees,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                ticker_data.get('symbol'),
                ticker_data.get('short_name'),
                ticker_data.get('long_name'),
                ticker_data.get('sector'),
                ticker_data.get('industry'),
                ticker_data.get('market_cap'),
                ticker_data.get('currency'),
                ticker_data.get('exchange'),
                ticker_data.get('quote_type'),
                ticker_data.get('country'),
                ticker_data.get('website'),
                ticker_data.get('description'),
                ticker_data.get('employees')
            ))
            self.connection.commit()
            return True
            
        except sqlite3.Error as e:
            logger.error(f"Failed to insert ticker {ticker_data.get('symbol')}: {e}")
            return False
    
    @_with_retry
    def insert_historical_data(self, ticker_symbol: str, data: List[Dict[str, Any]]) -> int:
        """
        Insert historical data for a ticker.
        
        Args:
            ticker_symbol: The ticker symbol.
            data: List of dictionaries containing historical data.
            
        Returns:
            Number of records inserted.
        """
        
        inserted = 0
        try:
            cursor = self.connection.cursor()
            for record in data:
                try:
                    cursor.execute("""
                        INSERT INTO historical_data (
                            ticker_symbol, date, open_price, close_price,
                            high_price, low_price, volume, adj_close
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker_symbol, date) DO UPDATE SET
                            open_price = excluded.open_price,
                            close_price = excluded.close_price,
                            high_price = excluded.high_price,
                            low_price = excluded.low_price,
                            volume = excluded.volume,
                            adj_close = excluded.adj_close
                    """, (
                        ticker_symbol,
                        record.get('date'),
                        record.get('open'),
                        record.get('close'),
                        record.get('high'),
                        record.get('low'),
                        record.get('volume'),
                        record.get('adj_close')
                    ))
                    inserted += 1
                except sqlite3.Error as e:
                    logger.warning(f"Failed to insert record for {ticker_symbol} on {record.get('date')}: {e}")
                    
            self.connection.commit()
            logger.info(f"Inserted {inserted} historical records for {ticker_symbol}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to insert historical data for {ticker_symbol}: {e}")
            
        return inserted
    
    @_with_retry
    def get_last_date_for_ticker(self, ticker_symbol: str) -> Optional[str]:
        """
        Get the last recorded date for a ticker.
        
        Args:
            ticker_symbol: The ticker symbol.
            
        Returns:
            The last date as a string, or None if no data exists.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT MAX(date) as last_date 
                FROM historical_data 
                WHERE ticker_symbol = ?
            """, (ticker_symbol,))
            
            result = cursor.fetchone()
            if result and result['last_date']:
                return result['last_date']
            return None
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get last date for {ticker_symbol}: {e}")
            return None
    
    @_with_retry
    def get_all_tickers(self) -> List[str]:
        """
        Get all ticker symbols from the database.
        
        Returns:
            List of ticker symbols.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT symbol FROM tickers")
            return [row['symbol'] for row in cursor.fetchall()]
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get tickers: {e}")
            return []
    
    @_with_retry
    def ticker_exists(self, symbol: str) -> bool:
        """
        Check if a ticker exists in the database.
        
        Args:
            symbol: The ticker symbol.
            
        Returns:
            True if the ticker exists, False otherwise.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT 1 FROM tickers WHERE symbol = ?", (symbol,))
            return cursor.fetchone() is not None
            
        except sqlite3.Error as e:
            logger.error(f"Failed to check ticker existence: {e}")
            return False
    
    @_with_retry
    def get_ticker_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a ticker.
        
        Args:
            symbol: The ticker symbol.
            
        Returns:
            Dictionary containing ticker information, or None if not found.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT symbol, short_name, long_name, sector, industry,
                       market_cap, currency, exchange, quote_type, country,
                       website, description, employees, created_at, updated_at
                FROM tickers WHERE symbol = ?
            """, (symbol,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'symbol': row['symbol'],
                    'short_name': row['short_name'],
                    'long_name': row['long_name'],
                    'sector': row['sector'],
                    'industry': row['industry'],
                    'market_cap': row['market_cap'],
                    'currency': row['currency'],
                    'exchange': row['exchange'],
                    'quote_type': row['quote_type'],
                    'country': row['country'],
                    'website': row['website'],
                    'description': row['description'],
                    'employees': row['employees'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                }
            return None
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get ticker info for {symbol}: {e}")
            return None
    
    @_with_retry
    def get_historical_data(
        self, 
        symbol: str, 
        start_date: str, 
        end_date: str
    ) -> List[Dict[str, Any]]:
        """
        Get historical data for a ticker within a date range.
        
        Args:
            symbol: The ticker symbol.
            start_date: Start date in 'YYYY-MM-DD' format.
            end_date: End date in 'YYYY-MM-DD' format.
            
        Returns:
            List of dictionaries containing historical data.
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT date, open_price, close_price, high_price, 
                       low_price, volume, adj_close
                FROM historical_data 
                WHERE ticker_symbol = ? AND date BETWEEN ? AND ?
                ORDER BY date ASC
            """, (symbol, start_date, end_date))
            
            records = []
            for row in cursor.fetchall():
                records.append({
                    'date': row['date'],
                    'open': row['open_price'],
                    'close': row['close_price'],
                    'high': row['high_price'],
                    'low': row['low_price'],
                    'volume': row['volume'],
                    'adj_close': row['adj_close']
                })
            
            return records
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get historical data for {symbol}: {e}")
            return []

    @_with_retry
    def insert_update_history(self, ticker_symbol: str, update_type: str,
                               records_updated: int = 0, status: str = 'success',
                               error_message: str = None) -> bool:
        """
        Insert an update history record for a ticker.
        
        Args:
            ticker_symbol: The ticker symbol.
            update_type: Type of update ('initial_sync', 'data_update', 'info_update', 'manual_add').
            records_updated: Number of records inserted/updated.
            status: 'success' or 'error'.
            error_message: Error message if status is 'error'.
            
        Returns:
            True if successful, False otherwise.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO update_history (
                    ticker_symbol, update_type, records_updated, status, error_message
                ) VALUES (?, ?, ?, ?, ?)
            """, (ticker_symbol, update_type, records_updated, status, error_message))
            self.connection.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to insert update history for {ticker_symbol}: {e}")
            return False

    @_with_retry
    def get_update_history(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get update history for a ticker.
        
        Args:
            symbol: The ticker symbol.
            limit: Maximum number of records to return.
            
        Returns:
            List of update history records.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT id, ticker_symbol, update_type, records_updated,
                       status, error_message, updated_at
                FROM update_history
                WHERE ticker_symbol = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (symbol, limit))
            
            records = []
            for row in cursor.fetchall():
                records.append({
                    'id': row['id'],
                    'ticker_symbol': row['ticker_symbol'],
                    'update_type': row['update_type'],
                    'records_updated': row['records_updated'],
                    'status': row['status'],
                    'error_message': row['error_message'],
                    'updated_at': row['updated_at']
                })
            return records
        except sqlite3.Error as e:
            logger.error(f"Failed to get update history for {symbol}: {e}")
            return []

    @_with_retry
    def get_update_summary(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get a summary of update history for a ticker.
        
        Args:
            symbol: The ticker symbol.
            
        Returns:
            Dictionary with summary info, or None if no history.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_updates,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_updates,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed_updates,
                    SUM(records_updated) as total_records_updated,
                    MIN(updated_at) as first_update,
                    MAX(updated_at) as last_update
                FROM update_history
                WHERE ticker_symbol = ?
            """, (symbol,))
            
            row = cursor.fetchone()
            if row and row['total_updates'] > 0:
                return {
                    'total_updates': row['total_updates'],
                    'successful_updates': row['successful_updates'],
                    'failed_updates': row['failed_updates'],
                    'total_records_updated': row['total_records_updated'],
                    'first_update': row['first_update'],
                    'last_update': row['last_update']
                }
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get update summary for {symbol}: {e}")
            return None
