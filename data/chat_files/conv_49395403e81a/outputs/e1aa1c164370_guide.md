# Guide

## Steps

- Prepare the input
  - Validate required fields
  - Normalize values
- Process the data
  - Apply transformation rules
  - Capture any errors
- Produce the output
  - Save the final result
  - Verify the generated artifact

## Schema

| field | type |
|---|---|
| id | string |
| name | string |
| active | boolean |
| created_at | datetime |

## Snippets

```python
from datetime import datetime

record = {
    "id": "A-100",
    "name": "Example",
    "active": True,
    "created_at": datetime.utcnow().isoformat()
}

print(record)
```

```json
{
  "id": "A-100",
  "name": "Example",
  "active": true,
  "created_at": "2026-05-29T00:00:00Z"
}
```