# Data Explorer v2 — NL→SQL Competency Report

Generated: 2026-08-26 21:19:13
Endpoint: POST /data_explorer/chat
Engine: LLMDataEngineV2
Target agent: id=281

## Headline

- **Overall score: 95.8%** (23.0 / 24.0)
- Questions asked: **24**
- SQL pattern matched: **21 / 24**
- Answer pattern matched: **22 / 24**
- Both matched: **20 / 24**

Compare with `data_assistant_competency_report.md` (legacy /chat/data path).

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
- Elapsed: 5.5s   status=200
- Generated SQL:
    === Data Query ===
    SELECT TOP (1)
        l.store_name,
        s.store_id,
        SUM(s.total_revenue) AS total_sales_revenue
    FROM TS.sales AS s
    INNER JOIN TS.location_master AS l
        ON l.store_id = s.store_id
    GROUP BY l.store_name, s.store_id
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    Southpoint Center (Store S006) had the highest total sales revenue: $414.93 million.
    {"blocks": [{"content": "Southpoint Center (Store S006) had the highest total sales revenue: $414.93 million.", "metadata": {}, "type": "text"}], "type": "rich_content"}


## All Q&A (audit trail)

### ✅✅ List the distinct product categories sold in the stores.
- score: 1.0 | dimensions: simple_select, distinct_count | 7.7s | status=200
- SQL (183 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 pm.category AS product_category FROM TS.sales AS s INNER JOIN TS.product_master AS pm ON s.product_id = pm.product_id ORDER BY pm.category
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>product_category</th>
        </tr>

### ✅✅ How many distinct cities have store locations?
- score: 1.0 | dimensions: distinct_count, aggregate_sum | 4.3s | status=200
- SQL (103 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT city) AS distinct_city_count FROM TS.location_master
- Answer:
    Store locations are present in 15 distinct cities.
    {"blocks": [{"content": "Store locations are present in 15 distinct cities.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ What product categories are available?
- score: 1.0 | dimensions: simple_select | 7.9s | status=200
- SQL (114 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 category AS product_category FROM TS.product_master ORDER BY category
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>product_category</th>
        </tr>

### ✅✅ How many products are in the Electronics category?
- score: 1.0 | dimensions: count, where_filter | 4.8s | status=200
- SQL (145 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT product_id) AS electronics_product_count FROM TS.product_master WHERE category = 'Electronics'
- Answer:
    There are 64 products in the Electronics category.
    {"blocks": [{"content": "There are 64 products in the Electronics category.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ How many stores are located in California?
- score: 1.0 | dimensions: count, where_filter | 4.8s | status=200
- SQL (113 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(*) AS california_store_count FROM TS.location_master WHERE state = 'CA'
- Answer:
    There are 4 stores located in California.
    {"blocks": [{"content": "There are 4 stores located in California.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ How many employees does the company have?
- score: 1.0 | dimensions: count | 4.6s | status=200
- SQL (103 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT employee_id) AS employee_count FROM TS.employee_data
- Answer:
    The company has 75 employees.
    {"blocks": [{"content": "The company has 75 employees.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅sql What is the total sales revenue across all stores?
- score: 1.0 | dimensions: aggregate_sum | 5.8s | status=200
- SQL (91 chars):
    === Data Query ===
    SELECT TOP 10000 SUM(total_revenue) AS total_sales_revenue FROM TS.sales
- Answer:
    Total sales revenue across all stores is **$3,123,002,000**.
    {"blocks": [{"content": "Total sales revenue across all stores is **$3,123,002,000**.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ What is the average revenue per sale?
- score: 1.0 | dimensions: aggregate_avg | 5.5s | status=200
- SQL (144 chars):
    === Data Query ===
    SELECT TOP 10000 CAST(AVG(CAST(total_revenue AS NUMERIC(18, 2))) AS NUMERIC(18, 2)) AS average_revenue_per_sale FROM TS.sales
- Answer:
    The average revenue per sale is **$788.76**.
    {"blocks": [{"content": "The average revenue per sale is **$788.76**.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ What is the total sales revenue by product category?
- score: 1.0 | dimensions: group_by, join_2, aggregate_sum | 8.3s | status=200
- SQL (249 chars):
    === Data Query ===
    SELECT TOP 10000 p.category AS product_category, SUM(s.total_revenue) AS total_sales_revenue FROM TS.sales AS s INNER JOIN TS.product_master AS p ON s.product_id = p.product_id GROUP BY p.category ORDER BY total_sales_revenue DESC
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>product_category</th>
          <th>total_sales_revenue</th>

### ✅✅ Show me total sales revenue by year.
- score: 1.0 | dimensions: group_by, date_filter, aggregate_sum | 10.3s | status=200
- SQL (167 chars):
    === Data Query ===
    SELECT TOP 10000 YEAR(sale_date) AS sales_year, SUM(total_revenue) AS total_sales_revenue FROM TS.sales GROUP BY YEAR(sale_date) ORDER BY sales_year
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>sales_year</th>
          <th>total_sales_revenue</th>

### ❌ Which store had the highest total sales revenue? Just give me the top one.
- score: 0.0 | dimensions: order_by_top_n, join_2, aggregate_sum | 5.5s | status=200
- SQL (270 chars):
    === Data Query ===
    SELECT TOP (1)
        l.store_name,
        s.store_id,
        SUM(s.total_revenue) AS total_sales_revenue
    FROM TS.sales AS s
    INNER JOIN TS.location_master AS l
        ON l.store_id = s.store_id
    GROUP BY l.store_name, s.store_id
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    Southpoint Center (Store S006) had the highest total sales revenue: $414.93 million.
    {"blocks": [{"content": "Southpoint Center (Store S006) had the highest total sales revenue: $414.93 million.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ What are the top 5 products by units sold?
- score: 1.0 | dimensions: order_by_top_n, join_2 | 8.7s | status=200
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
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>product_id</th>
          <th>product_name</th>

### ✅✅ Which store names had at least one sale? Show distinct store names.
- score: 1.0 | dimensions: join_2, distinct_count | 10.6s | status=200
- SQL (178 chars):
    === Data Query ===
    SELECT DISTINCT TOP 10000 lm.store_name AS store_name FROM TS.sales AS s INNER JOIN TS.location_master AS lm ON s.store_id = lm.store_id ORDER BY lm.store_name
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>store_name</th>
        </tr>

### ✅✅ What is the total revenue per city across all stores?
- score: 1.0 | dimensions: join_3, group_by, aggregate_sum | 7.4s | status=200
- SQL (214 chars):
    === Data Query ===
    SELECT TOP 10000 l.city AS city, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS l ON s.store_id = l.store_id GROUP BY l.city ORDER BY total_revenue DESC
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>city</th>
          <th>total_revenue</th>

### ✅✅ Which product category has the most distinct products?
- score: 1.0 | dimensions: group_by, comparison | 4.7s | status=200
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
    {"blocks": [{"content": "Electronics has the most distinct products, with 64 products.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ How many sales transactions happened in 2025?
- score: 1.0 | dimensions: date_filter, count, where_filter | 4.9s | status=200
- SQL (168 chars):
    === Data Query ===
    SELECT TOP 10000 COUNT(DISTINCT transaction_id) AS sales_transactions_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2026-01-01'
- Answer:
    There were 789,731 distinct sales transactions in 2025.
    {"blocks": [{"content": "There were 789,731 distinct sales transactions in 2025.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ What was the total revenue for sales in January 2025?
- score: 1.0 | dimensions: date_filter, aggregate_sum, where_filter | 5.4s | status=200
- SQL (159 chars):
    === Data Query ===
    SELECT TOP 10000 SUM(total_revenue) AS total_revenue_january_2025 FROM TS.sales WHERE sale_date >= '2025-01-01' AND sale_date < '2025-02-01'
- Answer:
    Total sales revenue in January 2025 was **$108,999,200**.
    {"blocks": [{"content": "Total sales revenue in January 2025 was **$108,999,200**.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ Did the Downtown Flagship store generate more revenue than the Westside Mall store?
- score: 1.0 | dimensions: comparison, where_filter | 5.7s | status=200
- SQL (279 chars):
    === Data Query ===
    SELECT TOP 10000 l.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS l ON s.store_id = l.store_id WHERE l.store_name IN ('Downtown Flagship', 'Westside Mall') GROUP BY l.store_name ORDER BY total_revenue DESC
- Answer:
    Yes. Downtown Flagship generated approximately $176.4M in revenue, compared with about $159.6M for Westside Mall—a difference of roughly $16.8M in favor of Downtown Flagship.
    {"blocks": [{"content": "Yes. Downtown Flagship generated approximately $176.4M in revenue, compared with about $159.6M for Westside Mall\u2014a difference of roughly $16.8M in favor of Downtown Flagship.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ Which is the largest store by revenue: Central Plaza, Eastgate Store, or Downtown Flagship?
- score: 1.0 | dimensions: comparison, order_by_top_n | 5.5s | status=200
- SQL (297 chars):
    === Data Query ===
    SELECT TOP 10000 l.store_name, SUM(s.total_revenue) AS total_revenue FROM TS.sales AS s INNER JOIN TS.location_master AS l ON s.store_id = l.store_id WHERE l.store_name IN ('Central Plaza', 'Eastgate Store', 'Downtown Flagship') GROUP BY l.store_name ORDER BY total_revenue DESC
- Answer:
    Central Plaza is the largest of the three, with total revenue of approximately $408.5 million. Downtown Flagship generated about $176.4 million, and Eastgate Store about $136.7 million.
    {"blocks": [{"content": "Central Plaza is the largest of the three, with total revenue of approximately $408.5 million. Downtown Flagship generated about $176.4 million, and Eastgate Store about $136.7 million.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅ans Show me the customer churn rate for the past quarter.
- score: 1.0 | dimensions: not_present | 1.8s | status=200
- SQL: <none>
- Answer:
    Which data source or definition should I use for customer churn? The available tables contain sales and store activity, but no customer records or churn-status field.
    {"blocks": [{"content": "Which data source or definition should I use for customer churn? The available tables contain sales and store activity, but no customer records or churn-status field.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅ans What is the marketing spend by campaign last year?
- score: 1.0 | dimensions: not_present | 3.3s | status=200
- SQL: <none>
- Answer:
    I don’t see a marketing-spend or campaign table in the available data—only sales, inventory, products, stores, pricing, staffing, planning, and traffic. Please connect or provide the marketing campaign spend data, and I can break out last year’s spend by campaign.
    {"blocks": [{"content": "I don\u2019t see a marketing-spend or campaign table in the available data\u2014only sales, inventory, products, stores, pricing, staffing, planning, and traffic. Please connect or provide the marketing campaign spend data, and I can break out last year\u2019s spend by campaign.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ Which dates this year are holidays? List the holiday name for each.
- score: 1.0 | dimensions: where_filter, date_filter | 7.5s | status=200
- SQL (163 chars):
    === Data Query ===
    SELECT TOP 10000 [date] AS holiday_date, holiday_name FROM TS.calendar_master WHERE gregorian_year = 2026 AND is_holiday = 'Yes' ORDER BY [date]
- Answer:
    <table border="1" class="dataframe">
      <thead>
        <tr style="text-align: right;">
          <th></th>
          <th>holiday_date</th>
          <th>holiday_name</th>

### ✅✅ Which store has the highest average daily foot traffic?
- score: 1.0 | dimensions: join_2, group_by, order_by_top_n | 6.6s | status=200
- SQL (451 chars):
    === Data Query ===
    SELECT TOP (1)
        st.store_id,
        lm.store_name,
        lm.city,
        lm.state,
        CAST(AVG(CAST(st.foot_traffic_count AS decimal(18,2))) AS decimal(18,2)) AS average_daily_foot_traffic,
        COUNT(*) AS days_recorded
    FROM TS.store_traffic AS st
    INNER JOIN TS.location_master AS lm
        ON lm.store_id = st.store_id
    GROUP BY st.store_id, lm.store_name, lm.city, lm.state
- Answer:
    Uptown Gallery (S008) in San Diego, CA has the highest average daily foot traffic: 630.32 visitors per day across 31 recorded days.
    {"blocks": [{"content": "Uptown Gallery (S008) in San Diego, CA has the highest average daily foot traffic: 630.32 visitors per day across 31 recorded days.", "metadata": {}, "type": "text"}], "type": "rich_content"}

### ✅✅ Which employee made the most sales by revenue?
- score: 1.0 | dimensions: join_2, group_by, order_by_top_n | 4.7s | status=200
- SQL (295 chars):
    === Data Query ===
    SELECT TOP 1
        e.employee_name AS employee_name,
        s.employee_id,
        SUM(s.total_revenue) AS total_revenue
    FROM TS.sales AS s
    INNER JOIN TS.employee_data AS e
        ON e.employee_id = s.employee_id
    GROUP BY e.employee_name, s.employee_id
    ORDER BY SUM(s.total_revenue) DESC;
- Answer:
    William Sanchez (E0022) made the most sales by revenue, totaling approximately $71.39 million.
    {"blocks": [{"content": "William Sanchez (E0022) made the most sales by revenue, totaling approximately $71.39 million.", "metadata": {}, "type": "text"}], "type": "rich_content"}
