# HEARTBEAT.md — Gamma's 7-minute cycle

## 0. HIVE Check (ALWAYS FIRST)

```bash
python3 ~/love-unlimited/hive/hive.py check
```

## 1. Zerone Health

- Check devnet status if running
- Review test suite status
- Any failing tests to fix?

## 2. Build Engine

- What's the current active build?
- Pick one unit of work, execute it
- Commit with clear message

## 3. SOMA Technical

- Any firmware issues?
- Sensor readings normal?
- Build progress on current phase?

## 4. Check Coordination

```bash
python3 ~/love-unlimited/tools/stigmergy.py check
python3 ~/love-unlimited/tools/cognitive/council.py check
python3 ~/love-unlimited/tools/cognitive/joinmind.py sync
```

## 5. MLX Local Model

```bash
python3 ~/love-unlimited/tools/mlx_serve.py status
```

## Otherwise: HEARTBEAT_OK
