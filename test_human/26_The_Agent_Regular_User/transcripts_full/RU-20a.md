# RU-20a (retest)

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 10.6

## prompt

Remember how I like sales summaries: always by store, with a week-over-week column. Save that as a skill named alex-sales-style in my private scope.

## tools

- **mcp__aihub__save_skill**
  - input: `{'name': 'alex-sales-style', 'description': 'Use when presenting sales analysis or summaries', 'content': '## Sales Summary Format\n\nAlways organize sales data and summaries by store (one row per store, not by time or product).\n\nInclude a **week-over-week (WoW)** change column showing the percent or absolute change from the prior week.\n\nExample structure:\n| Store | This Week | Last Week | Wo`
  - result: ``

## reply

Done. I've saved **alex-sales-style** to your private skills scope. From now on, whenever I present a sales analysis, I'll organize it by store with a week-over-week column — every future conversation will load this preference automatically. No more hunting for the format you like.
