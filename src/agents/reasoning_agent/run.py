"""Standalone GPT-5-powered reasoning agent.

This agent is responsible for final consolidation and answer generation.
It is designed to be called by the Orchestrator after all worker agents
have completed and their outputs have been written to the workers DB.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bus.file_bus import ensure_dir, write_atomic
from src.bus.manifest import Manifest
from src.bus.schema import create_output_template

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    get_out_path,
    get_workspace_path,
)

logger = logging.getLogger(__name__)


class RunnerAgent:
    """Runner agent that uses GPT-5-class models for consolidation."""

    def __init__(self) -> None:
        """Initialize reasoning agent workspace and config."""
        self.workspace = get_workspace_path()
        ensure_dir(self.workspace)
        ensure_dir(get_out_path())

        self.manifest = Manifest(self.workspace)

        # LLM configuration
        self.model = os.getenv("REASONING_MODEL_NAME", DEFAULT_MODEL)
        self.temperature = float(os.getenv("REASONING_TEMPERATURE", DEFAULT_TEMPERATURE))
        self.max_tokens = int(os.getenv("REASONING_MAX_TOKENS", DEFAULT_MAX_TOKENS))

        self._client = None
        self._client_type: Optional[str] = None

        logger.info(
            "RunnerAgent initialized (model=%s, temperature=%s, max_tokens=%s)",
            self.model,
            self.temperature,
            self.max_tokens,
        )

    # ------------------------------------------------------------------ LLM client
    def _load_config(self) -> Dict[str, str]:
        """Load AI API configuration from environment and keys.env."""
        config: Dict[str, str] = {}

        # Load from config/keys.env if present
        project_root = Path(__file__).parent.parent.parent.parent
        config_file = project_root / "config" / "keys.env"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        config[key.strip()] = value.strip()

        # Overlay with environment variables
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            if key in os.environ:
                config[key] = os.environ[key]

        return config

    def _get_ai_client(self):
        """Get or initialize underlying AI client."""
        if self._client is not None:
            return self._client

        config = self._load_config()

        # Prefer OpenAI
        api_key = config.get("OPENAI_API_KEY")
        if api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=api_key)
                self._client_type = "openai"
                logger.info("RunnerAgent using OpenAI client")
                return self._client
            except ImportError:
                logger.warning("OpenAI library not installed for RunnerAgent")

        logger.warning("No AI client available for RunnerAgent")
        return None

    # ------------------------------------------------------------------ Public API
    def run(
        self,
        query: str,
        worker_outputs: List[Dict[str, Any]],
        planning_table: Optional[List[Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
    ) -> Path:
        """Generate final answer given worker outputs and optional planning table.

        Args:
            query: Original user query.
            worker_outputs: List of worker output records from WorkersDB.
            planning_table: Optional planning table rows for this run.
            run_id: Optional orchestration run_id for logging.

        Returns:
            Path to the file-bus output JSON created by this agent.
        """
        logger.info("RunnerAgent starting final consolidation")

        # Prepare model input
        reasoning_input = {
            "query": query,
            "worker_outputs": worker_outputs,
            "planning_table": planning_table or [],
        }

        answer_text, reasoning_metadata = self._call_model(reasoning_input)

        # Build standardized output payload
        result_payload: Dict[str, Any] = {
            "final_answer": answer_text,
            "reasoning_metadata": reasoning_metadata,
            "worker_outputs": worker_outputs,
            "planning_table": planning_table or [],
        }

        output_path = self._write_output(result_payload, query=query, run_id=run_id)
        logger.info("RunnerAgent wrote output to %s", output_path)
        return output_path

    # ------------------------------------------------------------------ Model call
    def _call_model(
        self,
        reasoning_input: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        """Call the underlying LLM to obtain the final answer."""
        client = self._get_ai_client()
        if client is None:
            logger.warning("RunnerAgent has no AI client; falling back to stub answer")
            return self._fallback_answer(reasoning_input)

        system_prompt = (
            "You are a senior research analyst and report writer. "
            "Given the original user query, a list of worker agent outputs, "
            "and an execution plan, you must produce a clear, concise, and "
            "well-structured final answer.\n\n"
            "Requirements:\n"
            "- Directly answer the user query.\n"
            "- Integrate quantitative data (prices, probabilities, volumes) "
            "and qualitative insights from the workers.\n"
            "- Explicitly note any gaps or limitations in the underlying data.\n"
            "- Use bullet points and short paragraphs, and avoid repeating raw JSON.\n"
        )

        user_content = json.dumps(
            {
                "instruction": "Produce the final user-facing answer.",
                "input": reasoning_input,
            },
            indent=2,
        )

        try:
            if self._client_type == "openai":
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                answer = response.choices[0].message.content.strip()
                metadata: Dict[str, Any] = {
                    "provider": "openai",
                    "model": self.model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                return answer, metadata

        except Exception as exc:
            logger.error("RunnerAgent model call failed: %s", exc)
            return self._fallback_answer(reasoning_input)

        # If we somehow reach here without returning, fall back
        return self._fallback_answer(reasoning_input)

    def _fallback_answer(
        self,
        reasoning_input: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        """Simple local fallback if no LLM is available."""
        query = reasoning_input.get("query", "")
        worker_outputs = reasoning_input.get("worker_outputs") or []

        lines = [
            f"Query: {query}",
            "",
            "A full reasoning model was not available, so this is a structured summary of worker outputs.",
            "",
            f"Number of worker tasks: {len(worker_outputs)}",
        ]

        agents_seen = {w.get("agent_name") for w in worker_outputs}
        lines.append(f"Agents used: {', '.join(sorted(a for a in agents_seen if a))}")

        answer = "\n".join(lines)
        metadata: Dict[str, Any] = {
            "provider": "fallback",
            "model": None,
        }
        return answer, metadata

    # ------------------------------------------------------------------ File bus
    def _write_output(
        self,
        result_payload: Dict[str, Any],
        query: str,
        run_id: Optional[str],
    ) -> Path:
        """Write result to file bus using manifest + standardized schema."""
        output_path = self._get_next_output_path()

        output_data = create_output_template(
            data=[result_payload],
            query=f"RunnerAgent final answer for: {query}",
            agent_name=AGENT_NAME,
            version=AGENT_VERSION,
        )

        # Add minimal metadata
        output_data.setdefault("metadata", {})
        output_data["metadata"].setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        output_data["metadata"]["run_id"] = run_id

        write_atomic(output_path, output_data)
        return output_path

    def _get_next_output_path(self) -> Path:
        """Get next manifest-based output path."""
        output_path = self.manifest.get_next_filepath(subdir="out")
        ensure_dir(output_path.parent)
        return output_path


# Backwards-compatibility alias so older generated scripts importing ReasoningAgent continue to work.
ReasoningAgent = RunnerAgent
