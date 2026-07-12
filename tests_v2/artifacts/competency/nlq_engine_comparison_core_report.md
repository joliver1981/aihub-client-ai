# NLQ Engine Comparison — legacy vs agentic (in-process)

Generated: 2026-07-12 15:44:35
Battery: core (24 questions)   Agent: 281   Path: direct get_answer()

## Headline

| Engine | Overall | SQL hits | Ans hits | Both | Errors | p50 | mean | max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy | **95.8%** | 21/24 | 19/24 | 17/24 | 0 | 31.7s | 26.2s | 38.1s |
| agentic | **100.0%** | 22/24 | 22/24 | 20/24 | 0 | 6.1s | 6.3s | 8.7s |

## Acceptance gates (plan §7)

- agentic >= legacy overall: **PASS** (agentic 100.0% vs legacy 95.8%, delta +4.2 pts)
- agentic p50 <= 15s: **PASS** (6.1s)

## Per-question

| # | Question | legacy | agentic | l.s | a.s |
|---|---|:--:|:--:|--:|--:|
| 1 | List the distinct product categories sold in the stores. | OK | OK | 31.9 | 7.7 |
| 2 | How many distinct cities have store locations? | OK | OK | 19.5 | 4.2 |
| 3 | What product categories are available? | XX | OK | 9.8 | 6.6 |
| 4 | How many products are in the Electronics category? | OK | OK | 17.9 | 4.8 |
| 5 | How many stores are located in California? | OK | OK | 17.6 | 4.8 |
| 6 | How many employees does the company have? | OK | OK | 22.0 | 4.6 |
| 7 | What is the total sales revenue across all stores? | OK | OK | 19.1 | 4.6 |
| 8 | What is the average revenue per sale? | OK | OK | 15.8 | 4.9 |
| 9 | What is the total sales revenue by product category? | OK | OK | 35.5 | 7.3 |
| 10 | Show me total sales revenue by year. | OK | OK | 35.9 | 7.7 |
| 11 | Which store had the highest total sales revenue? Just give m | OK | OK | 35.5 | 5.6 |
| 12 | What are the top 5 products by units sold? | OK | OK | 35.8 | 8.7 |
| 13 | Which store names had at least one sale? Show distinct store | OK | OK | 34.2 | 7.4 |
| 14 | What is the total revenue per city across all stores? | OK | OK | 38.1 | 8.1 |
| 15 | Which product category has the most distinct products? | OK | OK | 32.5 | 4.8 |
| 16 | How many sales transactions happened in 2025? | OK | OK | 19.5 | 6.5 |
| 17 | What was the total revenue for sales in January 2025? | OK | OK | 17.2 | 5.2 |
| 18 | Did the Downtown Flagship store generate more revenue than t | OK | OK | 34.7 | 8.3 |
| 19 | Which is the largest store by revenue: Central Plaza, Eastga | OK | OK | 37.6 | 8.4 |
| 20 | Show me the customer churn rate for the past quarter. | OK | OK | 7.9 | 5.6 |
| 21 | What is the marketing spend by campaign last year? | OK | OK | 7.3 | 5.4 |
| 22 | Which dates this year are holidays? List the holiday name fo | OK | OK | 31.5 | 7.9 |
| 23 | Which store has the highest average daily foot traffic? | OK | OK | 37.8 | 5.9 |
| 24 | Which employee made the most sales by revenue? | OK | OK | 34.7 | 6.2 |

## legacy — audit trail

### [OK] List the distinct product categories sold in the stores.
- type=dataframe sql_ok=True ans_ok=True 31.9s
- SQL: `SELECT DISTINCT pm.category FROM TS.sales s INNER JOIN TS.product_master pm ON s.product_id = pm.product_id;`
- Answer:               category            Electronics         Home & Kitchen Beauty & Personal Care               Clothing

### [OK] How many distinct cities have store locations?
- type=dataframe sql_ok=True ans_ok=True 19.5s
- SQL: `SELECT COUNT(DISTINCT city) AS distinct_cities FROM TS.location_master;`
- Answer:  distinct_cities               15

### [XX] What product categories are available?
- type=string sql_ok=False ans_ok=False 9.8s
- Answer: Great question! I can help you explore the product categories available in our catalog.  While I don't have the specific category names listed right in front of me at this moment, I do have access to 

### [OK] How many products are in the Electronics category?
- type=dataframe sql_ok=True ans_ok=False 17.9s
- SQL: `SELECT COUNT(product_id) AS products_by_category FROM TS.product_master WHERE category = 'Electronics';`
- Answer:  products_by_category                    64

### [OK] How many stores are located in California?
- type=dataframe sql_ok=True ans_ok=False 17.6s
- SQL: `SELECT COUNT(store_id) AS number_of_stores FROM TS.location_master WHERE state = 'California';`
- Answer:  number_of_stores                 0

### [OK] How many employees does the company have?
- type=dataframe sql_ok=True ans_ok=True 22.0s
- SQL: `SELECT COUNT(employee_id) AS employee_count FROM TS.employee_data;`
- Answer:  employee_count              75

### [OK] What is the total sales revenue across all stores?
- type=dataframe sql_ok=True ans_ok=False 19.1s
- SQL: `SELECT SUM(total_revenue) AS total_sales_revenue FROM TS.sales;`
- Answer:  total_sales_revenue         1.923453e+09

### [OK] What is the average revenue per sale?
- type=dataframe sql_ok=True ans_ok=False 15.8s
- SQL: `SELECT AVG(total_revenue) AS average_revenue_per_sale FROM TS.sales;`
- Answer:  average_revenue_per_sale                807.988205

### [OK] What is the total sales revenue by product category?
- type=dataframe sql_ok=True ans_ok=True 35.5s
- SQL: `SELECT     p.category,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.product_master p     ON s.product_id = p.product_id GROUP BY p.category ORDER BY total_sales_revenu`
- Answer:               category total_sales_revenue            Electronics   $1,531,922,624.38               Clothing     $210,406,440.46         Home & Kitchen     $176,376,205.19 Beauty & Personal Care      

### [OK] Show me total sales revenue by year.
- type=dataframe sql_ok=True ans_ok=True 35.9s
- SQL: `SELECT     YEAR(sale_date) AS sale_year,     SUM(total_revenue) AS total_sales_revenue FROM TS.sales GROUP BY YEAR(sale_date) ORDER BY sale_year;`
- Answer: sale_year total_sales_revenue      2024       $4,694,653.20      2025   $1,240,216,392.21      2026     $678,542,043.86

### [OK] Which store had the highest total sales revenue? Just give me the top one.
- type=dataframe sql_ok=True ans_ok=True 35.5s
- SQL: `SELECT TOP 1     l.store_id,     l.store_name,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.location_master l ON s.store_id = l.store_id GROUP BY l.store_id, l.store_n`
- Answer: store_id    store_name total_sales_revenue     S003 Central Plaza     $255,317,634.14

### [OK] What are the top 5 products by units sold?
- type=dataframe sql_ok=True ans_ok=True 35.8s
- SQL: `SELECT TOP 5     p.product_id,     p.product_name,     SUM(s.quantity_sold) AS total_quantity_sold FROM TS.sales s INNER JOIN TS.product_master p     ON s.product_id = p.product_id GROUP BY p.product_`
- Answer: product_id       product_name  total_quantity_sold      P0016     Laptops Item 8               338560      P0112 Formal Wear Item 8               286237      P0020     Tablets Item 4               284

### [OK] Which store names had at least one sale? Show distinct store names.
- type=dataframe sql_ok=True ans_ok=True 34.2s
- SQL: `SELECT DISTINCT lm.store_name FROM TS.location_master lm INNER JOIN TS.sales s ON lm.store_id = s.store_id;`
- Answer:        store_name     Hillside Mall   Bayfront Outlet Southpoint Center     Westside Mall  Northside Outlet    Eastgate Store     Lakeside Mall    Uptown Gallery   Riverside Plaza Downtown Flagship   

### [OK] What is the total revenue per city across all stores?
- type=dataframe sql_ok=True ans_ok=True 38.1s
- SQL: `SELECT     lm.city,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.location_master lm     ON s.store_id = lm.store_id GROUP BY lm.city ORDER BY total_sales_revenue DESC;`
- Answer:          city total_sales_revenue       Chicago     $255,317,634.14  Philadelphia     $255,161,920.23      San Jose     $211,418,816.80       Phoenix     $184,513,479.26     San Diego     $150,172,825

### [OK] Which product category has the most distinct products?
- type=dataframe sql_ok=True ans_ok=True 32.5s
- SQL: `SELECT TOP 1 category, COUNT(DISTINCT product_id) AS distinct_product_count FROM TS.product_master GROUP BY category ORDER BY distinct_product_count DESC;`
- Answer:    category  distinct_product_count Electronics                      64

### [OK] How many sales transactions happened in 2025?
- type=dataframe sql_ok=True ans_ok=True 19.5s
- SQL: `SELECT COUNT(DISTINCT transaction_id) AS distinct_transactions FROM TS.sales WHERE YEAR(sale_date) = 2025;`
- Answer:  distinct_transactions                 100000

### [OK] What was the total revenue for sales in January 2025?
- type=dataframe sql_ok=True ans_ok=True 17.2s
- SQL: `SELECT SUM(total_revenue) AS total_sales_revenue FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2025-02-01';`
- Answer:  total_sales_revenue         1.089992e+08

### [OK] Did the Downtown Flagship store generate more revenue than the Westside Mall store?
- type=string sql_ok=True ans_ok=True 34.7s
- SQL: `SELECT     l.store_name,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.location_master l     ON s.store_id = l.store_id WHERE l.store_name IN ('Downtown Flagship', 'Wes`
- Answer: Yes. The Downtown Flagship store generated more revenue than the Westside Mall store. Downtown Flagship generated $108,520,727.51 in total sales revenue, while Westside Mall generated $96,119,212.14.

### [OK] Which is the largest store by revenue: Central Plaza, Eastgate Store, or Downtown Flagship?
- type=dataframe sql_ok=True ans_ok=True 37.6s
- SQL: `SELECT TOP 1     lm.store_name,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.location_master lm     ON s.store_id = lm.store_id WHERE lm.store_name IN ('Central Plaza'`
- Answer:    store_name total_sales_revenue Central Plaza     $255,317,634.14

### [OK] Show me the customer churn rate for the past quarter.
- type=string sql_ok=False ans_ok=True 7.9s
- Answer: *I can’t provide a customer churn rate because customer retention information isn’t available here. I can still help with related insights from the past quarter, such as sales trends, store traffic, c

### [OK] What is the marketing spend by campaign last year?
- type=string sql_ok=False ans_ok=True 7.3s
- Answer: *I can’t provide marketing spend by campaign because that information isn’t available here. You can still ask about related business metrics such as sales trends, product performance, store traffic, i

### [OK] Which dates this year are holidays? List the holiday name for each.
- type=dataframe sql_ok=True ans_ok=True 31.5s
- SQL: `SELECT date, holiday_name FROM TS.calendar_master WHERE is_holiday = 'Yes'   AND gregorian_year = YEAR(GETDATE()) ORDER BY date;`
- Answer:       date           holiday_name 2026-01-01               New Year 2026-01-19 Martin Luther King Day 2026-02-16         Presidents Day 2026-05-25           Memorial Day 2026-06-19             Junetee

### [OK] Which store has the highest average daily foot traffic?
- type=dataframe sql_ok=True ans_ok=True 37.8s
- SQL: `SELECT TOP 1     st.store_id,     lm.store_name,     AVG(CAST(st.foot_traffic_count AS FLOAT)) AS avg_daily_foot_traffic FROM TS.store_traffic st INNER JOIN TS.location_master lm     ON st.store_id = `
- Answer: store_id     store_name  avg_daily_foot_traffic     S008 Uptown Gallery                     630

### [OK] Which employee made the most sales by revenue?
- type=dataframe sql_ok=True ans_ok=True 34.7s
- SQL: `SELECT TOP 1     e.employee_id,     e.employee_name,     SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.employee_data e     ON s.employee_id = e.employee_id GROUP BY e.emplo`
- Answer: employee_id   employee_name total_sales_revenue       E0022 William Sanchez      $44,159,634.94


## agentic — audit trail

### [OK] List the distinct product categories sold in the stores.
- type=dataframe sql_ok=True ans_ok=True 7.7s
- SQL: `SELECT DISTINCT TOP 10000 pm.category FROM TS.sales AS s INNER JOIN TS.product_master AS pm ON s.product_id = pm.product_id ORDER BY pm.category`
- Answer:               category Beauty & Personal Care               Clothing            Electronics         Home & Kitchen

### [OK] How many distinct cities have store locations?
- type=string sql_ok=True ans_ok=True 4.2s
- SQL: `SELECT TOP 10000 COUNT(DISTINCT city) AS distinct_city_count FROM TS.location_master`
- Answer: There are 15 distinct cities with store locations.

### [OK] What product categories are available?
- type=dataframe sql_ok=True ans_ok=True 6.6s
- SQL: `SELECT DISTINCT TOP 10000 category FROM TS.product_master ORDER BY category`
- Answer:               category Beauty & Personal Care               Clothing            Electronics         Home & Kitchen

### [OK] How many products are in the Electronics category?
- type=string sql_ok=True ans_ok=True 4.8s
- SQL: `SELECT TOP 10000 COUNT(*) AS electronics_product_count FROM TS.product_master WHERE category = 'Electronics'`
- Answer: There are 64 products in the Electronics category.

### [OK] How many stores are located in California?
- type=string sql_ok=True ans_ok=True 4.8s
- SQL: `SELECT TOP 10000 COUNT(DISTINCT store_id) AS california_store_count FROM TS.location_master WHERE state = 'California'`
- Answer: There are 0 stores located in California.

### [OK] How many employees does the company have?
- type=string sql_ok=True ans_ok=True 4.6s
- SQL: `SELECT TOP 10000 COUNT(*) AS employee_count FROM TS.employee_data`
- Answer: The company currently has 75 employees.

### [OK] What is the total sales revenue across all stores?
- type=string sql_ok=True ans_ok=False 4.6s
- SQL: `SELECT TOP 10000 SUM(total_revenue) AS total_sales_revenue FROM TS.sales`
- Answer: The total sales revenue across all stores is approximately 1.92 billion.

### [OK] What is the average revenue per sale?
- type=string sql_ok=True ans_ok=False 4.9s
- SQL: `SELECT TOP 10000 AVG(CAST(total_revenue AS FLOAT)) AS average_revenue_per_sale FROM TS.sales`
- Answer: The average revenue per sale is 807.99.

### [OK] What is the total sales revenue by product category?
- type=dataframe sql_ok=True ans_ok=True 7.3s
- SQL: `SELECT TOP 10000 pm.category, SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales AS s INNER JOIN TS.product_master AS pm ON s.product_id = pm.product_id GROUP BY pm.category ORDER BY total_sale`
- Answer:               category total_sales_revenue            Electronics   $1,531,922,624.38               Clothing     $210,406,440.46         Home & Kitchen     $176,376,205.19 Beauty & Personal Care      

### [OK] Show me total sales revenue by year.
- type=dataframe sql_ok=True ans_ok=True 7.7s
- SQL: `SELECT TOP 10000 YEAR(sale_date) AS sales_year, SUM(total_revenue) AS total_sales_revenue FROM TS.sales GROUP BY YEAR(sale_date) ORDER BY sales_year`
- Answer:  sales_year total_sales_revenue        2024       $4,694,653.20        2025   $1,240,216,392.21        2026     $678,542,043.86

### [OK] Which store had the highest total sales revenue? Just give me the top one.
- type=string sql_ok=True ans_ok=True 5.6s
- SQL: `SELECT TOP 1 lm.store_name, SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.location_master lm ON s.store_id = lm.store_id GROUP BY lm.store_name ORDER BY total_sales_revenue`
- Answer: The store with the highest total sales revenue is Central Plaza, with total sales revenue of approximately 255.3 million.

### [OK] What are the top 5 products by units sold?
- type=dataframe sql_ok=True ans_ok=True 8.7s
- SQL: `SELECT TOP 5     s.product_id,     p.product_name,     SUM(s.quantity_sold) AS total_units_sold FROM TS.sales s INNER JOIN TS.product_master p     ON s.product_id = p.product_id GROUP BY s.product_id,`
- Answer: product_id       product_name  total_units_sold      P0016     Laptops Item 8            338560      P0112 Formal Wear Item 8            286237      P0020     Tablets Item 4            284494      P01

### [OK] Which store names had at least one sale? Show distinct store names.
- type=dataframe sql_ok=True ans_ok=True 7.4s
- SQL: `SELECT DISTINCT TOP 10000 lm.store_name FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id ORDER BY lm.store_name`
- Answer:        store_name   Bayfront Outlet     Capital Store     Central Plaza Downtown Flagship    Eastgate Store     Hillside Mall     Lakeside Mall      Metro Center    Midtown Market  Northside Outlet   

### [OK] What is the total revenue per city across all stores?
- type=dataframe sql_ok=True ans_ok=True 8.1s
- SQL: `SELECT TOP 10000 lm.city, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id GROUP BY lm.city ORDER BY total_revenue DESC`
- Answer:          city   total_revenue       Chicago $255,317,634.14  Philadelphia $255,161,920.23      San Jose $211,418,816.80       Phoenix $184,513,479.26     San Diego $150,172,825.68 San Francisco $133,7

### [OK] Which product category has the most distinct products?
- type=string sql_ok=True ans_ok=True 4.8s
- SQL: `SELECT TOP 1     category,     COUNT(DISTINCT product_id) AS distinct_product_count FROM TS.product_master GROUP BY category ORDER BY distinct_product_count DESC;`
- Answer: The product category with the most distinct products is Electronics, with 64 unique products.

### [OK] How many sales transactions happened in 2025?
- type=string sql_ok=True ans_ok=True 6.5s
- SQL: `SELECT TOP 10000 COUNT(DISTINCT transaction_id) AS sales_transactions_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2026-01-01'`
- Answer: There were 100,000 sales transactions recorded in 2025.

### [OK] What was the total revenue for sales in January 2025?
- type=string sql_ok=True ans_ok=True 5.2s
- SQL: `SELECT TOP 10000 SUM(total_revenue) AS total_revenue_january_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2025-02-01'`
- Answer: The total revenue for sales in January 2025 was 108,999,200.

### [OK] Did the Downtown Flagship store generate more revenue than the Westside Mall store?
- type=dataframe sql_ok=True ans_ok=True 8.3s
- SQL: `SELECT TOP 10000 lm.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id WHERE lm.store_name IN ('Downtown Flagship', 'W`
- Answer:        store_name   total_revenue     Westside Mall  $96,119,212.14 Downtown Flagship $108,520,727.51

### [OK] Which is the largest store by revenue: Central Plaza, Eastgate Store, or Downtown Flagship?
- type=string sql_ok=True ans_ok=True 8.4s
- SQL: `SELECT TOP 10000 lm.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id WHERE lm.store_name IN ('Central Plaza', 'Eastg`
- Answer: Central Plaza is the largest store by revenue among the three stores listed.  Total revenue: - Central Plaza: 255.3 million - Downtown Flagship: 108.5 million - Eastgate Store: 82.9 million

### [OK] Show me the customer churn rate for the past quarter.
- type=string sql_ok=False ans_ok=True 5.6s
- Answer: How would you like to define customer churn rate in this data? The available tables include sales transactions and store traffic, but there is no explicit customer account or subscription table. For e

### [OK] What is the marketing spend by campaign last year?
- type=string sql_ok=False ans_ok=True 5.4s
- Answer: I can’t answer that from the available data because there isn’t a marketing or campaign spend table in the current database catalog. The available tables cover sales, products, inventory, employees, s

### [OK] Which dates this year are holidays? List the holiday name for each.
- type=dataframe sql_ok=True ans_ok=True 7.9s
- SQL: `SELECT TOP 10000 [date], holiday_name FROM TS.calendar_master WHERE gregorian_year = YEAR(GETDATE()) AND is_holiday = 'Yes' ORDER BY [date]`
- Answer:       date           holiday_name 2026-01-01               New Year 2026-01-19 Martin Luther King Day 2026-02-16         Presidents Day 2026-05-25           Memorial Day 2026-06-19             Junetee

### [OK] Which store has the highest average daily foot traffic?
- type=string sql_ok=True ans_ok=True 5.9s
- SQL: `SELECT TOP 1 st.store_id,        lm.store_name,        AVG(CAST(st.foot_traffic_count AS FLOAT)) AS avg_daily_foot_traffic FROM TS.store_traffic st LEFT JOIN TS.location_master lm     ON st.store_id =`
- Answer: The store with the highest average daily foot traffic is S008 — Uptown Gallery, with an average of 630.32 visitors per day.

### [OK] Which employee made the most sales by revenue?
- type=string sql_ok=True ans_ok=True 6.2s
- SQL: `SELECT TOP 1 e.employee_id, e.employee_name, SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales s INNER JOIN TS.employee_data e ON s.employee_id = e.employee_id GROUP BY e.employee_id, e.employ`
- Answer: The employee with the highest sales revenue is William Sanchez (Employee ID: E0022), with total sales revenue of approximately $44.16 million.
