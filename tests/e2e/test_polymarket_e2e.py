"""End-to-end tests for Polymarket Agent."""

import pytest
import json
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.agents.polymarket_agent.run import PolymarketAgent
from src.agents.polymarket_agent.config import AGENT_NAME, OUT_DIR, LOGS_DIR
from src.servers.polymarket.expand_keywords import expand_query_keywords, extract_keywords_fallback
from src.servers.polymarket.search_markets import filter_markets_by_keywords
from src.servers.polymarket.get_history import get_polymarket_history


class TestPolymarketAgent:
    """Test Polymarket agent initialization and basic functionality."""
    
    def test_agent_initialization(self, clean_workspace):
        """Test agent initializes correctly."""
        agent = PolymarketAgent()
        
        assert agent.workspace.exists()
        assert agent.manifest is not None
        assert agent.mcp_client is not None
    
    def test_session_id_generation(self, clean_workspace):
        """Test session ID generation."""
        agent = PolymarketAgent()
        
        session_id = agent.generate_session_id()
        
        # Check format: {timestamp}_{hash}
        assert '_' in session_id
        parts = session_id.split('_')
        assert len(parts) == 2
        
        # Timestamp should be 14 digits (YYYYMMDDHHmmss)
        assert len(parts[0]) == 14
        assert parts[0].isdigit()
        
        # Hash should be 6 hex chars (3 bytes)
        assert len(parts[1]) == 6
        assert all(c in '0123456789abcdef' for c in parts[1])
    
    def test_session_ids_unique(self, clean_workspace):
        """Test that session IDs are unique."""
        agent = PolymarketAgent()
        
        id1 = agent.generate_session_id()
        id2 = agent.generate_session_id()
        
        assert id1 != id2


class TestKeywordExpansion:
    """Test LLM keyword expansion functionality."""
    
    def test_fallback_keyword_extraction(self):
        """Test fallback keyword extraction (no LLM)."""
        query = "Will Bitcoin reach $100k by end of year?"
        
        keywords = extract_keywords_fallback(query)
        
        # Should extract meaningful keywords
        assert len(keywords) > 0
        assert 'bitcoin' in keywords
        
        # Should not include stop words
        assert 'will' not in keywords
        assert 'the' not in keywords
    
    def test_keyword_deduplication(self):
        """Test that fallback removes duplicates."""
        query = "Bitcoin bitcoin BTC btc"
        
        keywords = extract_keywords_fallback(query)
        
        # Should be lowercase and deduplicated
        assert 'bitcoin' in keywords
        assert keywords.count('bitcoin') == 1
    
    def test_expand_keywords_with_fallback(self):
        """Test keyword expansion with fallback (LLM disabled)."""
        query = "Will federal shutdown end by November?"
        
        keywords = expand_query_keywords(query, use_llm=False)
        
        assert len(keywords) > 0
        assert 'federal' in keywords or 'shutdown' in keywords
    
    @pytest.mark.skipif(
        not Path(project_root / "config" / "keys.env").exists(),
        reason="API key not available"
    )
    def test_expand_keywords_with_llm(self):
        """Test keyword expansion with LLM (requires API key)."""
        query = "Will Bitcoin reach $100k?"
        
        # This will call OpenAI API
        keywords = expand_query_keywords(query, use_llm=True)
        
        # Should have expanded keywords
        assert len(keywords) > 0
        
        # Should include Bitcoin-related terms
        assert any(k in ['bitcoin', 'btc', 'cryptocurrency'] for k in keywords)


class TestMarketSearch:
    """Test API search and market filtering."""
    
    def test_filter_markets_by_keywords(self):
        """Test market filtering by keywords."""
        # Mock market data
        markets = [
            {
                "question": "Will Bitcoin reach $100,000 by end of 2025?",
                "description": "Resolves YES if Bitcoin reaches $100k",
                "title": "Bitcoin $100k prediction"
            },
            {
                "question": "Will Ethereum reach $5,000 by end of 2025?",
                "description": "Resolves YES if Ethereum reaches $5k",
                "title": "Ethereum $5k prediction"
            },
            {
                "question": "Will federal shutdown end by November 15?",
                "description": "Resolves YES if shutdown ends",
                "title": "Federal shutdown end date"
            }
        ]
        
        # Filter for Bitcoin
        bitcoin_keywords = ['bitcoin', 'btc', '100k']
        filtered = filter_markets_by_keywords(markets, bitcoin_keywords)
        
        assert len(filtered) >= 1
        assert 'bitcoin' in filtered[0]['question'].lower()
    
    def test_filter_markets_phrase_matching(self):
        """Test that phrase matches score higher."""
        markets = [
            {"question": "Bitcoin price prediction", "description": "", "title": ""},
            {"question": "Will Bitcoin reach $100k?", "description": "", "title": ""},
            {"question": "Random market about other stuff", "description": "bitcoin", "title": ""}
        ]
        
        keywords = ['bitcoin', 'reach', '100k']
        filtered = filter_markets_by_keywords(markets, keywords)
        
        # Second market should rank higher (more keyword matches)
        if len(filtered) >= 2:
            assert '100k' in filtered[0]['question'].lower()
    
    def test_filter_markets_empty_keywords(self):
        """Test filtering with empty keywords returns all."""
        markets = [
            {"question": "Market 1", "description": "", "title": ""},
            {"question": "Market 2", "description": "", "title": ""}
        ]
        
        filtered = filter_markets_by_keywords(markets, [])
        
        assert len(filtered) == len(markets)


class TestDatabaseIntegration:
    """Test database storage and retrieval."""
    
    @pytest.mark.skipif(
        not Path(project_root / "polymarket_markets.db").exists(),
        reason="Database not set up"
    )
    def test_query_history_storage(self):
        """Test that queries are stored in database."""
        # This would require running the agent
        # Check if get_history works
        try:
            history = get_polymarket_history(limit=1)
            assert "history" in history
            assert "metadata" in history
        except FileNotFoundError:
            pytest.skip("Database not initialized")


class TestMultiUser:
    """Test multi-user session isolation."""
    
    def test_different_sessions(self, clean_workspace):
        """Test that different sessions have different IDs."""
        agent = PolymarketAgent()
        
        session1 = agent.generate_session_id()
        session2 = agent.generate_session_id()
        
        assert session1 != session2


class TestManifestIncrement:
    """Test file bus incremental filenames."""
    
    def test_manifest_increments(self, clean_workspace):
        """Test that manifest increments file IDs."""
        agent = PolymarketAgent()
        
        # Check initial state
        stats = agent.get_stats()
        assert stats['next_id'] == 1
        
        # After getting filepath, next_id should increment
        out_path = agent.manifest.get_next_filepath(subdir=OUT_DIR)
        
        # Check filename format
        assert out_path.name == "000001.json"
        
        # Check next ID incremented
        stats = agent.get_stats()
        assert stats['next_id'] == 2


class TestRunLogs:
    """Test run logging functionality."""
    
    def test_validation_empty_query(self, clean_workspace):
        """Test that empty query is rejected."""
        agent = PolymarketAgent()
        
        with pytest.raises(ValueError, match="Query cannot be empty"):
            agent.run(query="", limit=10)
    
    def test_validation_invalid_limit(self, clean_workspace):
        """Test that invalid limit is rejected."""
        agent = PolymarketAgent()
        
        with pytest.raises(ValueError, match="limit must be positive"):
            agent.run(query="Test query", limit=0)
        
        with pytest.raises(ValueError, match="exceeds maximum"):
            agent.run(query="Test query", limit=100)
    
    def test_run_log_creation(self, clean_workspace):
        """Test that run logs are created."""
        agent = PolymarketAgent()
        
        # Create a test log
        log_path = agent._write_run_log(
            run_id="test_run",
            query="Test query",
            session_id="test_session",
            output_path=None,
            status="failed",
            error="Test error"
        )
        
        assert log_path.exists()
        
        # Read and validate log
        with open(log_path, 'r') as f:
            log_data = json.load(f)
        
        assert log_data['run_id'] == "test_run"
        assert log_data['query'] == "Test query"
        assert log_data['session_id'] == "test_session"
        assert log_data['status'] == "failed"
        assert log_data['error'] == "Test error"
        assert log_data['agent'] == AGENT_NAME


