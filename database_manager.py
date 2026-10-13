"""
Database Manager v2
- Create proper indexes for performance
- Initialize schema with migration support
- Connection pooling + error handling
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manage trades database with indexes and migrations.
    """
    
    SCHEMA_VERSION = 2
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()
    
    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    @contextmanager
    def connection(self):
        """Context manager for safe connections."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def init_schema(self):
        """Initialize database schema with migrations."""
        with self.connection() as conn:
            cursor = conn.cursor()
            
            # Version table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("SELECT MAX(version) as version FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row[0] else 0
            
            # Migration: v0 → v1 (base schema)
            if current_version < 1:
                logger.info("Applying migration: v1 (base schema)")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER,
                        tier TEXT,
                        instance_id TEXT,
                        pair TEXT,
                        entry_price REAL,
                        exit_price REAL,
                        quantity REAL,
                        pnl REAL,
                        pnl_percent REAL,
                        signal_type TEXT,
                        signal_confidence REAL,
                        entry_time INTEGER,
                        exit_time INTEGER,
                        hold_minutes INTEGER,
                        exit_reason TEXT
                    )
                """)
                cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
            
            # Migration: v1 → v2 (add instance tracking + indexes)
            if current_version < 2:
                logger.info("Applying migration: v2 (instance tracking + indexes)")
                
                # Check if instance_id column exists
                cursor.execute("PRAGMA table_info(trades)")
                columns = {row[1] for row in cursor.fetchall()}
                
                if "instance_id" not in columns:
                    cursor.execute("ALTER TABLE trades ADD COLUMN instance_id TEXT")
                
                cursor.execute("INSERT INTO schema_version (version) VALUES (2)")
            
            # Create indexes for performance
            self._create_indexes(cursor)
            conn.commit()
    
    def _create_indexes(self, cursor: sqlite3.Cursor):
        """Create performance indexes."""
        indexes = [
            # Tier queries
            ("idx_tier", "CREATE INDEX IF NOT EXISTS idx_tier ON trades(tier)"),
            
            # Instance queries
            ("idx_instance", "CREATE INDEX IF NOT EXISTS idx_instance_id ON trades(instance_id)"),
            
            # Pair-specific analysis
            ("idx_pair", "CREATE INDEX IF NOT EXISTS idx_pair ON trades(pair)"),
            
            # Time-based queries
            ("idx_entry_time", "CREATE INDEX IF NOT EXISTS idx_entry_time ON trades(entry_time)"),
            ("idx_exit_time", "CREATE INDEX IF NOT EXISTS idx_exit_time ON trades(exit_time)"),
            
            # Signal type analysis
            ("idx_signal", "CREATE INDEX IF NOT EXISTS idx_signal_type ON trades(signal_type)"),
            
            # Composite for common queries
            ("idx_tier_pair", "CREATE INDEX IF NOT EXISTS idx_tier_pair ON trades(tier, pair)"),
            ("idx_tier_instance", "CREATE INDEX IF NOT EXISTS idx_tier_instance ON trades(tier, instance_id)"),
            ("idx_tier_pair_time", "CREATE INDEX IF NOT EXISTS idx_tier_pair_time ON trades(tier, pair, entry_time)"),
        ]
        
        for index_name, create_stmt in indexes:
            try:
                cursor.execute(create_stmt)
            except sqlite3.OperationalError as e:
                if "already exists" in str(e):
                    logger.debug(f"Index {index_name} already exists")
                else:
                    logger.error(f"Failed to create index {index_name}: {e}")
    
    def record_trade(self, trade_data: dict) -> int:
        """
        Record a trade.
        
        Args:
            trade_data: Dict with keys: tier, instance_id, pair, entry_price, 
                       exit_price, quantity, pnl, signal_type, signal_confidence, 
                       entry_time, exit_time, exit_reason
        
        Returns:
            Trade ID
        """
        try:
            with self.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trades (
                        timestamp, tier, instance_id, pair, entry_price, exit_price,
                        quantity, pnl, pnl_percent, signal_type, signal_confidence,
                        entry_time, exit_time, hold_minutes, exit_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    int(__import__('time').time()),
                    trade_data.get('tier'),
                    trade_data.get('instance_id'),
                    trade_data.get('pair'),
                    trade_data.get('entry_price'),
                    trade_data.get('exit_price'),
                    trade_data.get('quantity'),
                    trade_data.get('pnl'),
                    trade_data.get('pnl_percent'),
                    trade_data.get('signal_type'),
                    trade_data.get('signal_confidence'),
                    trade_data.get('entry_time'),
                    trade_data.get('exit_time'),
                    trade_data.get('hold_minutes'),
                    trade_data.get('exit_reason')
                ))
                return cursor.lastrowid
        
        except Exception as e:
            logger.error(f"Failed to record trade: {e}")
            raise
    
    def get_pair_stats(self, pair: str, tier: Optional[str] = None) -> dict:
        """Get stats for a pair."""
        try:
            with self.connection() as conn:
                cursor = conn.cursor()
                
                where = "WHERE pair = ?"
                params = [pair]
                
                if tier:
                    where += " AND tier = ?"
                    params.append(tier)
                
                cursor.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                        AVG(pnl) as avg_pnl,
                        SUM(pnl) as total_pnl
                    FROM trades {where}
                """, params)
                
                row = cursor.fetchone()
                if not row or row[0] == 0:
                    return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0}
                
                total, wins, losses, avg_pnl, total_pnl = row
                wins = wins or 0
                
                return {
                    "pair": pair,
                    "total": total,
                    "wins": wins,
                    "losses": losses or 0,
                    "win_rate": wins / total if total > 0 else 0.0,
                    "avg_pnl": avg_pnl or 0,
                    "total_pnl": total_pnl or 0
                }
        
        except Exception as e:
            logger.error(f"Failed to get pair stats: {e}")
            return {"total": 0}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    db = DatabaseManager("/tmp/test_trades.db")
    
    # Record test trade
    trade_id = db.record_trade({
        "tier": "shadow",
        "instance_id": "shadow-1",
        "pair": "BTC/USD",
        "entry_price": 40000,
        "exit_price": 41000,
        "quantity": 0.1,
        "pnl": 100,
        "pnl_percent": 0.025,
        "signal_type": "RSI_OVERSOLD",
        "signal_confidence": 0.65,
        "entry_time": 1000,
        "exit_time": 2000,
        "hold_minutes": 16,
        "exit_reason": "tp_hit"
    })
    
    print(f"✓ Trade {trade_id} recorded")
    
    stats = db.get_pair_stats("BTC/USD", "shadow")
    print(f"Stats: {stats}")
