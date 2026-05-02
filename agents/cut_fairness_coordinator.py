"""
LangGraph coordinator for Cut Advisor + Fairness Agent.
Run with: python agents/cut_fairness_coordinator.py
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Any, TypedDict

from langgraph.graph import StateGraph, END

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cut_advisor_agent import CutAdvisorAgent
from agents.fairness_agent import FairnessAgent


class CoordinatorState(TypedDict, total=False):
    cut_result: Dict[str, Any]
    fairness_review: Dict[str, Any]
    final_result: Dict[str, Any]


def run_cut_advisor(state: CoordinatorState) -> CoordinatorState:
    agent = CutAdvisorAgent()
    result = agent.analyze(apply_fairness=False)
    return {"cut_result": result}


def run_fairness_review(state: CoordinatorState) -> CoordinatorState:
    fairness_agent = FairnessAgent()
    proposed = state.get("cut_result", {}).get("cut_priority_list", [])
    review = fairness_agent.validate_cut_list(proposed)
    return {"fairness_review": review}


def finalize_output(state: CoordinatorState) -> CoordinatorState:
    result = dict(state.get("cut_result", {}))
    review = state.get("fairness_review")

    if review:
        result["fairness_review"] = review
        if not review.get("approved") and review.get("substitution"):
            cut_list = result.get("cut_priority_list", [])
            if cut_list:
                replaced = cut_list[-1]
                substitution = review["substitution"]
                result["cut_priority_list"] = cut_list[:-1] + [substitution]
                review["applied_substitution"] = {
                    "replaced": replaced,
                    "added": substitution,
                }

    return {"final_result": result}


def build_graph():
    graph = StateGraph(CoordinatorState)
    graph.add_node("cut_advisor", run_cut_advisor)
    graph.add_node("fairness_review", run_fairness_review)
    graph.add_node("finalize", finalize_output)

    graph.set_entry_point("cut_advisor")
    graph.add_edge("cut_advisor", "fairness_review")
    graph.add_edge("fairness_review", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def run_coordinator() -> Dict[str, Any]:
    graph = build_graph()
    state = graph.invoke({})
    return state.get("final_result", {})


if __name__ == "__main__":
    result = run_coordinator()
    print("=== Cut + Fairness Coordination Result ===")
    print(f"Cut list: {result.get('cut_priority_list')}")
    print(f"Protected: {result.get('protected_regions')}")
    print(f"Fairness review: {result.get('fairness_review')}")
