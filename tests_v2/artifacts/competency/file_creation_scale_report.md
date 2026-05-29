# File-Creation Tools — Scale / Capacity Report

Generated: 2026-05-29 13:36:58
Model: `gpt-5.4-mini`  temp=0.1  chat-timeout=630s

Verdict key: **OK** = delivered ≥90% of requested; **UNDER** = produced a file but far fewer items than asked; **INVALID** = file corrupt/unparseable; **TIMEOUT** = no response within timeout; **NO_ARTIFACT** = 200 but no file.

| Test | Format | Requested | Actual | Valid | Elapsed | Size | Verdict | Note |
|---|---|---|---:|:--:|---:|---:|:--:|---|
| CSV-50 | csv | 50 rows | 50 | ✅ | 28.4s | 2.4 KB | **OK** | 50/50 rows |
| CSV-100 | csv | 100 rows | 100 | ✅ | 55.5s | 4.9 KB | **OK** | 100/100 rows |
| CSV-250 | csv | 250 rows | 250 | ✅ | 132.5s | 11.4 KB | **OK** | 250/250 rows |
| CSV-500 | csv | 500 rows | 500 | ✅ | 281.6s | 22.1 KB | **OK** | 500/500 rows |
| CSV-1000 | csv | 1000 rows | 0 | — | 242.6s | — | **NO_ARTIFACT** | no file produced |
| XLSX-3x50 | excel | 3 sheets x 50 rows = 150 | 150 | ✅ | 33.1s | 8.9 KB | **OK** | 3 sheets, 150/150 rows |
| XLSX-5x100 | excel | 5 sheets x 100 rows = 500 | 500 | ✅ | 102.8s | 16.3 KB | **OK** | 5 sheets, 500/500 rows |
| XLSX-10x100 | excel | 10 sheets x 100 rows = 1000 | 1000 | ✅ | 260.5s | 28.5 KB | **OK** | 10 sheets, 1000/1000 rows |
| DOCX-5sec | word | 5 sections | 5 | ✅ | 8.4s | 36.1 KB | **OK** | 5/5 sections |
| DOCX-15sec | word | 15 sections | 15 | ✅ | 17.2s | 36.8 KB | **OK** | 15/15 sections |
| DOCX-30sec | word | 30 sections | 30 | ✅ | 22.7s | 36.9 KB | **OK** | 30/30 sections |