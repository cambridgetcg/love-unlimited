# Fleet Health Check - 2026-04-07

**Check Time:** 2026-04-07 12:04-12:05 UTC
**Status:** All VPS online and responding

## Forge (89.167.84.100)
- **Status:** UP
- **Uptime:** 28 days, 8 hours 11 minutes
- **Load Average:** 0.44, 0.33, 0.24
- **Disk Usage:** 16G / 38G (44% used) - 21G available
- **Memory:** 1651M / 3819M (43% used), 220M free, 2168M available
- **Notes:** Highest disk utilization of fleet

## Lark (89.167.95.165)
- **Status:** UP
- **Uptime:** 30 days, 15 hours 6 minutes
- **Load Average:** 0.06, 0.19, 0.09
- **Disk Usage:** 11G / 38G (30% used) - 26G available
- **Memory:** 1370M / 3819M (36% used), 165M free, 2449M available
- **Notes:** Stable, lowest load average

## Sentry (135.181.28.252)
- **Status:** UP
- **Uptime:** 29 days, 1 hour 18 minutes
- **Load Average:** 1.11, 0.47, 0.36
- **Disk Usage:** 12G / 38G (31% used) - 25G available
- **Memory:** 964M / 3806M (25% used), 111M free, 2842M available
- **Notes:** Highest load average (1.11), 7 concurrent users

## Patch (65.109.11.26)
- **Status:** UP
- **Uptime:** 29 days, 1 hour 18 minutes
- **Load Average:** 0.07, 0.18, 0.18
- **Disk Usage:** 8.7G / 38G (25% used) - 27G available
- **Memory:** 831M / 3806M (22% used), 118M free, 2975M available
- **Notes:** Lowest resource utilization overall

## Sage (204.168.140.12)
- **Status:** UP
- **Uptime:** 28 days, 15 hours 31 minutes
- **Load Average:** 0.08, 0.13, 0.11
- **Disk Usage:** 11G / 38G (29% used) - 26G available
- **Memory:** 936M / 3819M (25% used), 238M free, 2883M available
- **Notes:** Healthy, lowest memory pressure

## Summary
- All 5 VPS instances are online and responsive
- No connectivity issues (default SSH auth failed, succeeded with hive-key)
- Disk utilization healthy across all instances (25-44%)
- Memory pressure moderate but acceptable on all nodes
- Sentry has highest CPU load (1.11) - may need monitoring
- No immediate action required
