"""
Stock Data Service - Flask Web Application v0.0.0.7
Web-based application for managing Yahoo Finance stock data synchronization.
Supports NYSE, NASDAQ, and NYSE Arca ticker synchronization.
Uses a persistent database connection with automatic reconnection.
"""

import atexit
import json
import os
import sys
import time
import logging
import logging.handlers
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set, Optional

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

from database import DatabaseManager
from split_check import SplitChecker
from yahoo_finance import YahooFinanceClient

# Ensure UTF-8 output on all platforms (important for Windows cp1252 console)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Log file next to this script (cross-platform absolute path)
_LOG_FILE = str(Path(__file__).parent / 'stock_service.log')

# If the log file exists but isn't writable (e.g. owned by root), remove and recreate it
if os.path.exists(_LOG_FILE) and not os.access(_LOG_FILE, os.W_OK):
    try:
        os.remove(_LOG_FILE)
    except OSError:
        # Fall back: write to a user-writable location
        _LOG_FILE = str(Path(__file__).parent / 'stock_service_user.log')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.WatchedFileHandler(_LOG_FILE, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Flask application
app = Flask(__name__)
CORS(app)


class StockDataService:
    """Main service for managing stock data synchronization."""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Initialize the Stock Data Service.
        
        Args:
            config_path: Path to the configuration file.
        """
        self.config = self._load_config(config_path)
        self.base_path = Path(os.path.dirname(os.path.abspath(__file__)))
        
        # Initialize components
        db_path = self._resolve_path(self.config['database']['file_path'])
        self.db = DatabaseManager(db_path)
        
        self.yahoo_client = YahooFinanceClient(
            interval=self.config['yahoo_finance']['data_interval'],
            period=self.config['yahoo_finance']['history_period'],
            max_retries=self.config['yahoo_finance'].get('max_retries', 3),
            retry_delay=self.config['yahoo_finance'].get('retry_delay', 1.0)
        )
        
        self.request_delay = self.config['yahoo_finance'].get('request_delay', 0.3)
        self.splits = SplitChecker(self.db, self.yahoo_client)
        
        self.tickers_file_path = self._resolve_path(
            self.config['files']['nasdaq_tickers']
        )
        
        # Sync status tracking
        self.sync_running = False
        self.sync_thread: Optional[threading.Thread] = None
        self.last_sync_time: Optional[datetime] = None
        self.sync_status = "idle"
        self.sync_progress = {"current": 0, "total": 0, "current_ticker": ""}
        
        # Load last sync time from file if exists
        self._load_last_sync_time()
        
        # Establish persistent database connection and initialise tables
        self.db.connect()
        self.db.initialize_tables()
        logger.info("Persistent database connection established")
        
    def _resolve_path(self, relative_path: str) -> str:
        """Resolve a relative path to an absolute path."""
        if os.path.isabs(relative_path):
            return relative_path
        return str(self.base_path / relative_path)
    
    def _load_config(self, config_path: str) -> dict:
        """
        Load configuration from JSON file.
        
        Args:
            config_path: Path to the configuration file.
            
        Returns:
            Configuration dictionary.
        """
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in configuration file: {e}")
            raise
    
    def _load_last_sync_time(self):
        """Load the last sync time from the tickers file."""
        try:
            if os.path.exists(self.tickers_file_path):
                with open(self.tickers_file_path, 'r') as f:
                    data = json.load(f)
                    last_updated = data.get('last_updated')
                    if last_updated:
                        self.last_sync_time = datetime.fromisoformat(last_updated)
        except Exception as e:
            logger.warning(f"Failed to load last sync time: {e}")
    
    def _load_saved_tickers(self) -> Set[str]:
        """
        Load previously saved tickers from JSON file.
        
        Returns:
            Set of ticker symbols.
        """
        try:
            if os.path.exists(self.tickers_file_path):
                with open(self.tickers_file_path, 'r') as f:
                    data = json.load(f)
                    return set(data.get('tickers', []))
            return set()
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load saved tickers: {e}")
            return set()
    
    def _save_tickers(self, tickers: List[str]):
        """
        Save tickers to JSON file.
        
        Args:
            tickers: List of ticker symbols to save.
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.tickers_file_path), exist_ok=True)
            
            self.last_sync_time = datetime.now()
            with open(self.tickers_file_path, 'w') as f:
                json.dump({
                    'tickers': tickers,
                    'last_updated': self.last_sync_time.isoformat(),
                    'count': len(tickers)
                }, f, indent=2)
            logger.info(f"Saved {len(tickers)} tickers to {self.tickers_file_path}")
        except IOError as e:
            logger.error(f"Failed to save tickers: {e}")
    
    def get_status(self) -> dict:
        """Get current service status."""
        ticker_count = len(self.db.get_all_tickers())
        
        return {
            "sync_running": self.sync_running,
            "sync_status": self.sync_status,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "ticker_count": ticker_count,
            "progress": self.sync_progress
        }
    
    def start_sync(self):
        """Start the synchronization process in a background thread."""
        if self.sync_running:
            return {"error": "Sync already running", "status": "error"}
        
        self.sync_thread = threading.Thread(target=self._run_sync_thread)
        self.sync_thread.daemon = True
        self.sync_thread.start()
        
        return {"message": "Sync started", "status": "success"}
    
    def stop_sync(self):
        """Stop the synchronization process."""
        if not self.sync_running:
            return {"error": "No sync is running", "status": "error"}
        
        self.sync_running = False
        self.sync_status = "stopping"
        
        return {"message": "Sync stop requested", "status": "success"}
    
    def _run_sync_thread(self):
        """Run the synchronization in a thread."""
        self.sync_running = True
        self.sync_status = "running"
        self.sync_progress = {"current": 0, "total": 0, "current_ticker": ""}
        
        try:
            self.run_sync()
            self.sync_status = "completed"
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            self.sync_status = f"error: {str(e)}"
        finally:
            self.sync_running = False
    
    def run_sync(self):
        """
        Run the main synchronization process.
        
        This method:
        1. Gets all NASDAQ, NYSE, and NYSE Arca tickers
        2. Compares with saved tickers to find new ones
        3. For new tickers: fetches full historical data
        4. For existing tickers: updates with recent data
        5. Saves updated ticker list
        """
        logger.info("=" * 60)
        logger.info(f"Starting stock data synchronization at {datetime.now()}")
        logger.info("=" * 60)
        
        try:
            # Step 1: Get current NASDAQ tickers
            logger.info("Step 1a: Fetching NASDAQ tickers...")
            nasdaq_tickers = self.yahoo_client.get_nasdaq_tickers()
            logger.info(f"Retrieved {len(nasdaq_tickers)} NASDAQ tickers")
            
            # Step 1b: Get current NYSE tickers
            logger.info("Step 1b: Fetching NYSE tickers...")
            nyse_tickers = self.yahoo_client.get_nyse_tickers()
            logger.info(f"Retrieved {len(nyse_tickers)} NYSE tickers")
            
            # Step 1c: Get current NYSE Arca tickers
            logger.info("Step 1c: Fetching NYSE Arca tickers...")
            nysearca_tickers = self.yahoo_client.get_nysearca_tickers()
            logger.info(f"Retrieved {len(nysearca_tickers)} NYSE Arca tickers")
            
            # Combine all tickers (NASDAQ + NYSE + NYSE Arca)
            current_tickers_set = set(nasdaq_tickers) | set(nyse_tickers) | set(nysearca_tickers)
            logger.info(f"Total unique tickers: {len(current_tickers_set)}")
            
            # Step 2: Load saved tickers and compare
            logger.info("Step 2: Comparing with saved tickers...")
            saved_tickers = self._load_saved_tickers()
            
            new_tickers = current_tickers_set - saved_tickers
            existing_tickers = current_tickers_set & saved_tickers
            
            logger.info(f"Found {len(new_tickers)} new tickers")
            logger.info(f"Found {len(existing_tickers)} existing tickers")
            
            # Step 3 & 4: Process new tickers (full historical data)
            if new_tickers and self.sync_running:
                logger.info(f"Step 3: Processing {len(new_tickers)} new tickers...")
                self._process_new_tickers(list(new_tickers))
            
            # Step 5: Update existing tickers with recent data
            if existing_tickers and self.sync_running:
                logger.info(f"Step 4: Updating {len(existing_tickers)} existing tickers...")
                self._update_existing_tickers(list(existing_tickers))
            
            # Step 6: Save updated ticker list
            if self.sync_running:
                logger.info("Step 5: Saving updated ticker list...")
                self._save_tickers(list(current_tickers_set))
            
            logger.info("=" * 60)
            logger.info("Stock data synchronization completed successfully!")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Synchronization failed: {e}")
            raise
    
    def _process_new_tickers(self, tickers: List[str]):
        """
        Process new tickers by fetching full historical data.
        
        Args:
            tickers: List of new ticker symbols.
        """
        total = len(tickers)
        self.sync_progress["total"] = total
        
        for i, symbol in enumerate(tickers, 1):
            if not self.sync_running:
                logger.info("Sync stopped by user")
                break
                
            try:
                self.sync_progress["current"] = i
                self.sync_progress["current_ticker"] = symbol
                logger.info(f"Processing new ticker {i}/{total}: {symbol}")
                
                # Get ticker info
                ticker_info = self.yahoo_client.get_ticker_info(symbol)
                if ticker_info:
                    self.db.insert_ticker(ticker_info)
                else:
                    # Insert minimal ticker info
                    self.db.insert_ticker({'symbol': symbol})
                
                # Get full historical data
                historical_data = self.yahoo_client.get_historical_data(symbol)
                records_count = 0
                if historical_data:
                    records_count = self.db.insert_historical_data(symbol, historical_data)
                    self.splits.register_initial(symbol, historical_data)
                
                # Record update history
                self.db.insert_update_history(
                    ticker_symbol=symbol,
                    update_type='initial_sync',
                    records_updated=records_count,
                    status='success'
                )
                
                # Delay to avoid rate limiting
                time.sleep(self.request_delay)
                
            except Exception as e:
                logger.error(f"Failed to process ticker {symbol}: {e}")
                self.db.insert_update_history(
                    ticker_symbol=symbol,
                    update_type='initial_sync',
                    records_updated=0,
                    status='error',
                    error_message=str(e)
                )
                continue
    
    def _update_existing_tickers(self, tickers: List[str]):
        """
        Update existing tickers by fetching full historical data and merging with DB.
        
        This fetches the complete 10-year history and merges with existing data,
        handling any gaps from days when the service didn't run.
        
        Args:
            tickers: List of existing ticker symbols.
        """
        total = len(tickers)
        self.sync_progress["total"] = total
        
        for i, symbol in enumerate(tickers, 1):
            if not self.sync_running:
                logger.info("Sync stopped by user")
                break
                
            try:
                self.sync_progress["current"] = i
                self.sync_progress["current_ticker"] = symbol
                
                last_date = self.db.get_last_date_for_ticker(symbol)
                first_date = self.db.get_first_date_for_ticker(symbol)
                # One-time check over the whole stored life: download from the first stored day.
                full_check = first_date is not None and self.splits.needs_full_check(symbol)
                logger.info(f"Updating ticker {i}/{total}: {symbol} (last date in DB: {last_date or 'none'}"
                            f"{', full split check' if full_check else ''})")
                
                if full_check:
                    historical_data = self.yahoo_client.get_historical_data(symbol, start_date=first_date)
                elif last_date and (datetime.now() - datetime.strptime(last_date, '%Y-%m-%d')).days > 25:
                    # Not updated for a while: cover the whole gap so no split in it is missed.
                    gap_start = (datetime.strptime(last_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
                    historical_data = self.yahoo_client.get_historical_data(symbol, start_date=gap_start)
                else:
                    historical_data = self.yahoo_client.get_historical_data(symbol)
                
                records_count = 0
                if historical_data:
                    result = self.splits.store_update(symbol, historical_data, covers_history=full_check)
                    records_count = result['records']
                
                # Update ticker info
                ticker_info = self.yahoo_client.get_ticker_info(symbol)
                if ticker_info:
                    self.db.insert_ticker(ticker_info)
                
                # Record update history
                self.db.insert_update_history(
                    ticker_symbol=symbol,
                    update_type='data_update',
                    records_updated=records_count,
                    status='success'
                )
                
                # Delay to avoid rate limiting
                time.sleep(self.request_delay)
                
            except Exception as e:
                logger.error(f"Failed to update ticker {symbol}: {e}")
                self.db.insert_update_history(
                    ticker_symbol=symbol,
                    update_type='data_update',
                    records_updated=0,
                    status='error',
                    error_message=str(e)
                )
                continue
    
    def add_ticker(self, symbol: str) -> dict:
        """
        Add a new ticker to the database with its historical data.
        
        Args:
            symbol: The ticker symbol to add.
            
        Returns:
            Result dictionary with status.
        """
        symbol = symbol.upper().strip()
        
        if not symbol:
            return {"error": "Ticker symbol is required", "status": "error"}
        
        try:
            # Check if ticker already exists
            if self.db.ticker_exists(symbol):
                return {"error": f"Ticker {symbol} already exists", "status": "error"}
            
            # Get ticker info from Yahoo Finance
            logger.info(f"Adding new ticker: {symbol}")
            ticker_info = self.yahoo_client.get_ticker_info(symbol)
            
            if ticker_info:
                self.db.insert_ticker(ticker_info)
            else:
                # Try to insert with just the symbol
                self.db.insert_ticker({'symbol': symbol})
            
            # Get historical data
            historical_data = self.yahoo_client.get_historical_data(symbol)
            records_count = 0
            if historical_data:
                records_count = self.db.insert_historical_data(symbol, historical_data)
                self.splits.register_initial(symbol, historical_data)
                
            # Record update history
            self.db.insert_update_history(
                ticker_symbol=symbol,
                update_type='manual_add',
                records_updated=records_count,
                status='success'
            )
            
            # Update saved tickers file
            saved_tickers = self._load_saved_tickers()
            saved_tickers.add(symbol)
            self._save_tickers(list(saved_tickers))
            
            return {
                "message": f"Ticker {symbol} added successfully",
                "status": "success",
                "records_added": records_count
            }
            
        except Exception as e:
            logger.error(f"Failed to add ticker {symbol}: {e}")
            return {"error": str(e), "status": "error"}


# Initialize service
service = StockDataService()

# Register shutdown hook to close the persistent database connection
atexit.register(service.db.shutdown)


# ============== Web Routes ==============

@app.route('/')
def index():
    """Render the main dashboard page."""
    return render_template('index.html')


@app.route('/status')
def status():
    """Get current application status."""
    return jsonify(service.get_status())


# ============== REST API Routes ==============

@app.route('/api/sync/start', methods=['POST'])
def api_start_sync():
    """Start database synchronization."""
    result = service.start_sync()
    return jsonify(result)


@app.route('/api/sync/stop', methods=['POST'])
def api_stop_sync():
    """Stop database synchronization."""
    result = service.stop_sync()
    return jsonify(result)


@app.route('/api/sync/status', methods=['GET'])
def api_sync_status():
    """Get synchronization status."""
    return jsonify(service.get_status())


@app.route('/api/ticker', methods=['POST'])
def api_add_ticker():
    """
    Add a new ticker to the database.
    
    Request body: {"symbol": "AAPL"}
    """
    data = request.get_json()
    if not data or 'symbol' not in data:
        return jsonify({"error": "Symbol is required", "status": "error"}), 400
    
    result = service.add_ticker(data['symbol'])
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@app.route('/api/ticker/<symbol>', methods=['GET'])
def api_get_ticker_info(symbol: str):
    """
    Get detailed information about a ticker.
    
    Args:
        symbol: The ticker symbol.
    """
    try:
        ticker_info = service.db.get_ticker_info(symbol.upper())
        
        if ticker_info:
            return jsonify({"status": "success", "data": ticker_info})
        else:
            return jsonify({"error": f"Ticker {symbol} not found", "status": "error"}), 404
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/api/ticker/<symbol>/history', methods=['GET'])
def api_get_ticker_history(symbol: str):
    """
    Get historical stock price for a ticker.
    
    Query parameters:
        - days: Number of days (e.g., days=30)
        - months: Number of months (e.g., months=6)
        - years: Number of years (e.g., years=2)
        - start_date: Start date in YYYY-MM-DD format
        - end_date: End date in YYYY-MM-DD format
        
    If no parameters provided, returns last 30 days.
    """
    try:
        # Check if ticker exists
        if not service.db.ticker_exists(symbol.upper()):
            return jsonify({"error": f"Ticker {symbol} not found", "status": "error"}), 404
        
        # Parse query parameters
        days = request.args.get('days', type=int)
        months = request.args.get('months', type=int)
        years = request.args.get('years', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Calculate date range
        if start_date and end_date:
            # Use provided date range
            pass
        elif days:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        elif months:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=months * 30)).strftime('%Y-%m-%d')
        elif years:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=years * 365)).strftime('%Y-%m-%d')
        else:
            # Default: last 30 days
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        history = service.db.get_historical_data(symbol.upper(), start_date, end_date)
        
        return jsonify({
            "status": "success",
            "symbol": symbol.upper(),
            "start_date": start_date,
            "end_date": end_date,
            "count": len(history),
            "data": history
        })
        
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/api/tickers', methods=['GET'])
def api_get_all_tickers():
    """Get list of all tickers in the database."""
    try:
        tickers = service.db.get_all_tickers()
        
        return jsonify({
            "status": "success",
            "count": len(tickers),
            "tickers": tickers
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/api/search', methods=['GET'])
def api_search_tickers():
    """
    Search tickers by symbol or company name.
    
    Query parameters:
        - q: Search text (1-64 characters), e.g. "AAP" or "apple"
        - limit: Maximum number of results (1-50, default: 10)
        - source: "db" (default) searches tickers in the database; "yahoo" looks up
          all of Yahoo Finance and flags results already in the database (in_database)
    """
    query = (request.args.get('q') or '').strip()
    if not query or len(query) > 64:
        return jsonify({"error": "Query parameter 'q' must be 1-64 characters", "status": "error"}), 400
    limit = request.args.get('limit', 10, type=int)
    if limit is None or not 1 <= limit <= 50:
        return jsonify({"error": "Query parameter 'limit' must be between 1 and 50", "status": "error"}), 400
    source = request.args.get('source', 'db')
    if source not in ('db', 'yahoo'):
        return jsonify({"error": "Query parameter 'source' must be 'db' or 'yahoo'", "status": "error"}), 400
    try:
        if source == 'yahoo':
            results = service.yahoo_client.search_symbols(query, limit)
            for item in results:
                item['in_database'] = service.db.ticker_exists(item['symbol'].upper())
        else:
            results = service.db.search_tickers(query, limit)
        return jsonify({
            "status": "success",
            "query": query,
            "source": source,
            "count": len(results),
            "data": results
        })
    except Exception as e:
        logger.error(f"Ticker search ({source}) failed for {query!r}: {e}")
        if source == 'yahoo':
            return jsonify({"error": "Yahoo Finance search unavailable", "status": "error"}), 503
        return jsonify({"error": "Search failed", "status": "error"}), 500


@app.route('/api/ticker/<symbol>/updates', methods=['GET'])
def api_get_ticker_updates(symbol: str):
    """
    Get update history for a ticker.
    
    Query parameters:
        - limit: Maximum number of records to return (default: 50)
    """
    try:
        if not service.db.ticker_exists(symbol.upper()):
            return jsonify({"error": f"Ticker {symbol} not found", "status": "error"}), 404
        
        limit = request.args.get('limit', 50, type=int)
        history = service.db.get_update_history(symbol.upper(), limit=limit)
        summary = service.db.get_update_summary(symbol.upper())
        
        return jsonify({
            "status": "success",
            "symbol": symbol.upper(),
            "summary": summary,
            "count": len(history),
            "data": history
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/api/ticker/<symbol>/splits', methods=['GET'])
def api_get_ticker_splits(symbol: str):
    """Splits already taken care of for a ticker, and its full split check (if done)."""
    symbol = symbol.upper()
    if not service.db.ticker_exists(symbol):
        return jsonify({"error": f"Ticker {symbol} not found", "status": "error"}), 404
    return jsonify({
        "status": "success",
        "symbol": symbol,
        "check": service.db.get_split_check(symbol),
        "data": service.db.get_split_events(symbol),
    })


@app.route('/api/ticker/<symbol>/check-splits', methods=['POST'])
def api_check_ticker_splits(symbol: str):
    """
    Check a ticker's whole stored history for splits now and re-download it if needed.

    Query parameters:
        - force: "1" re-downloads the history even when no new split is found
    """
    symbol = symbol.upper()
    if not service.db.ticker_exists(symbol):
        return jsonify({"error": f"Ticker {symbol} not found", "status": "error"}), 404
    try:
        result = service.splits.check(symbol, force=request.args.get('force') == '1')
    except Exception as e:
        logger.error(f"Split check failed for {symbol}: {e}")
        return jsonify({"error": "Split check failed", "status": "error"}), 500
    if result['action'] == 'download_failed':
        return jsonify({"error": "Yahoo Finance history unavailable", "status": "error"}), 503
    return jsonify({"status": "success", "symbol": symbol, **result,
                    "splits": [{"date": d, "ratio": r} for d, r in result['splits']]})


def main():
    """Main entry point for the Flask application."""
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║           Stock Data Service v0.0.0.7                     ║
    ║   Flask Web Application for Yahoo Finance Data            ║
    ║   Persistent DB Connection with Auto-Reconnect            ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    # Get configuration
    host = service.config.get('web', {}).get('host', '0.0.0.0')
    port = service.config.get('web', {}).get('port', 5000)
    debug = service.config.get('web', {}).get('debug', False)
    
    logger.info(f"Starting Flask server on {host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
