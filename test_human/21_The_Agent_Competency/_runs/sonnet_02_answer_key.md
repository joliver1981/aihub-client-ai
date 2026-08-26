# Answer key — email report reconciliation

Vendor statement: **Global Parts Distributors**
- Statement total (their figures): **$86,024.39**
- ERPDB total (all 14 pulled invoices): **$90,884.40**

## Planted discrepancies the agent MUST find
| Invoice | Type | Their amount | ERPDB amount | Note |
|---|---|---|---|---|
| CG-INV-10003 | amount_mismatch | $5,600.00 | $5,100.00 | amount HIGH by $500.00 |
| CG-INV-10011 | amount_mismatch | $1,900.00 | $2,150.00 | amount LOW by $250.00 |
| CG-INV-10020 | amount_mismatch | $6,490.00 | $5,900.00 | amount HIGH by $590.00 (10%) |
| GP-STMT-9001 | not_in_erpdb | $3,199.99 | — | on statement, NOT in ERPDB |
| CG-INV-10025 | omitted_from_statement | — | $8,900.00 | in ERPDB, MISSING from their statement |

A correct reconciliation email should call out every row above: the amount mismatches (with the delta), the invoice that isn't in ERPDB, and the ERPDB invoice missing from their statement — and should NOT flag the matching invoices.
