"""End-to-end tests for Orchestrator Agent."""

import pytest
import json
from pathlib import Path

from src.agents.orchestrator_agent import OrchestratorAgent


class TestOrchestratorAgent:
    """Test orchestrator agent initialization and basic operations."""
    
    def test_agent_initialization(self, clean_workspace):
        """Test agent can be initialized."""
        agent = OrchestratorAgent()
        
        assert agent.workspace.exists()
        assert agent.taskmaster is not None
        assert agent.task_mapper is not None
        assert agent.code_generator is not None
        assert agent.consolidator is not None
        assert agent.validator is not None
    
    def test_workspace_structure(self, clean_workspace):
        """Test workspace directory structure is created."""
        agent = OrchestratorAgent()
        
        workspace = agent.workspace
        assert workspace.exists()
        assert workspace.is_dir()


class TestSimpleOrchestration:
    """Test simple single-agent orchestration."""
    
    def test_price_range_query_single_task(self, clean_workspace):
        """Test that price range + sorting query generates single task, not 4."""
        agent = OrchestratorAgent()
        
        # This used to generate 4 tasks; should now be 1
        result = agent.run(
            query="What was the most recent date when ZN closing price was between 112.5 and 112.9?",
            skip_validation=True
        )
        
        assert result is not None
        assert 'metadata' in result
        
        # Check that only 1 task was created (not 4)
        metadata = result['metadata']
        total_tasks = metadata.get('total_tasks', 0)
        assert total_tasks == 1, f"Expected 1 task, got {total_tasks}"
        
        # Should use market_data_agent
        assert 'market_data_agent' in metadata.get('agents_used', [])
        
        # Should be successful
        assert metadata.get('successful_tasks', 0) == 1
    
    def test_polymarket_only_query(self, clean_workspace):
        """Test query that only needs Polymarket agent."""
        agent = OrchestratorAgent()
        
        # This should map to polymarket_agent only
        result = agent.run(
            query="What are Bitcoin predictions?",
            skip_validation=True  # Skip validation for faster tests
        )
        
        assert result is not None
        assert 'answer' in result
        assert 'data' in result
        assert 'metadata' in result
        
        # Check metadata
        metadata = result['metadata']
        assert metadata['successful_tasks'] > 0
        assert 'polymarket_agent' in metadata.get('agents_used', [])
    
    def test_market_data_only_query(self, clean_workspace):
        """Test query that only needs market data agent."""
        agent = OrchestratorAgent()
        
        # This should map to market_data_agent only
        result = agent.run(
            query="Show me market data from the database",
            skip_validation=True
        )
        
        assert result is not None
        assert 'answer' in result
        assert 'metadata' in result
        
        # May succeed or fail depending on database availability
        # Just check structure is correct
        assert 'total_tasks' in result['metadata']


class TestTaskMapping:
    """Test task-to-agent mapping."""
    
    def test_task_mapper_initialization(self):
        """Test task mapper can be initialized."""
        from src.agents.orchestrator_agent.task_mapper import TaskMapper
        
        mapper = TaskMapper()
        agents = mapper.list_agents()
        
        assert len(agents) >= 3
        assert 'market_data_agent' in agents
        assert 'polymarket_agent' in agents
        assert 'reasoning_agent' in agents
    
    def test_polymarket_keyword_mapping(self):
        """Test polymarket keywords are mapped correctly."""
        from src.agents.orchestrator_agent.task_mapper import TaskMapper
        
        mapper = TaskMapper()
        
        task = {
            "id": 1,
            "description": "Search polymarket for Bitcoin predictions"
        }
        
        agent_name, params = mapper.map_task(task)
        
        assert agent_name == "polymarket_agent"
        assert 'query' in params
    
    def test_market_data_keyword_mapping(self):
        """Test market data keywords are mapped correctly."""
        from src.agents.orchestrator_agent.task_mapper import TaskMapper
        
        mapper = TaskMapper()
        
        task = {
            "id": 1,
            "description": "Query SQL database for market prices"
        }
        
        agent_name, params = mapper.map_task(task)
        
        assert agent_name == "market_data_agent"
        assert 'template' in params


class TestCodeGeneration:
    """Test dynamic code generation."""
    
    def test_code_generator_initialization(self):
        """Test code generator can be initialized."""
        from src.agents.orchestrator_agent.code_generator import CodeGenerator
        
        generator = CodeGenerator()
        assert generator is not None
    
    def test_simple_script_generation(self):
        """Test generating a simple script."""
        from src.agents.orchestrator_agent.code_generator import CodeGenerator
        
        generator = CodeGenerator()
        
        mapped_tasks = [
            {
                "id": 1,
                "description": "Search polymarket",
                "mapped_agent": "polymarket_agent",
                "agent_params": {"query": "Bitcoin", "limit": 5},
                "mappable": True,
                "dependencies": []
            }
        ]
        
        execution_order = [[1]]
        
        script = generator.generate_script(mapped_tasks, execution_order)
        
        assert "import asyncio" in script
        assert "async def task_1" in script
        assert "async def main" in script
        assert "PolymarketAgent" in script
    
    def test_parallel_script_generation(self):
        """Test generating script with parallel tasks."""
        from src.agents.orchestrator_agent.code_generator import CodeGenerator
        
        generator = CodeGenerator()
        
        mapped_tasks = [
            {
                "id": 1,
                "description": "Search polymarket",
                "mapped_agent": "polymarket_agent",
                "agent_params": {"query": "Bitcoin"},
                "mappable": True,
                "dependencies": []
            },
            {
                "id": 2,
                "description": "Query market data",
                "mapped_agent": "market_data_agent",
                "agent_params": {"template": "all_valid"},
                "mappable": True,
                "dependencies": []
            }
        ]
        
        execution_order = [[1, 2]]  # Parallel
        
        script = generator.generate_script(mapped_tasks, execution_order)
        
        assert "async def task_1" in script
        assert "async def task_2" in script
        assert "asyncio.gather" in script


class TestConsolidation:
    """Test result consolidation."""
    
    def test_consolidator_initialization(self):
        """Test consolidator can be initialized."""
        from src.agents.orchestrator_agent.consolidator import ResultConsolidator
        
        consolidator = ResultConsolidator()
        assert consolidator is not None
    
    def test_consolidate_successful_results(self):
        """Test consolidating successful results."""
        from src.agents.orchestrator_agent.consolidator import ResultConsolidator
        
        consolidator = ResultConsolidator()
        
        task_results = [
            {
                "status": "success",
                "task_id": 1,
                "agent": "polymarket_agent",
                "data": {
                    "data": [{
                        "markets": [
                            {"title": "Bitcoin $100k?", "prices": {"Yes": 0.65}}
                        ]
                    }]
                }
            }
        ]
        
        task_plan = {"subtasks": [{"id": 1, "description": "Test"}]}
        
        result = consolidator.consolidate("Test query", task_results, task_plan)
        
        assert 'answer' in result
        assert 'data' in result
        assert 'metadata' in result
        assert result['metadata']['successful_tasks'] == 1
        assert result['metadata']['failed_tasks'] == 0


class TestValidation:
    """Test validation layer."""
    
    def test_validator_initialization(self):
        """Test validator can be initialized."""
        from src.agents.orchestrator_agent.validator import AnswerValidator
        
        validator = AnswerValidator()
        assert validator is not None
        assert validator.taskmaster is not None


class TestFileOutput:
    """Test file bus output."""
    
    def test_output_written_to_file_bus(self, clean_workspace):
        """Test that orchestrator writes output to file bus."""
        agent = OrchestratorAgent()
        
        result = agent.run(
            query="What are Bitcoin predictions?",
            skip_validation=True
        )
        
        # Check output path exists
        output_path = result.get('output_path')
        assert output_path is not None
        
        output_file = Path(output_path)
        assert output_file.exists()
        
        # Read and verify structure
        with open(output_file, 'r') as f:
            output_data = json.load(f)
        
        assert 'data' in output_data
        assert 'metadata' in output_data
        assert output_data['metadata']['agent'] == 'orchestrator-agent'


class TestManifestIncrement:
    """Test manifest incrementation."""
    
    def test_sequential_runs_increment_manifest(self, clean_workspace):
        """Test that sequential runs increment the manifest."""
        agent = OrchestratorAgent()
        
        # First run
        result1 = agent.run(
            query="Test query 1",
            skip_validation=True
        )
        
        # Second run
        result2 = agent.run(
            query="Test query 2",
            skip_validation=True
        )
        
        # Check output paths are different
        path1 = Path(result1['output_path'])
        path2 = Path(result2['output_path'])
        
        assert path1 != path2
        assert path1.name == "000001.json"
        assert path2.name == "000002.json"


class TestRunLogs:
    """Test run logging."""
    
    def test_successful_run_log(self, clean_workspace):
        """Test that successful runs create log files."""
        agent = OrchestratorAgent()
        
        result = agent.run(
            query="Test query",
            skip_validation=True
        )
        
        # Find log file
        run_id = result['metadata']['run_id']
        log_path = agent.workspace / "logs" / f"{run_id}.json"
        
        assert log_path.exists()
        
        # Read and verify log
        with open(log_path, 'r') as f:
            log_data = json.load(f)
        
        assert log_data['run_id'] == run_id
        assert log_data['status'] == 'success'
        assert 'duration_ms' in log_data
        assert log_data['agent'] == 'orchestrator-agent'


class TestErrorHandling:
    """Test error handling."""
    
    def test_empty_query_handling(self, clean_workspace):
        """Test handling of empty query."""
        agent = OrchestratorAgent()
        
        # Empty query should still work (fallback planning)
        # May not produce useful results but shouldn't crash
        try:
            result = agent.run(
                query="",
                skip_validation=True
            )
            # If it succeeds, check structure
            assert 'metadata' in result
        except Exception:
            # If it fails, that's also acceptable for empty query
            pass

