"""End-to-end tests for market data agents."""

import pytest
import json
from pathlib import Path

from src.agents.market_data_agent import MarketDataAgent
from src.agents.consumer_agent import ConsumerAgent
from src.bus.manifest import Manifest
from src.bus.file_bus import read_json


class TestMarketDataE2E:
    """End-to-end tests for market data system."""
    
    def test_producer_correctness(self, clean_workspace):
        """
        Test 1: Producer correctness.
        
        Validates:
        - SQL templating correct
        - Output file created with correct schema
        - Manifest incremented
        - Run log exists with SQL and output_path
        """
        # Initialize agent
        agent = MarketDataAgent()
        
        # Run query
        output_path = agent.run(
            template="by_symbol",
            params={"symbol_pattern": "%.C"}
        )
        
        # Assert output file exists
        assert output_path.exists(), "Output file should exist"
        
        # Read output
        output_data = read_json(output_path)
        
        # Assert schema structure
        assert "data" in output_data, "Output should have 'data' field"
        assert "metadata" in output_data, "Output should have 'metadata' field"
        assert isinstance(output_data["data"], list), "'data' should be a list"
        
        # Assert metadata
        metadata = output_data["metadata"]
        assert "query" in metadata, "Metadata should have 'query'"
        assert "timestamp" in metadata, "Metadata should have 'timestamp'"
        assert "row_count" in metadata, "Metadata should have 'row_count'"
        assert "agent" in metadata, "Metadata should have 'agent'"
        assert metadata["agent"] == "market-data-agent"
        
        # Assert SQL contains LIKE for symbol pattern
        sql = metadata["query"]
        assert "LIKE" in sql, "SQL should use LIKE for pattern matching"
        assert "market_data" in sql, "SQL should query market_data table"
        
        # Assert manifest incremented
        manifest = Manifest(agent.workspace)
        stats = manifest.get_stats()
        assert stats["total_runs"] == 1, "Should have 1 run recorded"
        assert stats["next_id"] == 2, "Next ID should be 2"
        
        # Assert output filename is 000001.json
        assert output_path.name == "000001.json", "First output should be 000001.json"
        
        # Assert run log exists
        logs_dir = agent.workspace / "logs"
        assert logs_dir.exists(), "Logs directory should exist"
        
        log_files = list(logs_dir.glob("*.json"))
        assert len(log_files) == 1, "Should have 1 log file"
        
        # Read log
        log_data = read_json(log_files[0])
        assert "sql" in log_data, "Log should contain SQL"
        assert "output_path" in log_data, "Log should contain output_path"
        assert "status" in log_data, "Log should contain status"
        assert log_data["status"] == "success", "Status should be success"
        assert str(output_path) in log_data["output_path"], "Log should reference output file"
    
    def test_producer_completeness(self, clean_workspace):
        """
        Test 2: Producer completeness.
        
        Validates:
        - All requested columns present
        - Data matches query filters
        """
        agent = MarketDataAgent()
        
        # Query with specific columns
        columns = ["symbol", "bid", "ask", "price"]
        output_path = agent.run(
            template="by_symbol",
            params={"symbol_pattern": "XCME.OZN.%"},
            columns=columns
        )
        
        # Read output
        output_data = read_json(output_path)
        data = output_data["data"]
        
        # Assert data returned
        assert len(data) > 0, "Should return data"
        
        # Assert all requested columns present
        first_record = data[0]
        for col in columns:
            assert col in first_record, f"Column {col} should be present"
        
        # Assert data matches filter (all symbols should match pattern)
        for record in data:
            symbol = record.get("symbol", "")
            assert symbol.startswith("XCME.OZN."), f"Symbol {symbol} should match pattern"
    
    def test_consumer(self, clean_workspace):
        """
        Test 3: Consumer.
        
        Validates:
        - Reads producer output
        - Validates schema
        - Emits derived artifact
        """
        # Run producer first
        producer = MarketDataAgent()
        producer_output = producer.run(
            template="by_symbol",
            params={"symbol_pattern": "%.C"},
            limit=10
        )
        
        # Run consumer
        consumer = ConsumerAgent()
        consumer_output = consumer.run(producer_output)
        
        # Assert consumer output exists
        assert consumer_output.exists(), "Consumer output should exist"
        
        # Read consumer output
        output_data = read_json(consumer_output)
        
        # Assert structure
        assert "data" in output_data
        assert "metadata" in output_data
        
        # Consumer wraps processed data as single record
        processed_data = output_data["data"][0]
        
        # Assert processed data has expected fields
        assert "source" in processed_data
        assert "statistics" in processed_data
        assert "source_row_count" in processed_data
        
        # Assert statistics computed
        stats = processed_data["statistics"]
        assert "total_records" in stats
        assert stats["total_records"] == 10, "Should process 10 records"
        
        # Assert consumer manifest
        consumer_manifest = Manifest(consumer.workspace)
        consumer_stats = consumer_manifest.get_stats()
        assert consumer_stats["total_runs"] == 1
    
    def test_manifest_increments(self, clean_workspace):
        """
        Test 4: Manifest increments.
        
        Validates:
        - Multiple runs create 000001.json, 000002.json, etc.
        - meta.json updated correctly
        """
        agent = MarketDataAgent()
        
        # Run 1
        output1 = agent.run(
            template="by_symbol",
            params={"symbol_pattern": "%.C"},
            limit=5
        )
        
        # Run 2
        output2 = agent.run(
            template="by_symbol",
            params={"symbol_pattern": "%.P"},
            limit=5
        )
        
        # Run 3
        output3 = agent.run(
            template="all_valid",
            limit=5
        )
        
        # Assert filenames
        assert output1.name == "000001.json"
        assert output2.name == "000002.json"
        assert output3.name == "000003.json"
        
        # Assert all files exist
        assert output1.exists()
        assert output2.exists()
        assert output3.exists()
        
        # Assert manifest
        manifest = Manifest(agent.workspace)
        stats = manifest.get_stats()
        assert stats["next_id"] == 4, "Next ID should be 4"
        assert stats["total_runs"] == 3, "Should have 3 runs"
    
    def test_run_logs(self, clean_workspace):
        """
        Test 5: Run logs.
        
        Validates:
        - Log file created per run
        - Contains sql, output_path, timestamp
        - JSON format valid
        """
        agent = MarketDataAgent()
        
        # Run query
        output_path = agent.run(
            template="by_date",
            params={"file_date": "2025-07-21"}
        )
        
        # Check logs directory
        logs_dir = agent.workspace / "logs"
        log_files = list(logs_dir.glob("*.json"))
        
        assert len(log_files) == 1, "Should have exactly 1 log file"
        
        # Read log
        log_data = read_json(log_files[0])
        
        # Assert required fields
        required_fields = [
            "run_id",
            "sql",
            "params",
            "output_path",
            "status",
            "row_count",
            "timestamp",
            "duration_ms",
            "agent",
            "version"
        ]
        
        for field in required_fields:
            assert field in log_data, f"Log should contain {field}"
        
        # Assert values
        assert log_data["status"] == "success"
        assert log_data["agent"] == "market-data-agent"
        assert log_data["sql"] != "", "SQL should not be empty"
        assert log_data["output_path"] == str(output_path)
        assert "file_date" in log_data["params"]
        assert log_data["params"]["file_date"] == "2025-07-21"
        
        # Assert duration is reasonable (> 0, < 10 seconds)
        assert log_data["duration_ms"] > 0
        assert log_data["duration_ms"] < 10000
    
    def test_validation_errors(self, clean_workspace):
        """Test validation catches invalid inputs."""
        agent = MarketDataAgent()
        
        # Test invalid template
        with pytest.raises(ValueError, match="Invalid template"):
            agent.run(template="invalid_template")
        
        # Test missing required params
        with pytest.raises(ValueError, match="requires params"):
            agent.run(template="by_symbol")
        
        # Test invalid limit
        with pytest.raises(ValueError, match="Limit must be positive"):
            agent.run(template="all_valid", limit=-1)
    
    def test_full_pipeline(self, clean_workspace):
        """
        Test 6: Full pipeline (seed → produce → consume).
        
        Integration test of complete flow.
        """
        # Producer
        producer = MarketDataAgent()
        producer_output1 = producer.run(
            template="by_symbol",
            params={"symbol_pattern": "XCME.OZN.%"},
            columns=["symbol", "bid", "ask", "price"],
            limit=20
        )
        
        producer_output2 = producer.run(
            template="by_date",
            params={"file_date": "2025-07-21"},
            limit=15
        )
        
        # Consumer processes both
        consumer = ConsumerAgent()
        consumer_output1 = consumer.run(producer_output1)
        consumer_output2 = consumer.run(producer_output2)
        
        # Assert producer artifacts
        assert producer_output1.name == "000001.json"
        assert producer_output2.name == "000002.json"
        
        # Assert consumer artifacts
        assert consumer_output1.name == "000001.json"
        assert consumer_output2.name == "000002.json"
        
        # Assert manifests
        producer_manifest = Manifest(producer.workspace)
        assert producer_manifest.get_stats()["total_runs"] == 2
        
        consumer_manifest = Manifest(consumer.workspace)
        assert consumer_manifest.get_stats()["total_runs"] == 2
        
        # Assert logs exist for all runs
        producer_logs = list((producer.workspace / "logs").glob("*.json"))
        consumer_logs = list((consumer.workspace / "logs").glob("*.json"))
        
        assert len(producer_logs) == 2, "Should have 2 producer logs"
        assert len(consumer_logs) == 2, "Should have 2 consumer logs"
        
        # Read and validate consumer outputs
        consumer_data1 = read_json(consumer_output1)
        consumer_data2 = read_json(consumer_output2)
        
        assert consumer_data1["data"][0]["source"] == "market-data-agent"
        assert consumer_data2["data"][0]["source"] == "market-data-agent"
        
        # Verify statistics were computed
        stats1 = consumer_data1["data"][0]["statistics"]
        stats2 = consumer_data2["data"][0]["statistics"]
        
        assert stats1["total_records"] == 20
        assert stats2["total_records"] == 15
        
        print("\n✓ Full pipeline test passed!")
        print(f"  Producer outputs: {producer_output1.name}, {producer_output2.name}")
        print(f"  Consumer outputs: {consumer_output1.name}, {consumer_output2.name}")
        print(f"  All artifacts validated successfully")


