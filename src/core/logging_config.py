"""Centralized logging configuration for market data puller."""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Create logs directory if it doesn't exist (at project root)
import os as _os
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Log file with timestamp
LOG_FILE = LOG_DIR / f"market_data_puller_{datetime.now().strftime('%Y%m%d')}.log"


def setup_logging(level=logging.INFO, log_to_file=True, log_to_console=True):
    """
    Set up logging configuration for the entire application.
    
    Args:
        level: Logging level (default: INFO)
        log_to_file: Whether to log to file (default: True)
        log_to_console: Whether to log to console (default: True)
    """
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # File handler
    if log_to_file:
        file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # Log startup
    root_logger.info("="*80)
    root_logger.info("Market Data Puller - Logging initialized")
    root_logger.info(f"Log level: {logging.getLevelName(level)}")
    root_logger.info(f"Log file: {LOG_FILE}")
    root_logger.info("="*80)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.
    
    Args:
        name: Module name (usually __name__)
    
    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Initialize logging on import (can be reconfigured later)
if not logging.getLogger().handlers:
    setup_logging()

