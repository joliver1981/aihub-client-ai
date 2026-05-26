# Large_Invoice_Reports_Isolated Agent-Knowledge — Competency Report

Generated: 2026-05-24 22:04:52
Agent: id=8 agents (one per fixture) (deleted after run)

## Headline

- **Overall score: 100.0%** (62.0 / 62.0 weighted points)
- Questions asked: **62**
- Fixtures uploaded: **8**
- Leaks / forbidden-pattern hits: **0** ✅
- Questions that needed a clarification follow-up: **0** of 62

## Per-fixture competency

| Fixture | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `01_fedex_invoice_global_logistics_q1_2026.pdf` | 13 | **100.0%** | 13.0/13.0 |
| `01_financial_report_global_logistics_fy2025.xlsx` | 8 | **100.0%** | 8.0/8.0 |
| `02_fedex_invoice_megaretail_q1_2026.pdf` | 8 | **100.0%** | 8.0/8.0 |
| `02_financial_report_megaretail_fy2025.xlsx` | 7 | **100.0%** | 7.0/7.0 |
| `03_fedex_invoice_pacific_mfg_q1_2026.pdf` | 5 | **100.0%** | 5.0/5.0 |
| `03_financial_report_pacific_mfg_fy2025.xlsx` | 7 | **100.0%** | 7.0/7.0 |
| `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` | 7 | **100.0%** | 7.0/7.0 |
| `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` | 7 | **100.0%** | 7.0/7.0 |

## Per-dimension competency

| Dimension | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `tier1_direct_lookup` | 19 | **100.0%** | 19.0/19.0 |
| `tier2_aggregation` | 11 | **100.0%** | 11.0/11.0 |
| `money_extraction` | 23 | **100.0%** | 23.0/23.0 |
| `tier3_filter_count` | 13 | **100.0%** | 13.0/13.0 |
| `tier4_comparison` | 11 | **100.0%** | 11.0/11.0 |
| `tier5_multi_step` | 8 | **100.0%** | 8.0/8.0 |
| `no_repeat_header` | 7 | **100.0%** | 7.0/7.0 |
| `no_repeat_header_long` | 7 | **100.0%** | 7.0/7.0 |

## All Q&A (for audit)

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What is the account holder name on this FedEx invoice?
- score: 1.0 | dimensions: tier1_direct_lookup | 11.4s
- answer:
    The account holder name is **“Global Logistics Corp”**.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** (Page 1: “Account holder: Global Logistics Corp”)

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What is the invoice number?
- score: 1.0 | dimensions: tier1_direct_lookup | 10.4s
- answer:
    The invoice number is **GLC-FX-Q1-026114**.  
    Source: *01_fedex_invoice_global_logistics_q1_2026.pdf*

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What is the account number?
- score: 1.0 | dimensions: tier1_direct_lookup | 9.7s
- answer:
    The account number is **2189-4471-X**.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** (“Account number: 2189-4471-X”)

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What billing period does this invoice cover?
- score: 1.0 | dimensions: tier1_direct_lookup | 10.2s
- answer:
    The invoice covers the billing period **“January 1 – March 31, 2026”**.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf**.

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — How many total shipments are on this invoice?
- score: 1.0 | dimensions: tier2_aggregation | 12.7s
- answer:
    There are **2,400 total shipments** on this invoice.
    
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf**, page 1 (“**Total shipments: 2,400**”).

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What is the grand total amount due on this invoice?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 9.9s
- answer:
    The grand total amount due is **$503,825.58**.
    
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** — Page 112 shows **“GRAND TOTAL $503,825.58”**.

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What is the total amount of fuel surcharges on this invoice?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 10.2s
- answer:
    The total fuel surcharges on this invoice are **$118,884.88**.
    
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf**, page 112 (“**Fuel surcharges $118,884.88**”).

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — How many FedEx Priority Overnight shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count | 10.1s
- answer:
    There are **227 FedEx Priority Overnight shipments** on this invoice.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** (Service summary on page 1; also confirmed on page 113).

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — How many FedEx Ground shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count | 12.3s
- answer:
    There are **737 FedEx Ground shipments** on this invoice.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** (“FedEx Ground — **737**” in the service summary; page 1).

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — How many FedEx Freight shipments (Priority + Economy combined) are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count | 11.7s
- answer:
    There are **314 FedEx Freight shipments** on this invoice.
    
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf** — page 1 shows:
    - “**FedEx Freight Economy — 124**”
    - “**FedEx Freight Priority — 190**”
    
    Page 113 also confirms:
    - “**FedEx Freight (Priority+Economy) shipments: 314**”

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — Which FedEx service tier had the highest total charges?
- score: 1.0 | dimensions: tier4_comparison | 13.5s
- answer:
    The highest total charges were for **“FedEx Freight Priority” — “$226,744.98.”**  
    Source: *01_fedex_invoice_global_logistics_q1_2026.pdf* (Page 1 service summary; also confirmed on Page 113: “#1 service by total charges: FedEx Freight Priority — 190 shipments, $226,744.98”).

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — What was the single largest individual shipment charge on this invoice?
- score: 1.0 | dimensions: tier4_comparison, money_extraction | 10.8s
- answer:
    The single largest individual shipment charge was **$2,861.37** for **tracking 792655369073**.  
    Source: **01_fedex_invoice_global_logistics_q1_2026.pdf**
    
    The invoice’s audit summary states: **“Largest single shipment charge: $2,861.37 (tracking 792655369073)”**.

### ✅ `01_fedex_invoice_global_logistics_q1_2026.pdf` — If all fuel surcharges were removed, what would the new grand total be?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 12.4s
- answer:
    If all fuel surcharges were removed, the new grand total would be **$384,940.70**.
    
    Source: *01_fedex_invoice_global_logistics_q1_2026.pdf*  
    - “GRAND TOTAL **$503,825.58**”  
    - “Fuel surcharges **$118,884.88**”

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — What is Global Logistics Corp's total revenue for FY2025?
- score: 1.0 | dimensions: tier1_direct_lookup, money_extraction | 8.4s
- answer:
    Global Logistics Corp’s total revenue for FY2025 is **$184,350,000.00**.
    
    Source: **01_financial_report_global_logistics_fy2025.xlsx**, sheet **“Anchors”** (“**Total revenue FY2025 | $184,350,000.00**”) and sheet **“Summary”** (“**Total revenue (FY2025) | 184350000.0**”).

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — What is Global Logistics Corp's net income for FY2025?
- score: 1.0 | dimensions: tier1_direct_lookup, money_extraction | 6.1s
- answer:
    Global Logistics Corp's net income for FY2025 is **$15,847,500.00**.
    
    Source: **01_financial_report_global_logistics_fy2025.xlsx**, sheet **“Anchors”** (`Net income FY2025 | $15,847,500.00`)

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — How many employees does Global Logistics Corp have at year-end FY2025?
- score: 1.0 | dimensions: tier1_direct_lookup | 14.4s
- answer:
    Global Logistics Corp had **1,842 employees** at year-end FY2025.
    
    Source: **01_financial_report_global_logistics_fy2025.xlsx** (Summary sheet)

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — What was the gross margin percentage for FY2025?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 13.1s
- answer:
    The FY2025 gross margin percentage was **41.28%**.
    
    Source: **01_financial_report_global_logistics_fy2025.xlsx** — the **Summary** sheet lists **“Gross margin | 0.4128”**, which equals **41.28%**.

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — How many customers are listed in the customer concentration table?
- score: 1.0 | dimensions: tier3_filter_count | 5.6s
- answer:
    The customer concentration table lists **25 customers**.
    
    This is shown in the sheet title: **"Top 25 customers by FY2025 revenue contribution"** in **01_financial_report_global_logistics_fy2025.xlsx**.

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — Which department has the largest headcount?
- score: 1.0 | dimensions: tier4_comparison | 12.4s
- answer:
    The department with the largest headcount is **Operations / Network** with **1124** employees.  
    Source: **01_financial_report_global_logistics_fy2025.xlsx** (“Departmental Spend” sheet)

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — Which customer is the company's biggest by revenue?
- score: 1.0 | dimensions: tier4_comparison | 10.4s
- answer:
    The biggest customer by revenue is **Vellichor Industries** with **FY2025 revenue of 21753300**.  
    Source: **01_financial_report_global_logistics_fy2025.xlsx**, sheet **“Customer Concentration”**.

### ✅ `01_financial_report_global_logistics_fy2025.xlsx` — What percentage of total revenue does the top customer represent?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 13.5s
- answer:
    The top customer represents **11.8%** of total revenue.
    
    From **01_financial_report_global_logistics_fy2025.xlsx**, sheet **“Customer Concentration”**, the top-ranked customer is **“Vellichor Industries”** with **“% of total” = 0.118**, which equals **11.8%**.

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — What is the account holder name on this FedEx invoice?
- score: 1.0 | dimensions: tier1_direct_lookup | 10.1s
- answer:
    The account holder name is **“Mega Retail Inc”**. Source: **02_fedex_invoice_megaretail_q1_2026.pdf**.

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — What is the invoice number?
- score: 1.0 | dimensions: tier1_direct_lookup | 10.5s
- answer:
    The invoice number is **“MRI-FX-Q1-026891”**.  
    Source: **02_fedex_invoice_megaretail_q1_2026.pdf** (Page 1)

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — How many shipments are on this invoice?
- score: 1.0 | dimensions: tier2_aggregation | 13.0s
- answer:
    This invoice has **3,000 shipments**. Source: **02_fedex_invoice_megaretail_q1_2026.pdf** (“**Total shipments: 3,000**”).

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — What is the grand total amount due?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 10.8s
- answer:
    The grand total amount due is **$289,478.82**.  
    Source: **02_fedex_invoice_megaretail_q1_2026.pdf** (Page 140: “Grand total due: $289,478.82”; Page 139: “GRAND TOTAL $289,478.82”)

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — How many FedEx Ground shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count | 10.7s
- answer:
    There are **1,357 FedEx Ground shipments** on the invoice.  
    Source: **02_fedex_invoice_megaretail_q1_2026.pdf** — the service summary lists **“FedEx Ground 1,357”**.

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — What is the total amount of residential delivery fees on this invoice?
- score: 1.0 | dimensions: tier3_filter_count, money_extraction | 13.3s
- answer:
    The total amount of **residential delivery fees** is **$3,564.75**.
    
    Source: **02_fedex_invoice_megaretail_q1_2026.pdf**, page 139 (“Residential delivery fees $3,564.75”).

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — Which service tier had the highest total charges on this invoice?
- score: 1.0 | dimensions: tier4_comparison | 11.2s
- answer:
    The highest total charges were for **“FedEx Ground”** at **“$78,650.76”**.  
    Source: **02_fedex_invoice_megaretail_q1_2026.pdf**
    
    The document’s audit page also states: **“#1 service by total charges: FedEx Ground — 1,357 shipments, $78,650.76.”**

### ✅ `02_fedex_invoice_megaretail_q1_2026.pdf` — What percentage of the grand total is made up of fuel surcharges?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 16.5s
- answer:
    Fuel surcharges make up **15.56%** of the grand total.
    
    From **02_fedex_invoice_megaretail_q1_2026.pdf**:
    - **“Fuel surcharges”**: **$45,029.14**
    - **“GRAND TOTAL”**: **$289,478.82**

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — What is Mega Retail's total FY2025 revenue?
- score: 1.0 | dimensions: tier1_direct_lookup, money_extraction | 6.4s
- answer:
    Mega Retail's total FY2025 revenue is **$412,900,000.00**.  
    Source: **02_financial_report_megaretail_fy2025.xlsx** (sheet: **Anchors** / **Summary**)

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — How many active SKUs does Mega Retail have?
- score: 1.0 | dimensions: tier1_direct_lookup | 12.7s
- answer:
    Mega Retail has **12,485 active SKUs**.
    
    Source: **02_financial_report_megaretail_fy2025.xlsx** — the file lists **“Active SKUs | 12,485”** in the **Anchors** sheet.

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — How many stores does Mega Retail have at year-end?
- score: 1.0 | dimensions: tier1_direct_lookup | 12.3s
- answer:
    Mega Retail had **284 stores at year-end**.
    
    Source: **02_financial_report_megaretail_fy2025.xlsx** — the Summary sheet lists **“Total store count (year-end) | 284.0”**, and the Anchors sheet lists **“Total stores year-end | 284.”**

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — What is Mega Retail's e-commerce revenue?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 8.2s
- answer:
    Mega Retail's **e-commerce revenue** is **$228,440,000.00**.  
    Source: **02_financial_report_megaretail_fy2025.xlsx** — listed as **“E-commerce revenue | 228440000.0”** in the Summary sheet and **“E-commerce revenue | $228,440,000.00”** in the Anchors sheet.

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — How many regions does Mega Retail track in its regional revenue table?
- score: 1.0 | dimensions: tier3_filter_count | 8.0s
- answer:
    Mega Retail tracks **7 regions** in its regional revenue table. The sheet lists:
    
    - Northeast US
    - Mid-Atlantic US
    - Southeast US
    - Southwest US
    - Midwest US
    - West Coast US

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — Which region produces the largest share of Mega Retail's revenue?
- score: 1.0 | dimensions: tier4_comparison | 15.5s
- answer:
    The region with the largest share of Mega Retail’s revenue is **Southwest US**.
    
    - **TOTAL revenue:** **102490 ($ thousands)**
    - **Share of total company revenue:** **19.80%**
    
    Source: **02_financial_report_megaretail_fy2025.xlsx** (sheet: **Regional Revenue**)

### ✅ `02_financial_report_megaretail_fy2025.xlsx` — How much larger is Mega Retail's e-commerce revenue than its in-store revenue?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 9.4s
- answer:
    Mega Retail's e-commerce revenue is **$43,980,000** larger than its in-store revenue.
    
    Source: **02_financial_report_megaretail_fy2025.xlsx** — `"E-commerce revenue | 228440000.0"` and `"In-store revenue | 184460000.0"` in the **Summary** sheet.

### ✅ `03_fedex_invoice_pacific_mfg_q1_2026.pdf` — What is the account holder name?
- score: 1.0 | dimensions: tier1_direct_lookup | 15.0s
- answer:
    The account holder name is **“Pacific Manufacturing LLC”**.  
    Source: **03_fedex_invoice_pacific_mfg_q1_2026.pdf** (Page 1)

### ✅ `03_fedex_invoice_pacific_mfg_q1_2026.pdf` — What is the grand total due on this invoice?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 12.4s
- answer:
    The grand total due is **$1,592,426.45**. Source: **03_fedex_invoice_pacific_mfg_q1_2026.pdf** (“GRAND TOTAL $1,592,426.45”).

### ✅ `03_fedex_invoice_pacific_mfg_q1_2026.pdf` — How many FedEx Freight (Priority + Economy) shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count | 13.2s
- answer:
    There are **1,326 FedEx Freight shipments** on this invoice.
    
    Source: **03_fedex_invoice_pacific_mfg_q1_2026.pdf** — page 103 states: **“FedEx Freight (Priority+Economy) shipments: 1,326”**.

### ✅ `03_fedex_invoice_pacific_mfg_q1_2026.pdf` — Which FedEx service tier accounts for the largest portion of charges?
- score: 1.0 | dimensions: tier4_comparison | 13.9s
- answer:
    The largest service tier by charges is **“FedEx Freight Priority”** at **$953,685.81**.
    
    The invoice’s service summary lists:
    - **FedEx Freight Priority — 786 shipments — $953,685.81**
    - **FedEx Freight Economy — 540 shipments — $577,328.30**
    
    Page 103 also states: **“#1 service by total charges: FedEx Freight Priority — 786 shipments, $953,685.81”**.  
    Source: **03_fedex_invoice_pacific_mfg_q1_2026.pdf**

### ✅ `03_fedex_invoice_pacific_mfg_q1_2026.pdf` — What proportion of this invoice's grand total is made up of fuel surcharges?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 15.1s
- answer:
    Fuel surcharges are **28.75%** of the invoice’s grand total.
    
    From **03_fedex_invoice_pacific_mfg_q1_2026.pdf**:
    - **“Fuel surcharges”**: **$457,889.06**
    - **“GRAND TOTAL”**: **$1,592,426.45**
    
    So, $457,889.06 ÷ $1,592,426.45 × 100 = **28.75%**.

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — What is Pacific Manufacturing's total FY2025 revenue?
- score: 1.0 | dimensions: tier1_direct_lookup, money_extraction | 6.0s
- answer:
    Pacific Manufacturing's total FY2025 revenue is **$248,700,000.00**.
    
    Source: **03_financial_report_pacific_mfg_fy2025.xlsx** (“Anchors” sheet: **“Total revenue FY2025 | $248,700,000.00”**; “Summary” sheet: **“Total revenue (FY2025) | 248700000”**).

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — How many production facilities does Pacific Manufacturing operate?
- score: 1.0 | dimensions: tier1_direct_lookup | 8.1s
- answer:
    Pacific Manufacturing operates **7 production facilities**.
    
    From the **“Production by Plant”** sheet in **03_financial_report_pacific_mfg_fy2025.xlsx**, the listed plants are:
    
    - Tacoma WA
    - Fresno CA
    - Phoenix AZ
    - Wichita KS

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — What was Pacific Manufacturing's EBITDA for FY2025?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction | 12.1s
- answer:
    Pacific Manufacturing's **EBITDA for FY2025** was **34,180,000**.
    
    Source: **03_financial_report_pacific_mfg_fy2025.xlsx** ("Summary" sheet)

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — How many inventory SKUs are tracked in the inventory snapshot?
- score: 1.0 | dimensions: tier3_filter_count | 11.0s
- answer:
    There are 120 inventory SKUs tracked in the "Inventory Snapshot" sheet, excluding the blank row and header row. Source: 03_financial_report_pacific_mfg_fy2025.xlsx.

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — Which plant has the highest production output?
- score: 1.0 | dimensions: tier4_comparison | 10.8s
- answer:
    The plant with the highest production output is **Tacoma WA**, with **5.19 million units produced**.  
    Source: **03_financial_report_pacific_mfg_fy2025.xlsx** (“Production by Plant” sheet)

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — Which plant has the lowest production output?
- score: 1.0 | dimensions: tier4_comparison | 11.0s
- answer:
    The plant with the lowest production output is **Greenville SC**, at **1.34 M units produced**.  
    Source: **03_financial_report_pacific_mfg_fy2025.xlsx** ("Production by Plant")

### ✅ `03_financial_report_pacific_mfg_fy2025.xlsx` — What is the gross margin percentage?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction | 6.2s
- answer:
    The gross margin percentage is **26.97%**.  
    Source: **03_financial_report_pacific_mfg_fy2025.xlsx**, sheet **“Anchors”** (“Gross margin | 26.97%”).

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — What is the account holder name on this FedEx invoice?
- score: 1.0 | dimensions: tier1_direct_lookup, no_repeat_header | 8.2s
- answer:
    The account holder name is **“Continental Distribution Co”**.  
    Source: *04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf*, page 1 (“Account holder: Continental Distribution Co”).

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — What is the invoice number?
- score: 1.0 | dimensions: tier1_direct_lookup, no_repeat_header | 7.7s
- answer:
    The invoice number is **CDC-FX-Q1-026557**.  
    Source: *04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf*

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — What is the grand total amount due on this invoice?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction, no_repeat_header | 9.0s
- answer:
    The grand total amount due is **$165,334.13**.  
    Source: **04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf** — Page 23 shows **“GRAND TOTAL $165,334.13”**, and Page 24 confirms **“Grand total due: $165,334.13.”**

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — How many FedEx Freight (Priority + Economy) shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count, no_repeat_header | 9.3s
- answer:
    There are **106 FedEx Freight shipments** on this invoice.
    
    Source: **04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf**  
    The document states: **“FedEx Freight (Priority+Economy) shipments: 106”** (page 24).  
    This also matches the service summary on page 1: **“FedEx Freight Economy 38”** and **“FedEx Freight Priority 68”**.

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — How many FedEx Ground shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count, no_repeat_header | 8.2s
- answer:
    There are **294 FedEx Ground shipments** on this invoice.
    
    Source: **04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf** — page 1 service summary shows **“FedEx Ground 294”**, and page 24 confirms **“FedEx Ground shipments: 294.”**

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — Which FedEx service tier accounts for the largest portion of charges?
- score: 1.0 | dimensions: tier4_comparison, no_repeat_header | 9.1s
- answer:
    The largest portion of charges comes from **FedEx Freight Priority**.
    
    From **04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf**:
    - “**#1 service by total charges: FedEx Freight Priority — 68 shipments, $81,935.40**” (page 24)
    - The service summary also lists “**FedEx Freight Priority ... $81,935.40**” (page 1).

### ✅ `04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf` — What percentage of the grand total is made up of fuel surcharges?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction, no_repeat_header | 10.1s
- answer:
    Fuel surcharges are **23.77%** of the grand total.
    
    From **04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf**:
    - “**Fuel surcharges**” = **$39,300.02**
    - “**GRAND TOTAL**” = **$165,334.13**

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — What is the account holder name on this FedEx invoice?
- score: 1.0 | dimensions: tier1_direct_lookup, no_repeat_header_long | 10.5s
- answer:
    The account holder name is **“Titan Systems Holdings.”**  
    Source: **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf**, page 1.

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — What is the invoice number?
- score: 1.0 | dimensions: tier1_direct_lookup, no_repeat_header_long | 12.8s
- answer:
    The invoice number is **TSH-FX-Q1-026998**.  
    Source: *05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf*

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — What is the grand total amount due on this invoice?
- score: 1.0 | dimensions: tier2_aggregation, money_extraction, no_repeat_header_long | 11.5s
- answer:
    The grand total amount due is **$583,911.98**.
    
    Source: **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf** — page 79 shows **“GRAND TOTAL $583,911.98”**, and page 80 confirms **“Grand total due: $583,911.98.”**

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — How many FedEx Ground shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count, no_repeat_header_long | 12.0s
- answer:
    There are **1,191 FedEx Ground shipments** on this invoice.
    
    Source: **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf** — page 1 service summary shows **“FedEx Ground 1,191”**, and page 80 confirms **“FedEx Ground shipments: 1,191.”**

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — How many FedEx Freight (Priority + Economy) shipments are on this invoice?
- score: 1.0 | dimensions: tier3_filter_count, no_repeat_header_long | 17.2s
- answer:
    There are **328** FedEx Freight shipments total on this invoice.
    
    Source: **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf**, page 80:  
    “**FedEx Freight (Priority+Economy) shipments: 328**”
    
    This also matches page 1 service summary:  
    - “**FedEx Freight Economy — 132**”  
    - “**FedEx Freight Priority — 196**”

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — Which FedEx service tier accounts for the largest portion of charges?
- score: 1.0 | dimensions: tier4_comparison, no_repeat_header_long | 11.3s
- answer:
    The largest portion of charges comes from **“FedEx Freight Priority”**, with **“$262,757.12”** in total charges. Source: **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf**.
    
    The document also states: **“#1 service by total charges: FedEx Freight Priority — 196 shipments, $262,757.12”** (page 80).

### ✅ `05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf` — What percentage of the grand total is made up of fuel surcharges?
- score: 1.0 | dimensions: tier5_multi_step, money_extraction, no_repeat_header_long | 15.9s
- answer:
    Fuel surcharges are **23.10%** of the grand total.
    
    From **05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf**:
    - “**Fuel surcharges $134,889.08**”
    - “**GRAND TOTAL $583,911.98**”
    
    Calculated as: 134,889.08 ÷ 583,911.98 × 100 = **23.10%**.
