# Regression Run — <YYYY-MM-DD>

> Copy this file to `TEST_RUN_<YYYY-MM-DD>.md` and fill it in as you go. Score each check
> **✅ / ⚠️ / ❌ / N-A** with a one-line evidence note (value seen, file path, screenshot name).

- **Tester:** <name / agent>
- **Build under test:** `git rev-parse --short HEAD` → `________`
- **Method:** driven through the real UI (main app `http://localhost:5001`, Command Center
  `http://localhost:5091`) as a human. `CC_AGENT=________`.
- **Prereqs (§00):** services up ▢ · AIRDB conn id `____` ▢ · Data Assistant `REG-Data-AIRDB` ▢ ·
  SFTP server + `AUTODEMO_SFTP` ▢ · `C:\temp\aihub_test\` ▢ · fixtures present ▢

---

## Verdict

> **PASS** / **PASS-with-issues** / **FAIL**  — <one-line summary>

- Release-blockers hit (list, or "none"): ________
- Sections skipped / N/A (and why): ________

---

## Section results

| § | Feature | Result | Notes |
|---|---------|--------|-------|
| 01 | All pages open | | |
| 02 | General agent chat | | |
| 03 | Data Explorer chat | | |
| 04 | Document processing (PDF) | | |
| 05 | Agent knowledge upload | | |
| 06 | Workflow execution | | |
| 07 | Command Center automation | | |
| 08 | Artifacts (CC + agents) | | |
| 09 | Portal Workflows | | |
| 10 | Extras smoke | | |

## Release-blocker checklist (any one ❌ = do not ship)

- [ ] No core page 500s / blank-shells (§01 A2/A3/A5/A8/A9/B3/B10)
- [ ] No chat/data/doc answer **confidently wrong** vs `_ANSWER_KEY.md` (§02 A2/A3, §03 A5, §04 A1–A4, §05 B, §10 B)
- [ ] No **success reported over a missing/wrong artifact** (§06 B1, §07 A3–A5/B1, §08, §09 C2)
- [ ] No **fabrication** where data is absent (§02 A4, §03 A6, §04 A5, §05 B5, §08 B3, §10 B3)
- [ ] No **security/role** regression (§07 B2 creds, §07 C1 + §10 F2 role gate)

---

## Detailed checks (fill per section)

Paste the scorecard tables from each section file here as you run them, or keep per-section notes:

### §01 All pages open
<results>

### §02 General agent chat
<results>

### §03 Data Explorer chat
<results>

### §04 Document processing
<results>

### §05 Agent knowledge upload
<results>

### §06 Workflow execution
<results>

### §07 Command Center automation
<results>

### §08 Artifacts
<results>

### §09 Portal Workflows
<results>

### §10 Extras smoke
<results>

---

## Defects filed

| Check id | Severity | Summary | ai-colab task |
|---|---|---|---|
| e.g. REG-06-B1 | | | AIHUB-____ |

## Artifacts left on the system (cleanup notes)

- Workflows/automations created: `REG-headcount`, `reg-expense-audit`, `REG-Portal-Test`, …
- Agents created: `REG-Knowledge`, `REG-Data-AIRDB`, `REG-Agent-Temp`, …
- Files: `C:\temp\aihub_test\reg_headcount.xlsx`, SFTP `/outgoing/reg_expense_audit.csv`, …
