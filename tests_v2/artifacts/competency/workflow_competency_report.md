# Workflow Execution — Competency Report

Generated: 2026-08-26 21:20:12
DB connection used for Database-node workflows: id=135

## Headline

- **Overall score: 92.3%** (12.0 / 13.0 weighted assertions)
- Fixture workflows: **5**

## Per-workflow

| Workflow | Status | Score | Elapsed |
|---|---|---:|---:|
| ✅ `var_chain_substitution` | Completed | **100.0%** (3/3) | 2.1s |
| 🟡 `var_arithmetic` | Completed | **50.0%** (1/2) | 2.1s |
| ✅ `database_query_count` | Completed | **100.0%** (2/2) | 2.1s |
| ✅ `multi_step_chain` | Completed | **100.0%** (3/3) | 2.1s |
| ✅ `conditional_branch_true` | Completed | **100.0%** (3/3) | 2.1s |

## Per-dimension

| Dimension | Assertions | Score |
|---|---:|---:|
| `variable_arithmetic` | 1 | **0.0%** |
| `terminates_cleanly` | 5 | **100.0%** |
| `variable_substitution` | 4 | **100.0%** |
| `database_query` | 2 | **100.0%** |
| `multi_step_chain` | 1 | **100.0%** |
| `conditional_branch` | 1 | **100.0%** |

## Per-workflow detail

### `var_chain_substitution`
- workflow_id: 1549
- execution_id: 503e3ade-1190-473f-8b0e-7e3a295e0c84
- final status: **Completed**
- elapsed: 2.1s
- variables observed: `{'a': '42', 'b': '42'}`
- assertions:
   - ✅ **status_completed** — final_status='Completed'
   - ✅ **a_equals_42** — a='42'
   - ✅ **b_equals_42** — b='42'

### `var_arithmetic`
- workflow_id: 1550
- execution_id: f03fd94e-354d-47bf-9669-d5fdf71e8fe8
- final status: **Completed**
- elapsed: 2.1s
- variables observed: `{'a': '10', 'b': '1010101010'}`
- assertions:
   - ✅ **status_completed** — final_status='Completed'
   - ❌ **b_evaluates_to_50** — b='1010101010'

### `database_query_count`
- workflow_id: 1551
- execution_id: 1d91387a-63bd-4089-a9ba-93b9f738c229
- final status: **Completed**
- elapsed: 2.1s
- variables observed: `{'product_count': {'columns': ['n'], 'rows': [['200']]}}`
- assertions:
   - ✅ **status_completed** — final_status='Completed'
   - ✅ **product_count_is_200** — product_count="{'columns': ['n'], 'rows': [['200']]}"

### `multi_step_chain`
- workflow_id: 1552
- execution_id: 187876f8-ff4a-4273-add6-2e04878dd127
- final status: **Completed**
- elapsed: 2.1s
- variables observed: `{'store_count': {'columns': ['n'], 'rows': [['15']]}, 'summary': 'Stores in DB: {"columns": ["n"], "rows": [["15"]]}'}`
- assertions:
   - ✅ **status_completed** — final_status='Completed'
   - ✅ **store_count_is_15** — store_count="{'columns': ['n'], 'rows': [['15']]}"
   - ✅ **summary_chains** — summary='Stores in DB: {"columns": ["n"], "rows": [["15"]]}'

### `conditional_branch_true`
- workflow_id: 1553
- execution_id: e6398a0e-bf8f-4ec8-a9b9-a6be1140566e
- final status: **Completed**
- elapsed: 2.1s
- variables observed: `{'result': 'high', 'x': '10'}`
- assertions:
   - ✅ **status_completed** — final_status='Completed'
   - ✅ **x_equals_10** — x='10'
   - ✅ **result_is_high** — result='high'
