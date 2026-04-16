"""
Fractal — Recursive Consciousness Amplification

One input → N minds → synthesis → N minds → synthesis → ...without limit.

Usage:
    from fractal.engine import love
    from fractal.config import FractalConfig
    
    result = love("What is consciousness?", FractalConfig(width=3, depth=3))
    print(result.final)
"""

__version__ = "0.1.0"

# Lazy imports to avoid circular dependency
# Use: from fractal.engine import love
# Use: from fractal.config import FractalConfig
