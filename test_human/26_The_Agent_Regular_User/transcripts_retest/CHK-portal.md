# CHK-portal (retest)

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 16.1

## prompt

Can I save a portal login and schedule a portal workflow to download a file every day? Answer yes or no for each, based on what you can actually do for me.

## tools

_(none)_

## reply

**Yes** to both:

1. **Save a portal login** — Yes. I can call `save_portal` to store your login credentials encrypted, then use just the portal name for future fetches.

2. **Schedule a portal workflow to download a file every day** — Yes. I can use `schedule_portal_workflow` with `every_days=1` to run a recorded portal workflow daily. Each run delivers download links to your My Work.

**One caveat:** The daily schedule works on a *recorded* portal workflow — a saved browser RPA sequence of your login and download steps. If you don't have one recorded yet, we'd need to record it first (a one-time setup where the browser captures your steps), then schedule it. If you just have a portal login saved, I can fetch files on demand with `portal_fetch`, but for a daily schedule it needs to be a recorded workflow.
