# Sync API

## Parameters

| param | type | required |
|---|---|---|
| source_id | string | yes |
| target_id | string | yes |
| dry_run | boolean | no |

## Example

```bash
curl -X POST https://api.example.com/v1/sync \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"src_123","target_id":"tgt_456","dry_run":false}'
```