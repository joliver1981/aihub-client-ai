# Guide

## Steps

- Prepare the input
  - Gather the required fields
  - Validate the data format
- Process the data
  - Apply the transformation rules
  - Review the output for errors
- Save the result
  - Export the final file
  - Confirm the file is accessible

## Schema

| field | type |
|---|---|
| id | string |
| name | string |
| active | boolean |

## Snippets

```python
record = {
    "id": "123",
    "name": "Example",
    "active": True,
}
print(record)
```

```json
{
  "id": "123",
  "name": "Example",
  "active": true
}
```