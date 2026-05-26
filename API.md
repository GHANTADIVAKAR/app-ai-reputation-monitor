# API Reference

Base URL:

```text
http://localhost:3000
```

## Health

```http
GET /api/health
```

## Targets

```http
GET /api/targets
```

```http
POST /api/targets
Content-Type: application/json

{
  "name": "Vijay Deverakonda",
  "type": "celebrity",
  "description": "Telugu film actor",
  "queries": ["latest news", "movie review", "public reaction"]
}
```

## Dashboard

```http
GET /api/dashboard?targetId=target_id
```

Returns:

- selected target
- data mode, source status, and last scan time
- sentiment totals
- latest scan history
- top negative mentions
- top positive mentions
- alerts
- latest mentions with AI analysis

## Manual Scan

```http
POST /api/scan
Content-Type: application/json

{
  "targetId": "target_id"
}
```

If `targetId` is omitted, all targets are scanned.

## Mark Alert Read

```http
POST /api/alerts/read
Content-Type: application/json

{
  "alertId": "alert_id"
}
```
