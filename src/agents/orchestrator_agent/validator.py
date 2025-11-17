"""Answer Validation Layer using AI APIs."""

import logging
from typing import Dict, Any, Optional

from src.mcp.taskmaster_client import TaskPlannerClient

logger = logging.getLogger(__name__)


class AnswerValidator:
    """
    Validates consolidated answers using AI APIs (OpenAI/Anthropic).
    """
    
    def __init__(self):
        """Initialize validator with AI task planner client."""
        self.task_planner = TaskPlannerClient()
        logger.info("AnswerValidator initialized")
    
    def validate(
        self,
        query: str,
        answer: str,
        task_plan: Optional[Dict[str, Any]] = None,
        consolidated_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Validate if answer satisfies the query.
        
        Args:
            query: Original natural language query
            answer: Generated answer text
            task_plan: Optional task plan for context
            consolidated_result: Optional full result object
            
        Returns:
            Validation result with pass/fail and details
        """
        logger.info(f"Validating answer for query: '{query[:100]}...'")
        
        try:
            # Call AI validation
            validation_result = self.task_planner.validate_answer(
                query=query,
                answer=answer,
                task_plan=task_plan
            )
            
            # Enhance with local checks
            enhanced_result = self._enhance_validation(
                validation_result,
                query,
                answer,
                consolidated_result
            )
            
            # Log results
            if enhanced_result.get('valid'):
                score = enhanced_result.get('completeness_score', 0)
                logger.info(f"Validation PASSED (score: {score:.2f})")
            else:
                issues = enhanced_result.get('issues', [])
                logger.warning(f"Validation FAILED: {', '.join(issues)}")
            
            return enhanced_result
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            # Return error result
            return {
                "valid": False,
                "completeness_score": 0.0,
                "issues": [f"Validation error: {str(e)}"],
                "suggestions": ["Check AI API configuration (OpenAI/Anthropic keys)"],
                "method": "error"
            }
    
    def _enhance_validation(
        self,
        validation_result: Dict[str, Any],
        query: str,
        answer: str,
        consolidated_result: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Enhance AI validation with local checks.
        
        Args:
            validation_result: Result from AI validation
            query: Original query
            answer: Generated answer
            consolidated_result: Consolidated result object
            
        Returns:
            Enhanced validation result
        """
        enhanced = {**validation_result}
        
        # Add local checks
        local_checks = self._perform_local_checks(query, answer, consolidated_result)
        
        # Merge issues
        if 'issues' not in enhanced:
            enhanced['issues'] = []
        enhanced['issues'].extend(local_checks.get('issues', []))
        
        # Merge suggestions
        if 'suggestions' not in enhanced:
            enhanced['suggestions'] = []
        enhanced['suggestions'].extend(local_checks.get('suggestions', []))
        
        # Update validity if local checks found critical issues
        if local_checks.get('critical_issues'):
            enhanced['valid'] = False
            enhanced['issues'].append("Critical validation issues found")
        
        # Add check metadata
        enhanced['local_checks'] = local_checks
        
        return enhanced
    
    def _perform_local_checks(
        self,
        query: str,
        answer: str,
        consolidated_result: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Perform local validation checks.
        
        Args:
            query: Original query
            answer: Generated answer
            consolidated_result: Consolidated result object
            
        Returns:
            Local check results
        """
        issues = []
        suggestions = []
        critical_issues = False
        
        # Check 1: Answer length
        if len(answer) < 50:
            issues.append("Answer is very short (< 50 characters)")
            suggestions.append("Ensure worker agents returned sufficient data")
        
        # Check 2: Any failed tasks?
        if consolidated_result:
            metadata = consolidated_result.get('metadata', {})
            failed_count = metadata.get('failed_tasks', 0)
            
            if failed_count > 0:
                issues.append(f"{failed_count} task(s) failed to execute")
                suggestions.append("Review worker agent logs for errors")
                
                # If all tasks failed, mark as critical
                total_tasks = metadata.get('total_tasks', 0)
                if failed_count == total_tasks:
                    critical_issues = True
                    issues.append("ALL tasks failed - no results obtained")
        
        # Check 3: Worker data availability
        if consolidated_result:
            worker_outputs = consolidated_result.get('worker_outputs', [])
            if not worker_outputs:
                issues.append("No worker agent outputs available")
                critical_issues = True
        
        # Check 4: Query keyword coverage
        query_keywords = set(query.lower().split())
        answer_keywords = set(answer.lower().split())
        
        # Remove common words
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were'}
        query_keywords -= stopwords
        answer_keywords -= stopwords
        
        if query_keywords:
            overlap = len(query_keywords & answer_keywords) / len(query_keywords)
            if overlap < 0.3:
                issues.append(f"Low keyword overlap between query and answer ({overlap*100:.0f}%)")
                suggestions.append("Answer may not be addressing the query directly")
        
        return {
            "issues": issues,
            "suggestions": suggestions,
            "critical_issues": critical_issues,
            "checks_performed": [
                "answer_length",
                "task_failures",
                "worker_data_availability",
                "keyword_coverage"
            ]
        }
    
    def format_validation_report(self, validation_result: Dict[str, Any]) -> str:
        """
        Format validation result as human-readable report.
        
        Args:
            validation_result: Validation result dictionary
            
        Returns:
            Formatted report string
        """
        lines = []
        lines.append("="*60)
        lines.append("VALIDATION REPORT")
        lines.append("="*60)
        
        # Status
        status = "✓ PASSED" if validation_result.get('valid') else "✗ FAILED"
        lines.append(f"Status: {status}")
        
        # Score
        score = validation_result.get('completeness_score', 0)
        lines.append(f"Completeness Score: {score*100:.1f}%")
        
        # Method
        method = validation_result.get('method', 'unknown')
        lines.append(f"Method: {method}")
        
        # Issues
        issues = validation_result.get('issues', [])
        if issues:
            lines.append(f"\nIssues Found ({len(issues)}):")
            for i, issue in enumerate(issues, 1):
                lines.append(f"  {i}. {issue}")
        else:
            lines.append("\nIssues Found: None")
        
        # Suggestions
        suggestions = validation_result.get('suggestions', [])
        if suggestions:
            lines.append(f"\nSuggestions ({len(suggestions)}):")
            for i, suggestion in enumerate(suggestions, 1):
                lines.append(f"  {i}. {suggestion}")
        
        # Local checks
        local_checks = validation_result.get('local_checks', {})
        if local_checks:
            checks = local_checks.get('checks_performed', [])
            lines.append(f"\nLocal Checks Performed: {', '.join(checks)}")
        
        lines.append("="*60)
        
        return '\n'.join(lines)

