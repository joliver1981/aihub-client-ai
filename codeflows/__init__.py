"""
Code Flows — LLM-authored multi-step processes built as a WORKFLOW of Code
Step nodes, reusing the existing workflow execution engine (James's design:
code-only node palette, no second engine). See docs/code-flows-plan.md.

A Code Flow is a workflow (in the Workflows table, marked kind='code_flow')
whose nodes are inline Code Steps (LLM-authored Python running through the
Automations runner). The engine's pass/fail edges provide the control flow;
a failed step follows its 'fail' edge to an alert step.
"""
