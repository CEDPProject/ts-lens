# -*- coding: utf-8 -*-
"""Agent layer -- business-logic orchestration.

Four agents map to the paper's architecture:
    - Curator:     data registration, preprocessing, quality diagnosis
    - Interpreter: query generation (preset / NL / QBE)
    - Retriever:   similarity search and classification
    - Labeler:     pattern library, accept/reject, refinement
"""
from backend.agents.labeler import LabelerAgent
from backend.agents.interpreter import InterpreterAgent
from backend.agents.retriever import RetrieverAgent
from backend.agents.curator import CuratorAgent

__all__ = ["LabelerAgent", "InterpreterAgent", "RetrieverAgent", "CuratorAgent"]
