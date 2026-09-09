# RU-05d

**seat:** ru_drew  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 5.5

## prompt

What databases can I query here? Just list them.

## tools

- **mcp__aihub__list_data_connections**
  - input: `{}`
  - result: `Data connections: | - id 1 — ERPDB (type ?, db ERPDB) | - id 5 — EDW (SQL Server) (type ?, db LLMDB) | - id 18 — EDW (Postgres) (type ?, db edw) | - id 28 — EDWDB (Postgres) (type ?, db edwdb) | - id 1071 — AIRDB2 (type ?, db AIRDB2) | - id 1072 — AIRDB (type ?, db AIRDB) |  | Coverage rule: a 'no data / nothing recorded' answer covers only the connections you actually queried. When you do not know where data lives, search_tables checks every connection in one call; every query result ends with `

## reply

Here are the databases you can query:

1. **ERPDB** (id 1)
2. **EDW (SQL Server)** (id 5) — LLMDB
3. **EDW (Postgres)** (id 18) — edw
4. **EDWDB (Postgres)** (id 28) — edwdb
5. **AIRDB2** (id 1071)
6. **AIRDB** (id 1072)

You can query any of these using their names or IDs. Would you like to probe a schema or run a query?
