# Data Assistant — NL→SQL Competency Report

Generated: 2026-08-26 21:16:32
Target agent: id=281

## Headline

- **Overall score: 95.8%** (23.0 / 24.0)
- Questions asked: **24**
- SQL pattern matched: **21 / 24**
- Answer pattern matched: **22 / 24**
- Both matched (strongest signal): **20 / 24**

Each question is scored 1.0 if EITHER the generated SQL matches expected patterns (correct query shape) OR the answer text matches expected ground-truth values. The dual-signal approach tolerates DB execution hiccups while still catching cases where the agent fabricated a number with no underlying query.

## Per-dimension competency

| Dimension | Questions | Score | SQL hits | Ans hits |
|---|---:|---:|---:|---:|
| `order_by_top_n` | 5 | **80.0%** | 4/5 | 4/5 |
| `join_2` | 6 | **83.3%** | 5/6 | 5/6 |
| `aggregate_sum` | 7 | **85.7%** | 6/7 | 5/7 |
| `simple_select` | 2 | **100.0%** | 2/2 | 2/2 |
| `distinct_count` | 3 | **100.0%** | 3/3 | 3/3 |
| `count` | 4 | **100.0%** | 4/4 | 4/4 |
| `where_filter` | 6 | **100.0%** | 6/6 | 6/6 |
| `aggregate_avg` | 1 | **100.0%** | 1/1 | 1/1 |
| `group_by` | 6 | **100.0%** | 6/6 | 6/6 |
| `date_filter` | 4 | **100.0%** | 4/4 | 4/4 |
| `join_3` | 1 | **100.0%** | 1/1 | 1/1 |
| `comparison` | 3 | **100.0%** | 3/3 | 3/3 |
| `not_present` | 2 | **100.0%** | 0/2 | 2/2 |

## Failed questions

### ❌ Which store had the highest total sales revenue? Just give me the top one.
- Dimensions: order_by_top_n, join_2, aggregate_sum
- Elapsed: 5.0s   status=200
- Generated SQL:
    === Data Query ===
    SELECT TOP (1)
           l.store_name AS store_name,
           SUM(s.total_revenue) AS total_sales_revenue
    FROM TS.sales AS s
    INNER JOIN TS.location_master AS l
        ON l.store_id = s.store_id
    GROUP BY l.store_name
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    Southpoint Center had the highest total sales revenue: $414.93 million.


## All Q&A (audit trail)

### ✅✅ List the distinct product categories sold in the stores.
- score: 1.0 | dimensions: simple_select, distinct_count | 7.9s
- SQL (183 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 pm.category AS product_category FROM TS.sales AS s INNER JOIN TS.product_master AS pm ON s.product_id = pm.product_id ORDER BY pm.category
- Answer:
    {"schema":{"fields":[{"name":"product_category","type":"string"}],"pandas_version":"1.4.0"},"data":[{"product_category":"Beauty & Personal Care"},{"product_category":"Clothing"},{"product_category":"Electronics"},{"product_category":"Home & Kitchen"}]}

### ✅✅ How many distinct cities have store locations?
- score: 1.0 | dimensions: distinct_count, aggregate_sum | 4.2s
- SQL (103 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT city) AS distinct_city_count FROM TS.location_master
- Answer:
    Store locations are present in 15 distinct cities.

### ✅✅ What product categories are available?
- score: 1.0 | dimensions: simple_select | 6.3s
- SQL (94 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 category FROM TS.product_master ORDER BY category
- Answer:
    {"schema":{"fields":[{"name":"category","type":"string"}],"pandas_version":"1.4.0"},"data":[{"category":"Beauty & Personal Care"},{"category":"Clothing"},{"category":"Electronics"},{"category":"Home & Kitchen"}]}

### ✅✅ How many products are in the Electronics category?
- score: 1.0 | dimensions: count, where_filter | 4.4s
- SQL (145 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT product_id) AS electronics_product_count FROM TS.product_master WHERE category = 'Electronics'
- Answer:
    There are 64 products in the Electronics category.

### ✅✅ How many stores are located in California?
- score: 1.0 | dimensions: count, where_filter | 5.6s
- SQL (129 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT store_id) AS california_store_count FROM TS.location_master WHERE state = 'CA'
- Answer:
    There are 4 stores located in California.

### ✅✅ How many employees does the company have?
- score: 1.0 | dimensions: count | 5.0s
- SQL (103 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT employee_id) AS employee_count FROM TS.employee_data
- Answer:
    The company has 75 employees.

### ✅sql What is the total sales revenue across all stores?
- score: 1.0 | dimensions: aggregate_sum | 4.9s
- SQL (91 chars):
    === Data Query ===
    SELECT TOP 10000 SUM(total_revenue) AS total_sales_revenue FROM TS.sales
- Answer:
    Total sales revenue across all stores is **$3,123,002,000**.

### ✅✅ What is the average revenue per sale?
- score: 1.0 | dimensions: aggregate_avg | 4.9s
- SQL (144 chars):
    === Data Query ===
    SELECT TOP 10000 CAST(AVG(CAST(total_revenue AS NUMERIC(18, 2))) AS NUMERIC(18, 2)) AS average_revenue_per_sale FROM TS.sales
- Answer:
    The average revenue per sale is **$788.76**.

### ✅✅ What is the total sales revenue by product category?
- score: 1.0 | dimensions: group_by, join_2, aggregate_sum | 8.8s
- SQL (253 chars):
    === Data Query ===
    SELECT TOP 10000 pm.category AS product_category, SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales AS s INNER JOIN TS.product_master AS pm ON s.product_id = pm.product_id GROUP BY pm.category ORDER BY total_sales_revenue DESC
- Answer:
    {"schema":{"fields":[{"name":"product_category","type":"string"},{"name":"total_sales_revenue","type":"string"}],"pandas_version":"1.4.0"},"data":[{"product_category":"Electronics","total_sales_revenue":"$2,485,318,466.39"},{"product_category":"Clothing","total_sales_revenue":"$342,206,045.89"},{"product_category":"Home & Kitchen","total_sales_revenue":"$287,646,056.20"},{"product_category":"Beauty & Personal Care","total_sales_revenue":"$7,831,598.47"}]}

### ✅✅ Show me total sales revenue by year.
- score: 1.0 | dimensions: group_by, date_filter, aggregate_sum | 8.8s
- SQL (167 chars):
    === Data Query ===
    SELECT TOP 10000 YEAR(sale_date) AS sales_year, SUM(total_revenue) AS total_sales_revenue FROM TS.sales GROUP BY YEAR(sale_date) ORDER BY sales_year
- Answer:
    {"schema":{"fields":[{"name":"sales_year","type":"integer"},{"name":"total_sales_revenue","type":"string"}],"pandas_version":"1.4.0"},"data":[{"sales_year":2024,"total_sales_revenue":"$1,066,975,900.14"},{"sales_year":2025,"total_sales_revenue":"$1,240,216,392.21"},{"sales_year":2026,"total_sales_revenue":"$815,809,874.60"}]}

### ❌ Which store had the highest total sales revenue? Just give me the top one.
- score: 0.0 | dimensions: order_by_top_n, join_2, aggregate_sum | 5.0s
- SQL (262 chars):
    === Data Query ===
    SELECT TOP (1)
           l.store_name AS store_name,
           SUM(s.total_revenue) AS total_sales_revenue
    FROM TS.sales AS s
    INNER JOIN TS.location_master AS l
        ON l.store_id = s.store_id
    GROUP BY l.store_name
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    Southpoint Center had the highest total sales revenue: $414.93 million.

### ✅✅ What are the top 5 products by units sold?
- score: 1.0 | dimensions: order_by_top_n, join_2 | 9.2s
- SQL (299 chars):
    === Data Query ===
    SELECT TOP 5
           p.product_id,
           p.product_name,
           SUM(s.quantity_sold) AS units_sold
    FROM TS.sales AS s
    INNER JOIN TS.product_master AS p
        ON p.product_id = s.product_id
    GROUP BY p.product_id, p.product_name
    ORDER BY SUM(s.quantity_sold) DESC, p.product_name ASC;
- Answer:
    {"schema":{"fields":[{"name":"product_id","type":"string"},{"name":"product_name","type":"string"},{"name":"units_sold","type":"integer"}],"pandas_version":"1.4.0"},"data":[{"product_id":"P0016","product_name":"Laptops Item 8","units_sold":546666},{"product_id":"P0020","product_name":"Tablets Item 4","units_sold":469093},{"product_id":"P0112","product_name":"Formal Wear Item 8","units_sold":462127},{"product_id":"P0121","product_name":"Appliances Item 1","units_sold":370115},{"product_id":"P0052","product_name":"Cameras Item 4","units_sold":335605}]}

### ✅✅ Which store names had at least one sale? Show distinct store names.
- score: 1.0 | dimensions: join_2, distinct_count | 9.1s
- SQL (178 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 lm.store_name AS store_name FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id ORDER BY lm.store_name
- Answer:
    {"schema":{"fields":[{"name":"store_name","type":"string"}],"pandas_version":"1.4.0"},"data":[{"store_name":"Bayfront Outlet"},{"store_name":"Capital Store"},{"store_name":"Central Plaza"},{"store_name":"Downtown Flagship"},{"store_name":"Eastgate Store"},{"store_name":"Hillside Mall"},{"store_name":"Lakeside Mall"},{"store_name":"Metro Center"},{"store_name":"Midtown Market"},{"store_name":"Northside Outlet"},{"store_name":"Parkview Center"},{"store_name":"Riverside Plaza"},{"store_name":"Southpoint Center"},{"store_name":"Uptown Gallery"},{"store_name":"Westside Mall"}]}

### ✅✅ What is the total revenue per city across all stores?
- score: 1.0 | dimensions: join_3, group_by, aggregate_sum | 8.4s
- SQL (214 chars):
    === Data Query ===
    SELECT TOP 10000 l.city AS city, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS l ON s.store_id = l.store_id GROUP BY l.city ORDER BY total_revenue DESC
- Answer:
    {"schema":{"fields":[{"name":"city","type":"string"},{"name":"total_revenue","type":"string"}],"pandas_version":"1.4.0"},"data":[{"city":"Philadelphia","total_revenue":"$414,929,015.41"},{"city":"Chicago","total_revenue":"$408,517,419.08"},{"city":"San Jose","total_revenue":"$346,913,619.13"},{"city":"Phoenix","total_revenue":"$292,415,897.50"},{"city":"San Diego","total_revenue":"$250,682,977.36"},{"city":"San Francisco","total_revenue":"$222,998,061.98"},{"city":"New York","total_revenue":"$176,407,812.53"},{"city":"Columbus","total_revenue":"$173,244,627.59"},{"city":"Los Angeles","total_revenue":"$159,573,207.95"},{"city":"Jacksonville","total_revenue":"$151,116,384.75"},{"city":"Austin","total_revenue":"$150,273,869.42"},{"city":"Houston","total_revenue":"$136,749,065.05"},{"city":"San Antonio","total_revenue":"$108,073,406.56"},{"city":"Charlotte","total_revenue":"$75,322,953.33"},{"city":"Dallas","total_revenue":"$55,783,849.31"}]}

### ✅✅ Which product category has the most distinct products?
- score: 1.0 | dimensions: group_by, comparison | 4.8s
- SQL (221 chars):
    === Data Query ===
    SELECT TOP (1)
        category AS product_category,
        COUNT(DISTINCT product_id) AS distinct_product_count
    FROM TS.product_master
    GROUP BY category
    ORDER BY COUNT(DISTINCT product_id) DESC, category ASC;
- Answer:
    Electronics has the most distinct products, with 64 products.

### ✅✅ How many sales transactions happened in 2025?
- score: 1.0 | dimensions: date_filter, count, where_filter | 5.7s
- SQL (168 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT transaction_id) AS sales_transactions_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2026-01-01'
- Answer:
    There were 789,731 distinct sales transactions in 2025.

### ✅✅ What was the total revenue for sales in January 2025?
- score: 1.0 | dimensions: date_filter, aggregate_sum, where_filter | 6.4s
- SQL (159 chars):
    === Data Query ===
    SELECT TOP 10000 SUM(total_revenue) AS total_revenue_january_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2025-02-01'
- Answer:
    Total sales revenue in January 2025 was **$108,999,200**.

### ✅✅ Did the Downtown Flagship store generate more revenue than the Westside Mall store?
- score: 1.0 | dimensions: comparison, where_filter | 5.5s
- SQL (279 chars):
    === Data Query ===
    SELECT TOP 10000 l.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS l ON s.store_id = l.store_id WHERE l.store_name IN ('Downtown Flagship', 'Westside Mall') GROUP BY l.store_name ORDER BY total_revenue DESC
- Answer:
    Yes. Downtown Flagship generated about $176.4 million in revenue, compared with about $159.6 million for Westside Mall—roughly $16.8 million more.

### ✅✅ Which is the largest store by revenue: Central Plaza, Eastgate Store, or Downtown Flagship?
- score: 1.0 | dimensions: comparison, order_by_top_n | 5.9s
- SQL (302 chars):
    === Data Query ===
    SELECT TOP 10000 lm.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id WHERE lm.store_name IN ('Central Plaza', 'Eastgate Store', 'Downtown Flagship') GROUP BY lm.store_name ORDER BY total_revenue DESC
- Answer:
    Central Plaza is the largest by total recorded revenue, at approximately $408.5 million. Downtown Flagship follows at $176.4 million, and Eastgate Store at $136.7 million.

### ✅ans Show me the customer churn rate for the past quarter.
- score: 1.0 | dimensions: not_present | 1.8s
- SQL: <none>
- Answer:
    I don’t see any customer or subscription tables in the available data, so I can’t calculate a churn rate from this dataset.

### ✅ans What is the marketing spend by campaign last year?
- score: 1.0 | dimensions: not_present | 3.2s
- SQL: <none>
- Answer:
    I don’t have a marketing-spend or campaign table in the available data catalog, so I can’t calculate spend by campaign for last year (2025). The available tables cover sales, products, inventory, stores, employees, pricing, plans, traffic, and calendar data.

### ✅✅ Which dates this year are holidays? List the holiday name for each.
- score: 1.0 | dimensions: where_filter, date_filter | 7.4s
- SQL (163 chars):
    === Data Query ===
    SELECT TOP 10000 [date] AS holiday_date, holiday_name FROM TS.calendar_master WHERE gregorian_year = 2026 AND is_holiday = 'Yes' ORDER BY [date]
- Answer:
    {"schema":{"fields":[{"name":"holiday_date","type":"string"},{"name":"holiday_name","type":"string"}],"pandas_version":"1.4.0"},"data":[{"holiday_date":"2026-01-01T00:00:00.000","holiday_name":"New Year"},{"holiday_date":"2026-01-19T00:00:00.000","holiday_name":"Martin Luther King Day"},{"holiday_date":"2026-02-16T00:00:00.000","holiday_name":"Presidents Day"},{"holiday_date":"2026-05-25T00:00:00.000","holiday_name":"Memorial Day"},{"holiday_date":"2026-06-19T00:00:00.000","holiday_name":"Juneteenth"},{"holiday_date":"2026-07-04T00:00:00.000","holiday_name":"Independence Day"},{"holiday_date":"2026-09-07T00:00:00.000","holiday_name":"Labor Day"},{"holiday_date":"2026-11-26T00:00:00.000","holiday_name":"Thanksgiving"},{"holiday_date":"2026-12-25T00:00:00.000","holiday_name":"Christmas"}]}

### ✅✅ Which store has the highest average daily foot traffic?
- score: 1.0 | dimensions: join_2, group_by, order_by_top_n | 6.4s
- SQL (451 chars):
    === Data Query ===
    SELECT TOP (1)
        lm.store_id,
        lm.store_name,
        lm.city,
        lm.state,
        CAST(AVG(CAST(st.foot_traffic_count AS decimal(18,2))) AS decimal(18,2)) AS average_daily_foot_traffic,
        COUNT(*) AS days_recorded
    FROM TS.store_traffic AS st
    INNER JOIN TS.location_master AS lm
        ON lm.store_id = st.store_id
    GROUP BY lm.store_id, lm.store_name, lm.city, lm.state
- Answer:
    Uptown Gallery (S008) in San Diego, CA has the highest average daily foot traffic: 630.32 visitors per recorded day, based on 31 days of data.

### ✅✅ Which employee made the most sales by revenue?
- score: 1.0 | dimensions: join_2, group_by, order_by_top_n | 6.2s
- SQL (312 chars):
    === Data Query ===
    SELECT TOP (1)
        e.employee_name AS employee_name,
        s.employee_id AS employee_id,
        SUM(s.total_revenue) AS total_revenue
    FROM TS.sales AS s
    INNER JOIN TS.employee_data AS e
        ON e.employee_id = s.employee_id
    GROUP BY e.employee_name, s.employee_id
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    William Sanchez (E0022) made the most sales by revenue, generating $71.39 million in total revenue across the available sales data.
