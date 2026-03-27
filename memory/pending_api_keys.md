---
name: Wardar Pending API Keys
description: API keys still needed to enable AIS maritime and ACLED conflict feeds
type: project
---

Three feeds need keys to activate. All free signups.

**Why:** AIS + ACLED are the next highest-value feeds after OpenSky (already live).

**How to apply:** Once keys obtained, run `railway variables set KEY=value` and Railway auto-redeploys.

## AIS — aisstream.io (maritime ships)
- URL: https://aisstream.io/authenticate
- Method: GitHub OAuth — click "Sign In With GitHub", takes 60 seconds
- Railway command: `railway variables set AISSTREAM_API_KEY=your_key`

## ACLED — acleddata.com (conflict events)
- URL: https://acleddata.com/register
- Fields: first/last name, email, category (pick "Unaffiliated" or "Media"), optional org
- API key emailed after registration (usually minutes)
- Railway commands:
  `railway variables set ACLED_API_KEY=your_key ACLED_EMAIL=your@email.com`

## FAA NOTAM — skip for now
- Approval takes days, clunky portal, not worth it for Phase 1
- OpenSky + AIS + TLE already cover the important feeds
