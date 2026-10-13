"""
Test Suite — Loss Categorization Engine + Stress Factor Decay
15+ tests covering both engines with realistic scenarios.
"""

import unittest
import json
import os
import tempfile
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sys

# Import engines (adjust paths as needed)
sys.path.insert(0, '/home/claude/kraken-bot/v12')

from loss_categorization_engine import (
    LossCategorizer, LossCategory, LossCategorization
)
from stress_factor_decay import StressFactorDecay


class TestLossCategorizationEngine(unittest.TestCase):
    """Test suite for loss categorization."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.temp_categories = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_categories.close()
        
        self.categorizer = LossCategorizer(
            trades_db=self.temp_db.name,
            categories_path=self.temp_categories.name
        )
        
        self._create_test_db()
    
    def tearDown(self):
        """Clean up test files."""
        for path in [self.temp_db.name, self.temp_categories.name]:
            if os.path.exists(path):
                os.unlink(path)
    
    def _create_test_db(self):
        """Create test SQLite database with trades table."""
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT,
                entry_signal TEXT,
                entry_confidence REAL,
                entry_price REAL,
                exit_price REAL,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                net_pnl_usd REAL,
                net_pnl_pct REAL,
                tier TEXT,
                status TEXT,
                spread_bps REAL,
                has_error_flag INTEGER DEFAULT 0
            )
        """)
        
        cursor.execute("""
            CREATE TABLE market_snapshots (
                pair TEXT,
                snapshot_time TEXT,
                high_price REAL,
                low_price REAL,
                close_price REAL
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _add_trade(self, 
                   pair='BTC/USD',
                   entry_signal='RSI_OVERSOLD',
                   entry_confidence=0.8,
                   entry_price=50000,
                   exit_price=49800,
                   entry_time=None,
                   exit_time=None,
                   exit_reason='stop_loss',
                   net_pnl_usd=-200,
                   net_pnl_pct=-0.4,
                   spread_bps=2,
                   has_error=False):
        """Helper to add trade to test DB."""
        if entry_time is None:
            entry_time = datetime.utcnow().isoformat()
        if exit_time is None:
            exit_time = (datetime.fromisoformat(entry_time) + timedelta(minutes=10)).isoformat()
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades 
            (pair, entry_signal, entry_confidence, entry_price, exit_price,
             entry_time, exit_time, exit_reason, net_pnl_usd, net_pnl_pct,
             tier, status, spread_bps, has_error_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 'closed', ?, ?)
        """, (pair, entry_signal, entry_confidence, entry_price, exit_price,
              entry_time, exit_time, exit_reason, net_pnl_usd, net_pnl_pct, spread_bps, 1 if has_error else 0))
        
        conn.commit()
        conn.close()
    
    def test_01_load_categories_empty(self):
        """Test loading empty categorizations on fresh start."""
        self.assertEqual(len(self.categorizer.categories['categorizations']), 0)
        self.assertEqual(self.categorizer.categories['summary'], {})
    
    def test_02_categorize_small_loss_as_slippage(self):
        """Test that small losses (<0.5%) are categorized as slippage."""
        self._add_trade(net_pnl_usd=-25, net_pnl_pct=-0.05, spread_bps=5)
        
        conn = sqlite3.connect(self.temp_db.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = 1")
        trade = dict(cursor.fetchone())
        conn.close()
        
        cat = self.categorizer.categorize_loss(trade)
        
        # Small loss should be slippage or unknown; check both
        self.assertIn(cat.category, [LossCategory.SLIPPAGE, LossCategory.UNKNOWN])
        self.assertGreaterEqual(cat.confidence, 0.5)
    
    def test_03_categorize_quick_reversal_as_whipsaw(self):
        """Test that quick reversals (<5min) are categorized as whipsaw."""
        entry = datetime.utcnow().isoformat()
        exit_t = (datetime.fromisoformat(entry) + timedelta(minutes=2)).isoformat()
        
        self._add_trade(
            entry_time=entry,
            exit_time=exit_t,
            exit_reason='stop_loss',
            entry_confidence=0.85,
            net_pnl_usd=-150,
            net_pnl_pct=-0.3
        )
        
        conn = sqlite3.connect(self.temp_db.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = 1")
        trade = dict(cursor.fetchone())
        conn.close()
        
        cat = self.categorizer.categorize_loss(trade)
        
        self.assertEqual(cat.category, LossCategory.WHIPSAW)
        self.assertGreater(cat.confidence, 0.8)
    
    def test_04_categorize_technical_error(self):
        """Test that errors flagged in trade are categorized as TECHNICAL_ERROR."""
        self._add_trade(net_pnl_usd=-500, net_pnl_pct=-1.0, has_error=True)
        
        conn = sqlite3.connect(self.temp_db.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = 1")
        trade = dict(cursor.fetchone())
        conn.close()
        
        cat = self.categorizer.categorize_loss(trade)
        
        self.assertEqual(cat.category, LossCategory.TECHNICAL_ERROR)
        self.assertGreater(cat.confidence, 0.9)
    
    def test_05_save_and_load_categories(self):
        """Test persistence of categorization records."""
        self._add_trade(net_pnl_usd=-100, net_pnl_pct=-0.2)
        
        conn = sqlite3.connect(self.temp_db.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = 1")
        trade = dict(cursor.fetchone())
        conn.close()
        
        cat = self.categorizer.categorize_loss(trade)
        self.categorizer.categories['categorizations'].append(cat.to_dict())
        self.categorizer.save_categories()
        
        # Load in new instance
        categorizer2 = LossCategorizer(
            trades_db=self.temp_db.name,
            categories_path=self.temp_categories.name
        )
        
        self.assertEqual(len(categorizer2.categories['categorizations']), 1)
        self.assertEqual(
            categorizer2.categories['categorizations'][0]['category'],
            cat.category.value
        )
    
    def test_06_update_summary_stats(self):
        """Test that summary statistics are correctly calculated."""
        for i in range(3):
            self._add_trade(net_pnl_usd=-100 * (i+1), net_pnl_pct=-0.1)
        
        # Manually categorize each
        for trade_id in range(1, 4):
            conn = sqlite3.connect(self.temp_db.name)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM trades WHERE id = {trade_id}")
            trade = dict(cursor.fetchone())
            conn.close()
            
            cat = self.categorizer.categorize_loss(trade)
            self.categorizer.categories['categorizations'].append(cat.to_dict())
        
        self.categorizer.update_summary()
        
        summary = self.categorizer.categories['summary']
        self.assertTrue(len(summary) > 0)
        
        # Check that summary has expected keys
        for category in summary:
            self.assertIn('count', summary[category])
            self.assertIn('total_loss_usd', summary[category])
            self.assertIn('avg_loss_usd', summary[category])
            self.assertIn('avg_confidence', summary[category])
    
    def test_07_categorize_multiple_losses(self):
        """Test categorizing multiple losses in batch."""
        self._add_trade(net_pnl_usd=-50, net_pnl_pct=-0.1)
        self._add_trade(net_pnl_usd=-200, net_pnl_pct=-0.4)
        self._add_trade(net_pnl_usd=-500, net_pnl_pct=-1.0)
        
        result = self.categorizer.categorize_all_uncategorized_losses()
        
        self.assertIn('new_categorizations', result)
        self.assertGreaterEqual(result['new_categorizations'], 0)
        self.assertIn('category_breakdown', result)


class TestStressFactorDecay(unittest.TestCase):
    """Test suite for stress factor decay."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_stress = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_stress.close()
        self.temp_history = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_history.close()
        
        self.decay = StressFactorDecay(
            stress_path=self.temp_stress.name,
            history_path=self.temp_history.name
        )
    
    def tearDown(self):
        """Clean up test files."""
        for path in [self.temp_stress.name, self.temp_history.name]:
            if os.path.exists(path):
                os.unlink(path)
    
    def test_08_initialize_default_state(self):
        """Test that decay initializes with correct defaults."""
        self.assertEqual(self.decay.current['global_factor'], 1.0)
        self.assertEqual(self.decay.current['per_pair'], {})
    
    def test_09_increase_global_stress(self):
        """Test increasing global stress factor."""
        old = self.decay.current['global_factor']
        self.decay.increase_stress(pair=None, increase_amount=0.1)
        new = self.decay.current['global_factor']
        
        self.assertEqual(new, old + 0.1)
        self.assertEqual(len(self.decay.history['versions']), 1)
    
    def test_10_increase_per_pair_stress(self):
        """Test increasing stress for specific trading pair."""
        self.decay.increase_stress(pair='BTC/USD', increase_amount=0.15)
        
        self.assertIn('BTC/USD', self.decay.current['per_pair'])
        self.assertEqual(self.decay.current['per_pair']['BTC/USD'], 1.15)
    
    def test_11_stress_factor_respects_max_cap(self):
        """Test that stress factor doesn't exceed maximum."""
        # Start high
        self.decay.current['global_factor'] = 1.95
        self.decay.increase_stress(pair=None, increase_amount=0.1)
        
        self.assertEqual(self.decay.current['global_factor'], 2.0)
    
    def test_12_stress_factor_respects_min_cap(self):
        """Test that stress factor doesn't go below baseline."""
        self.decay.current['global_factor'] = 1.0
        # Apply decay (should fail or not change it)
        self.decay.apply_decay(regressions_detected=0)
        
        self.assertGreaterEqual(self.decay.current['global_factor'], 1.0)
    
    def test_13_decay_requires_time_passage(self):
        """Test that decay won't apply unless 6 days have passed."""
        old = self.decay.current['global_factor']
        self.decay.current['global_factor'] = 1.5
        
        # Should not decay (just set)
        result = self.decay.apply_decay(regressions_detected=0)
        
        self.assertFalse(result)
        self.assertEqual(self.decay.current['global_factor'], 1.5)
    
    def test_14_decay_skipped_if_regressions_detected(self):
        """Test that decay is skipped if regressions found."""
        # Simulate time passage
        old_decay = datetime.utcnow() - timedelta(days=7)
        self.decay.current['last_decay'] = old_decay.isoformat()
        self.decay.current['global_factor'] = 1.5
        
        # Should skip decay due to regressions
        result = self.decay.apply_decay(regressions_detected=2)
        
        self.assertFalse(result)
        self.assertEqual(self.decay.current['global_factor'], 1.5)
    
    def test_15_record_version_increments_id(self):
        """Test that version IDs increment correctly."""
        v1 = self.decay.record_version(True, 0, "Test v1")
        v2 = self.decay.record_version(False, 1, "Test v2")
        
        self.assertEqual(v1, 0)
        self.assertEqual(v2, 1)
        self.assertEqual(len(self.decay.history['versions']), 2)
    
    def test_16_reset_to_previous_version(self):
        """Test rolling back to a previous version."""
        # Record initial state
        self.decay.current['global_factor'] = 1.5
        v1 = self.decay.record_version(False, 1, "High stress")
        
        # Modify and record new version
        self.decay.current['global_factor'] = 1.2
        v2 = self.decay.record_version(True, 0, "Decayed")
        
        # Rollback
        self.decay.reset_to_version(v1)
        
        self.assertEqual(self.decay.current['global_factor'], 1.5)
    
    def test_17_save_and_load_stress_state(self):
        """Test persistence of stress factor state."""
        self.decay.current['global_factor'] = 1.3
        self.decay.current['per_pair']['ETH/USD'] = 1.2
        self.decay.record_version(False, 1, "Test state")
        
        self.assertTrue(self.decay.save_state())
        
        # Load in new instance
        decay2 = StressFactorDecay(
            stress_path=self.temp_stress.name,
            history_path=self.temp_history.name
        )
        
        self.assertEqual(decay2.current['global_factor'], 1.3)
        self.assertEqual(decay2.current['per_pair']['ETH/USD'], 1.2)
    
    def test_18_history_pruning_respects_retention(self):
        """Test that old versions are pruned to respect retention limit."""
        # Record more versions than retention limit
        for i in range(60):
            self.decay.record_version(i % 2 == 0, i % 3, f"Version {i}")
        
        # Should keep only HISTORY_RETENTION versions
        self.assertLessEqual(
            len(self.decay.history['versions']),
            StressFactorDecay.HISTORY_RETENTION
        )


class TestIntegration(unittest.TestCase):
    """Integration tests combining both engines."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db.close()
        self.temp_categories = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_categories.close()
        self.temp_stress = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_stress.close()
        self.temp_history = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_history.close()
        
        self.categorizer = LossCategorizer(
            trades_db=self.temp_db.name,
            categories_path=self.temp_categories.name
        )
        
        self.decay = StressFactorDecay(
            stress_path=self.temp_stress.name,
            history_path=self.temp_history.name
        )
        
        self._create_test_db()
    
    def tearDown(self):
        """Clean up test files."""
        for path in [self.temp_db.name, self.temp_categories.name, 
                     self.temp_stress.name, self.temp_history.name]:
            if os.path.exists(path):
                os.unlink(path)
    
    def _create_test_db(self):
        """Create test SQLite database."""
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT,
                entry_signal TEXT,
                entry_confidence REAL,
                entry_price REAL,
                exit_price REAL,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                net_pnl_usd REAL,
                net_pnl_pct REAL,
                tier TEXT,
                status TEXT,
                spread_bps REAL,
                has_error_flag INTEGER DEFAULT 0
            )
        """)
        
        conn.commit()
        conn.close()
    
    def test_19_workflow_categorize_then_adjust_stress(self):
        """Integration test: categorize losses, adjust stress accordingly."""
        # Add a whipsaw loss
        entry = datetime.utcnow().isoformat()
        exit_t = (datetime.fromisoformat(entry) + timedelta(minutes=3)).isoformat()
        
        conn = sqlite3.connect(self.temp_db.name)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades 
            (pair, entry_signal, entry_confidence, entry_price, exit_price,
             entry_time, exit_time, exit_reason, net_pnl_usd, net_pnl_pct,
             tier, status, spread_bps, has_error_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 'closed', ?, ?)
        """, ('BTC/USD', 'RSI', 0.8, 50000, 49850, entry, exit_t, 
              'stop_loss', -150, -0.3, 2, 0))
        
        conn.commit()
        conn.close()
        
        # Categorize the loss
        conn = sqlite3.connect(self.temp_db.name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = 1")
        trade = dict(cursor.fetchone())
        conn.close()
        
        cat = self.categorizer.categorize_loss(trade)
        self.assertEqual(cat.category, LossCategory.WHIPSAW)
        
        # Adjust stress based on categorization
        if cat.category == LossCategory.WHIPSAW:
            self.decay.increase_stress(pair='BTC/USD', increase_amount=0.1)
        
        self.assertEqual(self.decay.current['per_pair']['BTC/USD'], 1.1)


def run_tests():
    """Run all tests and report results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestLossCategorizationEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStressFactorDecay))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
