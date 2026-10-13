"""
Test Harness for Portfolio Safety Engine v1 - FINAL
"""

import pytest
import json
import tempfile
import sqlite3
import time
import os
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)

import sys
sys.path.insert(0, '/home/claude/kraken-bot/v12')

from portfolio_safety_engine import PortfolioSafetyEngine
from atomic_file_utils import AtomicFileWriter


class TestPortfolioSafetyEngine:
    """Portfolio Safety Engine Unit Tests"""
    
    @pytest.fixture
    def engine_with_db(self):
        """Create engine with test database."""
        tmpdir = tempfile.mkdtemp()
        db_path = f"{tmpdir}/trades.db"
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                tier TEXT,
                pair TEXT,
                pnl_percent REAL
            )
        """)
        
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD', 'DOTUSD', 'LINKUSD']
        for i, pair in enumerate(pairs):
            for j in range(50):
                pnl = 1.5 + (j % 5) * 0.25 + (i * 0.15)
                cursor.execute(
                    "INSERT INTO trades (timestamp, tier, pair, pnl_percent) VALUES (?, ?, ?, ?)",
                    (int(time.time()) - j * 100, 'paper', pair, pnl)
                )
        
        conn.commit()
        conn.close()
        
        engine = PortfolioSafetyEngine(db_path, "test-instance", "paper")
        yield engine, tmpdir
        
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    
    def test_initialization(self, engine_with_db):
        engine, _ = engine_with_db
        assert engine.MAX_PAIRS == 5
        assert engine.MAX_CORRELATION == 0.7
        assert engine.MIN_STABILITY_TRADES == 30
    
    def test_get_recent_returns(self, engine_with_db):
        engine, _ = engine_with_db
        returns = engine.get_recent_returns('XBTUSD', limit=10)
        assert returns is not None
        assert len(returns) == 10
    
    def test_calculate_volatility(self, engine_with_db):
        engine, _ = engine_with_db
        vol = engine.calculate_volatility('XBTUSD', window=20)
        assert vol is not None
        assert vol > 0
    
    def test_calculate_correlation(self, engine_with_db):
        engine, _ = engine_with_db
        corr = engine.calculate_correlation('XBTUSD', 'ETHUSD')
        assert corr is not None
        assert -1 <= corr <= 1
    
    def test_build_correlation_matrix(self, engine_with_db):
        engine, _ = engine_with_db
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD']
        matrix = engine.build_correlation_matrix(pairs)
        assert len(matrix) == 3
        assert matrix['XBTUSD']['XBTUSD'] == 1.0
    
    def test_find_correlated_pairs(self, engine_with_db):
        engine, _ = engine_with_db
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD']
        matrix = engine.build_correlation_matrix(pairs)
        violations = engine.find_correlated_pairs(matrix, threshold=0.7)
        assert isinstance(violations, list)
    
    def test_calculate_position_size(self, engine_with_db):
        engine, _ = engine_with_db
        size = engine.calculate_position_size('XBTUSD', base_size=1.0)
        assert 0.1 <= size <= 1.0
    
    def test_get_pair_trade_count(self, engine_with_db):
        engine, _ = engine_with_db
        count = engine.get_pair_trade_count('XBTUSD')
        assert count == 50
    
    def test_is_pair_stable(self, engine_with_db):
        engine, _ = engine_with_db
        assert engine.is_pair_stable('XBTUSD') is True
        assert engine.is_pair_stable('XBTUSD', min_trades=60) is False
    
    def test_validate_pair_for_addition(self, engine_with_db):
        engine, _ = engine_with_db
        result = engine.validate_pair_for_addition('ETHUSD', [])
        assert result['can_add'] is True
    
    def test_validate_pair_portfolio_full(self, engine_with_db):
        engine, _ = engine_with_db
        active = ['XBTUSD', 'ETHUSD', 'ADAUSD', 'DOTUSD', 'LINKUSD']
        result = engine.validate_pair_for_addition('XBTUSD', active)
        assert result['can_add'] is False
    
    def test_validate_portfolio_valid(self, engine_with_db):
        engine, _ = engine_with_db
        result = engine.validate_portfolio(['XBTUSD', 'ETHUSD'])
        assert 'is_valid' in result
        assert 'metrics' in result
    
    def test_validate_portfolio_oversized(self, engine_with_db):
        engine, _ = engine_with_db
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD', 'DOTUSD', 'LINKUSD', 'BNBUSD']
        result = engine.validate_portfolio(pairs)
        assert result['is_valid'] is False
    
    def test_run_validation(self, engine_with_db):
        engine, _ = engine_with_db
        result = engine.run_validation(['XBTUSD', 'ETHUSD'])
        assert 'is_valid' in result
        assert os.path.exists(engine.state_file)


class TestPerformance:
    """Performance tests"""
    
    def test_correlation_matrix_performance(self):
        tmpdir = tempfile.mkdtemp()
        db_path = f"{tmpdir}/trades_perf.db"
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER,
                tier TEXT,
                pair TEXT,
                pnl_percent REAL
            )
        """)
        
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD', 'DOTUSD']
        for i, pair in enumerate(pairs):
            for j in range(500):
                pnl = 1.5 + (j % 10) * 0.2
                cursor.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?, ?)",
                    (j + i*500, int(time.time()) - j*50, 'paper', pair, pnl)
                )
        
        conn.commit()
        conn.close()
        
        engine = PortfolioSafetyEngine(db_path, "perf", "paper")
        
        start = time.time()
        matrix = engine.build_correlation_matrix(pairs)
        elapsed = time.time() - start
        
        assert elapsed < 5.0
        assert len(matrix) == 4
        
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)



if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
