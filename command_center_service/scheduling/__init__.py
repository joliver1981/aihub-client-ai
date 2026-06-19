"""Command Center self-scheduling: per-user local store + scheduler-API logic.

CC-native and isolated from the existing Jobs/QuickJob subsystem. The recurring schedule
itself lives in the shared scheduler engine (ScheduledJobs/ScheduleDefinitions, JobType
'command_center'); this package only owns (a) per-user task/result metadata in a local JSON
store and (b) the thin logic that creates/lists/cancels those schedules.
"""
