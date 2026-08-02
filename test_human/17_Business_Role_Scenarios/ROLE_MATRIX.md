# Department / role matrix — Continental Goods Co.

Every department and core role in a retail / wholesale / ecom business that sells products, mapped
to what the platform could actually do for them, and how well the **existing test data** supports
building that scenario today.

**Readiness:** ✅ real data + fixtures exist · 🟡 partial, needs seeding · ⬜ thin, data would need
inventing.

**Data behind the ✅s:** `AIRDB` (`TS.sales` ~2M rows, `Inventory`, `product_master`,
`location_master`, `cost_of_products`, `price_of_goods`, `plan_sales_data`, `employee_data`,
`store_traffic`, `calendar_master`) · `ERPDB` (`Invoices`, `InvoiceLineItems`, `CustomerPayments`,
`PaymentApplications`, `SalesOrders`, `EKKO`/`EKPO`/`EKBE`/`LFA1`/`T052`, `orders`,
`order_approvals`, `GeneralLedger`, `wms_*`) · `test_human` document fixtures · the Meridian 2FA
vendor portal · the localhost SFTP/FTP/FTPS server.

---

## 1. Executive

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| CEO | Owns the P&L and the plan | Scheduled state-of-the-business digest (sales vs plan, margin, cash) by email/SMS; NLQ follow-ups | 🟡 |
| CFO | Cash, margin, close, board reporting | Flash close pack; DSO/DPO and aging; margin-erosion detection; variance narrative from GL + sales; approval authority | ✅ |
| COO | Fulfillment, service levels, cost to serve | Fill-rate and late-order exception digest; cross-system ops brief | 🟡 |
| CMO | Demand generation, brand | Promo lift vs `price_of_goods` discounts; campaign→revenue attribution | 🟡 |

## 2. Finance & Accounting

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| **AP Clerk** | Matches vendor invoices to PO + receipt, codes, queues payment | **3-way match** (`EKKO`/`EKPO`/`EKBE`); invoice PDF → extract → match → exception queue; **2FA portal invoice download**; SFTP drop pickup; duplicate detection; discount capture from `T052` | ✅✅ |
| **AR Clerk / Collections** | Applies cash, chases open invoices, resolves short-pays | Cash application; aging and DSO; short-pay root-cause; approval-gated dunning; call prep | ✅✅ **built** |
| Staff / GL Accountant | Journals, reconciliations, close checklist | GL vs subledger reconciliation; accruals from received-not-invoiced; close checklist with checkpoints | ✅ |
| Controller | Owns close, controls, policy | Close-status dashboard; exception review inbox; policy Q&A; sign-off approvals | 🟡 |
| FP&A Analyst | Budget vs actual, forecast, board deck | Plan-vs-actual by product/store; driver-based re-forecast; auto commentary; chart artifacts | ✅ ⚠️ |
| Payroll / Treasury | Pay runs, cash positioning | Cash forecast from AR due dates + AP terms; payment-run proposal (proposes, never executes) | 🟡 |

> ⚠️ FP&A: `plan_sales_data` in AIRDB2 is known-broken (see the AIRDB2 landmines memo). Verify before
> building a plan-vs-actual scenario.

## 3. Merchandising & Buying

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Buyer | Places POs, negotiates cost, manages vendors | Reorder candidates (`Inventory` vs `min_stock_threshold`); vendor scorecard (`EKBE.BUDAT` vs `EKPO.EINDT`); cost-change detection; PO draft + approval | ✅ |
| Category / Merch Manager | Assortment, margin, lifecycle | Category performance NLQ; margin waterfall; slow-mover and markdown candidates | ✅ |
| Pricing Analyst | Price and discount architecture | Effective-price audit (date-bounded overlaps/gaps); discount leakage; price-change sim | ✅ |
| Vendor Manager | Vendor master, terms, compliance | `LFA1` hygiene audit; contract Q&A; portal document harvest | ✅ |

## 4. Supply Chain & Planning

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Demand Planner | Forecasts units by product/store | Forecast from `sales` + `calendar_master` seasonality; accuracy scoring; weekly refresh | ✅ |
| Inventory / Allocation Analyst | Right stock, right store | Stock-out and overstock exceptions; transfer proposals; weeks-of-supply | ✅ |
| Logistics Coordinator | Inbound and outbound movement | Late-inbound PO alerts; carrier file pickup over SFTP; ASN reconciliation | 🟡 |
| S&OP Lead | Ties demand, supply and plan | Consolidated monthly S&OP pack across AIRDB + ERPDB | 🟡 |

## 5. Warehouse & Distribution

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| DC / Warehouse Manager | Throughput, accuracy, labor | `wms_orders` throughput and backlog; pick-accuracy exceptions | 🟡 |
| Receiving Clerk | Receipts against POs | Receipt-vs-PO discrepancies; packing slip → extract → match | ✅ |
| Shipping / Fulfillment Lead | On-time out the door | Late/held queue from `orders.current_status` + `lead_time_days` | 🟡 |
| Returns / RMA Coordinator | Reverse logistics | Return-reason clustering; credit memo prep | ⬜ |

## 6. Wholesale / B2B Sales

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Account Manager | Owns named accounts | Account 360 in one prompt; pre-call brief; at-risk detection from order-cadence decay | ✅ |
| Inside Sales / Order Entry | Turns emailed POs into orders | Inbound email → PO extract → order draft → approval; order-status Q&A | ✅ |
| Sales Ops Analyst | Quota, territory, comp | Attainment vs `employee_data.monthly_sales_target`; commission calc; hygiene exceptions | ✅ |
| Regional Sales Manager | Team performance | Rep leaderboard; scheduled Monday territory pack | ✅ |

## 7. Retail Store Operations

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Director of Store Ops | Chain-wide performance | Traffic-adjusted conversion ranking; outlier detection | ✅ |
| District Manager | 5–10 stores | District daily flash pushed each morning; store-visit prep | ✅ |
| Store Manager | One store, one day | "How did we do yesterday?"; staffing vs traffic curve; low-stock list; shift notes | ✅ |
| Loss Prevention | Shrink and anomalies | Discount/void anomaly detection by employee | 🟡 |

## 8. Ecommerce & Digital

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Ecom Manager | Site revenue, conversion | Channel P&L; funnel and AOV; out-of-stock-on-site exceptions | 🟡 |
| Marketplace Manager | Amazon / Walmart etc. | **Portal workflow** to pull settlement reports; fee reconciliation vs revenue | ✅ |
| Ecom Ops / Content | PDP data quality | Catalog completeness vs `product_master`; bulk copy with review checkpoint | 🟡 |
| Web / Conversion Analyst | Tests and traffic | Conversion analysis; A/B readout in Code Interpreter | 🟡 |

## 9. Marketing

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Promotions / Campaign Manager | Runs and measures promos | Lift vs baseline using discount windows; margin-adjusted ROI | ✅ |
| CRM / Lifecycle Manager | Segments and sends | Segment builder via NLQ; **sends always approval-gated** | 🟡 |
| Brand / Content | Assets and messaging | Brand-guidelines agent; grounded drafting | ⬜ |

## 10. Customer Service

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| CS Representative | Order, invoice, return questions | Order/invoice lookup; inbound email triage with approval-gated replies; policy grounding | ✅ |
| CS Manager | Volume, SLA, escalations | Ticket-theme clustering; SLA breach digest | ⬜ |
| Billing Disputes / Escalations | Short-pays, credits, chargebacks | Dispute research pack assembled in one shot | ✅ |

## 11. HR / People Ops

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| HR Generalist | Policy, onboarding, questions | Handbook Q&A; onboarding checklist automation | 🟡 |
| Recruiter | Req pipeline | Resume screen against a rubric, human decision gate | ⬜ |
| Store HR / Scheduling | Staffing to demand | Staffing vs traffic recommendations | ✅ |

## 12. IT / Data & Analytics

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| BI / Data Analyst | Answers the business's questions | Data Explorer NLQ + Data Dictionary; ad-hoc deflection; scheduled delivery | ✅ |
| ERP / Systems Analyst | Keeps the ERP honest | Master-data quality audits; interface file monitoring; failed-job digest | ✅ |
| Data Engineer | Moves and shapes data | SFTP → parse → load → verify; source-vs-target reconciliation | ✅ |
| IT Helpdesk | Tickets | KB-grounded answers | ⬜ |

## 13. Legal / Compliance / Risk

| Role | What they do | Platform functions | Ready |
|---|---|---|---|
| Contracts Manager | Vendor and customer agreements | Obligation extraction; **term-vs-invoice conformance**; renewal calendar | ✅ |
| Compliance / Internal Audit | Controls testing | SoD and approval-threshold testing over `order_approvals`; sampling + evidence pack | 🟡 |

---

## Picking the next scenario

Favour roles where **the answer is verifiable to the penny** — a SQL query or a fixture answer key
as the oracle. AP and AR win on that; marketing and HR mostly don't, which is why they sit at ⬜
regardless of how useful the feature would be.

Ranked by how much distinct platform surface a single day exercises:

1. **AP Clerk** — documents, 3-way match, 2FA portal, SFTP, exceptions, approvals, scheduling. The
   densest single role, and every step has a numeric right answer
2. **AR Clerk** — *built*. Cleanest math on the platform
3. **Inventory / Demand Planner** — best showcase of NLQ and Code Interpreter over 2M rows
4. **Buyer** — vendor scorecard plus PO approval, and it reuses the AP data
5. **Store Manager** — broadest audience, lightest build
