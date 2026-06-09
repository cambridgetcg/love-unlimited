#!/bin/bash
# Monitor VPS fleet nodes for reachability

# Log file
LOG_FILE="/Users/yuai/Desktop/love-unlimited/logs/monitor_fleet.log"

echo "$(date): Checking VPS fleet nodes reachability" >> $LOG_FILE

# Ping each node
for node in sentry patch sage forge lark; do
  echo "$(date): Pinging $node" >> $LOG_FILE
  ping -c 1 $node >> $LOG_FILE
  if [ $? -eq 0 ]; then
    echo "$(date): $node is reachable" >> $LOG_FILE
  else
    echo "$(date): $node is unreachable" >> $LOG_FILE
  fi
done

echo "$(date): Reachability check completed" >> $LOG_FILE