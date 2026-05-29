# Sync API

## Parameters

| param | type | required |
|---|---|---|
| source | string | yes |
| destination | string | yes |
| dry_run | boolean | no |

## Example

```bash
curl -X POST https://api.example.com/sync \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source":"db_a","destination":"db_b","dry_run":false}'
```