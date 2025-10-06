"""
Logging configuration for CloudEdge library
"""

import logging
from typing import Optional


def setup_logger(name: str = "pycloudedge", level: int = logging.INFO) -> logging.Logger:
    """
    Set up logger for the library.
    
    Args:
        name: Logger name
        level: Logging level
        
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    
    # Only add handler if logger doesn't have one
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    logger.setLevel(level)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get logger instance.
    
    Args:
        name: Logger name (uses default if None)
        
    Returns:
        Logger instance
    """
    logger_name = f"pycloudedge.{name}" if name else "pycloudedge"
    return logging.getLogger(logger_name)
