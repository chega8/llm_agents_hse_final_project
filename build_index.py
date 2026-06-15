#!/usr/bin/env python3
"""Построение FAISS-индекса по нормативным документам (запускать один раз)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from credit_agent.rag import build_index

if __name__ == "__main__":
    build_index()
