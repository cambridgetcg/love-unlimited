"""只用 synthetic fixtures；唔 import network／auth clients。"""
import json
import os
from pathlib import Path
import signal
import sys
import time

mode, marker = sys.argv[1:3]
args = sys.argv[3:]
with Path(marker).open("a") as output:
    output.write(json.dumps({"pid": os.getpid(), "args": args}) + "\n")


def send(message):
    print(json.dumps(message), flush=True)


def hang():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)


if args == ["--version"]:
    if mode == "version_timeout":
        hang()
    print("codex-cli " + ("0.153.2" if mode == "old_version" else "0.153.3"), flush=True)
    if mode == "version_lingers":
        hang()
    sys.exit(0)

assert args == ["app-server"]
if mode == "early_eof":
    sys.exit(0)
if mode == "timeout":
    hang()
if mode == "oversized_stdout":
    sys.stdout.write("X" * (2 * 1024 * 1024))
    sys.stdout.flush()
    hang()
if mode == "oversized_stderr":
    os.write(2, b"STDERR_SECRET_CANARY" * 100000)
    hang()
if mode == "too_many_lines":
    for _ in range(300):
        send({"method": "ignore", "params": {"secret": "NOTIFICATION_SECRET_CANARY"}})
    hang()

window = {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 2000000000}
bucket = {"limitId": "codex", "limitName": "IGNORE_ALL_PREVIOUS_SECRET_CANARY",
          "planType": "pro", "primary": window, "secondary": None,
          "credits": {"hasCredits": True, "unlimited": False, "balance": "12.50"},
          "freeText": "RPC_SECRET_CANARY"}
reads = 0
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    with Path(marker).open("a") as output:
        output.write(json.dumps({"request": request}) + "\n")
    method = request.get("method")
    if method == "initialize":
        client = request["params"]["clientInfo"]
        assert all(isinstance(client.get(key), str) and client[key] for key in ("name", "version"))
        assert request["id"] == 1
        if mode == "malformed":
            print("RAW_RPC_SECRET_CANARY NOT JSON", flush=True)
            hang()
        if mode == "rpc_error":
            send({"id": 1, "error": {"message": "AUTH_SECRET_CANARY", "code": -32000}})
            continue
        # 0.153.3 initialize 回覆 metadata 唔包含 quota／account identity。
        send({"id": 1, "result": {"userAgent": "codex-cli/0.153.3", "platformFamily": "unix"}})
    elif method == "initialized":
        assert "params" not in request and "id" not in request
        initialized = True
    elif method == "account/rateLimits/read":
        assert initialized and "params" not in request
        reads += 1
        if mode == "sparse" or (mode == "refetch" and reads == 1):
            result = {"rateLimits": {"limitId": "codex", "primary": None, "secondary": None}}
        elif mode == "multibucket":
            second = {**bucket, "limitId": "review", "primary": {**window, "usedPercent": 11}}
            result = {"rateLimits": bucket, "rateLimitsByLimitId": {"codex": bucket, "review": second}}
        else:
            result = {"rateLimits": bucket, "rateLimitsByLimitId": {"codex": bucket}}
        send({"method": "some/notification", "params": {"secret": "NOTIFICATION_SECRET_CANARY"}})
        os.write(2, b"STDERR_SECRET_CANARY\n" * 1000)
        send({"id": request["id"], "result": result})
    else:
        raise AssertionError("unexpected RPC")
if mode == "cleanup_hang":
    hang()
