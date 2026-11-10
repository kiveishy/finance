#!/usr/bin/env python3
"""
NYSE and NYSE Arca Ticker Synchronization Script
Downloads NYSE and NYSE Arca tickers and adds new ones to the database with 10 years of historical data.
Uses a persistent database connection with automatic reconnection.

This script:
1. Downloads the list of all NYSE and NYSE Arca tickers
2. Compares with existing tickers in the database
3. For new tickers, downloads 10 years of historical data
4. Inserts ticker info and historical data into the database
"""

import json
import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set, Tuple

from database import DatabaseManager
from yahoo_finance import YahooFinanceClient

# Ensure UTF-8 output on all platforms (important for Windows cp1252 console)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Log file next to this script (cross-platform absolute path)
_LOG_FILE = str(Path(__file__).parent / 'nyse_sync.log')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class NYSESyncService:
    """Service for synchronizing NYSE and NYSE Arca tickers with the database."""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Initialize the NYSE Sync Service.
        
        Args:
            config_path: Path to the configuration file.
        """
        self.config = self._load_config(config_path)
        self.base_path = Path(os.path.dirname(os.path.abspath(__file__)))
        
        # Initialize database manager
        db_path = self._resolve_path(self.config['database']['file_path'])
        self.db = DatabaseManager(db_path)
        
        # Initialize Yahoo Finance client with 10-year period and daily interval
        self.yahoo_client = YahooFinanceClient(
            interval="1d",
            period="10y"
        )
        
        # NYSE tickers file path
        self.nyse_tickers_file = self._resolve_path(
            self.config.get('files', {}).get('nyse_tickers', './data/nyse_tickers.json')
        )
        
        # NYSE Arca tickers file path
        self.nysearca_tickers_file = self._resolve_path(
            self.config.get('files', {}).get('nysearca_tickers', './data/nysearca_tickers.json')
        )
        
        # Sync statistics
        self.stats = {
            'total_nyse_tickers': 0,
            'total_nysearca_tickers': 0,
            'existing_tickers': 0,
            'new_tickers': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'total_records_inserted': 0
        }
        
        # Establish persistent database connection
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
    
    def _save_nyse_tickers(self, tickers: List[str]):
        """
        Save NYSE tickers to JSON file.
        
        Args:
            tickers: List of NYSE ticker symbols to save.
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.nyse_tickers_file), exist_ok=True)
            
            with open(self.nyse_tickers_file, 'w') as f:
                json.dump({
                    'exchange': 'NYSE',
                    'tickers': tickers,
                    'last_updated': datetime.now().isoformat(),
                    'count': len(tickers)
                }, f, indent=2)
            logger.info(f"Saved {len(tickers)} NYSE tickers to {self.nyse_tickers_file}")
        except IOError as e:
            logger.error(f"Failed to save NYSE tickers: {e}")
    
    def _save_nysearca_tickers(self, tickers: List[str]):
        """
        Save NYSE Arca tickers to JSON file.
        
        Args:
            tickers: List of NYSE Arca ticker symbols to save.
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.nysearca_tickers_file), exist_ok=True)
            
            with open(self.nysearca_tickers_file, 'w') as f:
                json.dump({
                    'exchange': 'NYSE Arca',
                    'tickers': tickers,
                    'last_updated': datetime.now().isoformat(),
                    'count': len(tickers)
                }, f, indent=2)
            logger.info(f"Saved {len(tickers)} NYSE Arca tickers to {self.nysearca_tickers_file}")
        except IOError as e:
            logger.error(f"Failed to save NYSE Arca tickers: {e}")
    
    def _load_saved_nyse_tickers(self) -> Set[str]:
        """
        Load previously saved NYSE tickers from JSON file.
        
        Returns:
            Set of NYSE ticker symbols.
        """
        try:
            if os.path.exists(self.nyse_tickers_file):
                with open(self.nyse_tickers_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('tickers', []))
            return set()
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load saved NYSE tickers: {e}")
            return set()
    
    def _load_saved_nysearca_tickers(self) -> Set[str]:
        """
        Load previously saved NYSE Arca tickers from JSON file.
        
        Returns:
            Set of NYSE Arca ticker symbols.
        """
        try:
            if os.path.exists(self.nysearca_tickers_file):
                with open(self.nysearca_tickers_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('tickers', []))
            return set()
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load saved NYSE Arca tickers: {e}")
            return set()
    
    def get_existing_tickers_from_db(self) -> Set[str]:
        """
        Get all existing tickers from the database.
        
        Returns:
            Set of existing ticker symbols in the database.
        """
        try:
            tickers = self.db.get_all_tickers()
            return set(tickers)
        except Exception as e:
            logger.error(f"Failed to get existing tickers from database: {e}")
            return set()
    
    def find_new_nyse_tickers(self) -> Tuple[List[str], Set[str]]:
        """
        Download NYSE tickers and find ones not in the database.
        
        Returns:
            Tuple of (list of new tickers, set of all NYSE tickers)
        """
        logger.info("Downloading NYSE ticker list...")
        
        # Get NYSE tickers from Yahoo Finance client
        nyse_tickers = self.yahoo_client.get_nyse_tickers()
        nyse_tickers_set = set(nyse_tickers)
        self.stats['total_nyse_tickers'] = len(nyse_tickers_set)
        
        logger.info(f"Retrieved {len(nyse_tickers_set)} NYSE tickers")
        
        # Get existing tickers from database
        existing_tickers = self.get_existing_tickers_from_db()
        self.stats['existing_tickers'] = len(existing_tickers)
        
        logger.info(f"Found {len(existing_tickers)} existing tickers in database")
        
        # Find new tickers (NYSE tickers not in database)
        new_tickers = nyse_tickers_set - existing_tickers
        
        logger.info(f"Found {len(new_tickers)} new NYSE tickers to add")
        
        return list(new_tickers), nyse_tickers_set
    
    def find_new_nysearca_tickers(self) -> Tuple[List[str], Set[str]]:
        """
        Download NYSE Arca tickers and find ones not in the database.
        
        Returns:
            Tuple of (list of new tickers, set of all NYSE Arca tickers)
        """
        logger.info("Downloading NYSE Arca ticker list...")
        
        # Get NYSE Arca tickers from Yahoo Finance client
        nysearca_tickers = self.yahoo_client.get_nysearca_tickers()
        nysearca_tickers_set = set(nysearca_tickers)
        self.stats['total_nysearca_tickers'] = len(nysearca_tickers_set)
        
        logger.info(f"Retrieved {len(nysearca_tickers_set)} NYSE Arca tickers")
        
        # Get existing tickers from database
        existing_tickers = self.get_existing_tickers_from_db()
        
        logger.info(f"Found {len(existing_tickers)} existing tickers in database")
        
        # Find new tickers (NYSE Arca tickers not in database)
        new_tickers = nysearca_tickers_set - existing_tickers
        
        logger.info(f"Found {len(new_tickers)} new NYSE Arca tickers to add")
        
        return list(new_tickers), nysearca_tickers_set
    
    def download_and_insert_ticker(self, symbol: str, exchange: str = 'NYSE') -> bool:
        """
        Download ticker info and historical data, then insert into database.
        
        Args:
            symbol: The ticker symbol to process.
            exchange: The exchange name (NYSE, NYSE Arca, etc.)
            
        Returns:
            True if successful, False otherwise.
        """
        try:
            logger.info(f"Processing ticker: {symbol}")
            
            # Get ticker info from Yahoo Finance
            ticker_info = self.yahoo_client.get_ticker_info(symbol)
            
            if ticker_info:
                # Mark this with the correct exchange
                ticker_info['exchange'] = exchange
                success = self.db.insert_ticker(ticker_info)
            else:
                # Insert minimal ticker info
                logger.warning(f"No detailed info available for {symbol}, inserting minimal data")
                success = self.db.insert_ticker({
                    'symbol': symbol,
                    'exchange': exchange
                })
            
            if not success:
                logger.error(f"Failed to insert ticker info for {symbol}")
                return False
            
            # Get 10 years of historical data
            logger.info(f"Downloading 10 years of historical data for {symbol}")
            historical_data = self.yahoo_client.get_historical_data(symbol, period="10y")
            
            if historical_data:
                records_inserted = self.db.insert_historical_data(symbol, historical_data)
                self.stats['total_records_inserted'] += records_inserted
                logger.info(f"Inserted {records_inserted} historical records for {symbol}")
            else:
                logger.warning(f"No historical data available for {symbol}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing ticker {symbol}: {e}")
            return False
    
    def _process_tickers(self, tickers: List[str], exchange: str, start_time: datetime, 
                          delay: float = 0.5) -> None:
        """
        Process a list of tickers, downloading their data and inserting into database.
        
        Args:
            tickers: List of ticker symbols to process
            exchange: The exchange name for these tickers
            start_time: The start time of the sync operation
            delay: Delay between API calls
        """
        total = len(tickers)
        logger.info(f"Processing {total} new {exchange} tickers...")
        
        for i, symbol in enumerate(tickers, 1):
            try:
                logger.info(f"[{i}/{total}] Processing {symbol} ({exchange})")
                
                success = self.download_and_insert_ticker(symbol, exchange)
                
                if success:
                    self.stats['successful_downloads'] += 1
                else:
                    self.stats['failed_downloads'] += 1
                
                # Progress update every 10 tickers
                if i % 10 == 0:
                    elapsed = datetime.now() - start_time
                    rate = i / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
                    remaining = (total - i) / rate if rate > 0 else 0
                    logger.info(f"Progress ({exchange}): {i}/{total} ({100*i/total:.1f}%) - "
                                f"ETA: {remaining/60:.1f} minutes")
                
                # Delay to avoid rate limiting
                if delay > 0 and i < total:
                    time.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                self.stats['failed_downloads'] += 1
                continue

    def run_sync(self, limit: int = None, delay: float = 0.5, dry_run: bool = False,
                 sync_nyse: bool = True, sync_nysearca: bool = True):
        """
        Run the NYSE and NYSE Arca synchronization process.
        
        Args:
            limit: Maximum number of new tickers to process per exchange (None for all)
            delay: Delay in seconds between API calls to avoid rate limiting
            dry_run: If True, only show what would be done without making changes
            sync_nyse: If True, sync NYSE tickers
            sync_nysearca: If True, sync NYSE Arca tickers
        """
        print("""
        ╔═══════════════════════════════════════════════════════════╗
        ║       NYSE & NYSE Arca Ticker Synchronization Script      ║
        ║   Download and sync tickers to local database             ║
        ║   Persistent DB Connection with Auto-Reconnect            ║
        ╚═══════════════════════════════════════════════════════════╝
        """)
        
        start_time = datetime.now()
        logger.info(f"Starting sync at {start_time}")
        
        try:
            all_new_tickers = []
            all_nyse_tickers = set()
            all_nysearca_tickers = set()
            
            # Find new NYSE tickers
            if sync_nyse:
                new_nyse_tickers, all_nyse_tickers = self.find_new_nyse_tickers()
                logger.info(f"Found {len(new_nyse_tickers)} new NYSE tickers")
            else:
                new_nyse_tickers = []
                logger.info("Skipping NYSE tickers (disabled)")
            
            # Find new NYSE Arca tickers
            if sync_nysearca:
                new_nysearca_tickers, all_nysearca_tickers = self.find_new_nysearca_tickers()
                logger.info(f"Found {len(new_nysearca_tickers)} new NYSE Arca tickers")
            else:
                new_nysearca_tickers = []
                logger.info("Skipping NYSE Arca tickers (disabled)")
            
            # Update stats
            total_new = len(new_nyse_tickers) + len(new_nysearca_tickers)
            self.stats['new_tickers'] = total_new
            self.stats['existing_tickers'] = len(self.get_existing_tickers_from_db())
            
            if total_new == 0:
                logger.info("No new tickers to add. Database is up to date.")
                print("\n✅ No new tickers to add. Database is up to date.")
                return
            
            # Apply limit if specified
            if limit:
                if len(new_nyse_tickers) > limit:
                    logger.info(f"Limiting NYSE tickers to {limit} (out of {len(new_nyse_tickers)})")
                    new_nyse_tickers = new_nyse_tickers[:limit]
                if len(new_nysearca_tickers) > limit:
                    logger.info(f"Limiting NYSE Arca tickers to {limit} (out of {len(new_nysearca_tickers)})")
                    new_nysearca_tickers = new_nysearca_tickers[:limit]
            
            if dry_run:
                print(f"\n🔍 DRY RUN MODE")
                if new_nyse_tickers:
                    print(f"\n  Would process {len(new_nyse_tickers)} new NYSE tickers:")
                    for ticker in new_nyse_tickers[:10]:
                        print(f"    - {ticker}")
                    if len(new_nyse_tickers) > 10:
                        print(f"    ... and {len(new_nyse_tickers) - 10} more")
                
                if new_nysearca_tickers:
                    print(f"\n  Would process {len(new_nysearca_tickers)} new NYSE Arca tickers:")
                    for ticker in new_nysearca_tickers[:10]:
                        print(f"    - {ticker}")
                    if len(new_nysearca_tickers) > 10:
                        print(f"    ... and {len(new_nysearca_tickers) - 10} more")
                return
            
            # Process NYSE tickers
            if new_nyse_tickers:
                print(f"\n📊 Processing {len(new_nyse_tickers)} NYSE tickers...")
                self._process_tickers(new_nyse_tickers, 'NYSE', start_time, delay)
                self._save_nyse_tickers(list(all_nyse_tickers))
            
            # Process NYSE Arca tickers
            if new_nysearca_tickers:
                print(f"\n📊 Processing {len(new_nysearca_tickers)} NYSE Arca tickers...")
                self._process_tickers(new_nysearca_tickers, 'NYSE Arca', start_time, delay)
                self._save_nysearca_tickers(list(all_nysearca_tickers))
            
            # Print summary
            end_time = datetime.now()
            duration = end_time - start_time
            
            print(f"""
        ╔═══════════════════════════════════════════════════════════╗
        ║                  Synchronization Complete                 ║
        ╠═══════════════════════════════════════════════════════════╣
        ║  Total NYSE tickers found:       {self.stats['total_nyse_tickers']:>6}                   ║
        ║  Total NYSE Arca tickers found:  {self.stats['total_nysearca_tickers']:>6}                   ║
        ║  Existing tickers in DB:         {self.stats['existing_tickers']:>6}                   ║
        ║  New tickers to process:         {self.stats['new_tickers']:>6}                   ║
        ║  Successfully downloaded:        {self.stats['successful_downloads']:>6}                   ║
        ║  Failed downloads:               {self.stats['failed_downloads']:>6}                   ║
        ║  Total records inserted:         {self.stats['total_records_inserted']:>6}                   ║
        ║  Duration:                       {str(duration).split('.')[0]:>10}               ║
        ╚═══════════════════════════════════════════════════════════╝
            """)
            
            logger.info(f"Sync completed in {duration}")
            logger.info(f"Stats: {self.stats}")
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            raise


def main():
    """Main entry point for the NYSE and NYSE Arca sync script."""
    parser = argparse.ArgumentParser(
        description='Synchronize NYSE and NYSE Arca tickers with the local database'
    )
    parser.add_argument(
        '--limit', '-l',
        type=int,
        default=None,
        help='Maximum number of new tickers to process per exchange (default: all)'
    )
    parser.add_argument(
        '--delay', '-d',
        type=float,
        default=0.5,
        help='Delay in seconds between API calls (default: 0.5)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--nyse-only',
        action='store_true',
        help='Only sync NYSE tickers (skip NYSE Arca)'
    )
    parser.add_argument(
        '--nysearca-only',
        action='store_true',
        help='Only sync NYSE Arca tickers (skip NYSE)'
    )
    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config.json',
        help='Path to configuration file (default: config.json)'
    )
    
    args = parser.parse_args()
    
    # Determine which exchanges to sync
    sync_nyse = True
    sync_nysearca = True
    
    if args.nyse_only and args.nysearca_only:
        parser.error("Cannot use both --nyse-only and --nysearca-only")
    elif args.nyse_only:
        sync_nysearca = False
    elif args.nysearca_only:
        sync_nyse = False
    
    try:
        service = NYSESyncService(config_path=args.config)
        service.run_sync(
            limit=args.limit,
            delay=args.delay,
            dry_run=args.dry_run,
            sync_nyse=sync_nyse,
            sync_nysearca=sync_nysearca
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Sync interrupted by user")
        logger.info("Sync interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        logger.error(f"Sync failed: {e}")
        sys.exit(1)
    finally:
        try:
            service.db.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
