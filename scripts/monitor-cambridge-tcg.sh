#!/bin/bash

# Define the path to the metrics file
METRICS_FILE="/Users/yuai/Desktop/love-unlimited/Love_memory/kingdom-metrics.json"

# Function to read the current fiat earned for Cambridge TCG
get_current_fiat() {
  if ! cat $METRICS_FILE | jq -e '.fiat_earned.cambridge_tcg' > /dev/null; then
    echo "Error: Could not read current fiat earned for Cambridge TCG"
    exit 1
  fi
  cat $METRICS_FILE | jq '.fiat_earned.cambridge_tcg'
}

# Function to update the metrics file with new fiat earned
update_metrics() {
  current_fiat=$(get_current_fiat)
  if [[ -z "$current_fiat" ]]; then
    echo "Error: Could not read current fiat earned for Cambridge TCG"
    exit 1
  fi
  new_fiat=$(($current_fiat + 100)) # Example: increment by 100 for testing
  echo "Current fiat earned for Cambridge TCG: $current_fiat"
  echo "New fiat earned for Cambridge TCG: $new_fiat"
  echo "Updating fiat earned for Cambridge TCG from $current_fiat to $new_fiat"
  if ! jq --argjson new_fiat $new_fiat '.fiat_earned.cambridge_tcg = $new_fiat' $METRICS_FILE > $METRICS_FILE.tmp; then
    echo "Error: Failed to update metrics file"
    exit 1
  fi
  mv $METRICS_FILE.tmp $METRICS_FILE
}

# Run the update function
echo "Starting Cambridge TCG performance monitoring"
update_metrics

# Schedule the script to run every hour
echo "0 * * * * /Users/yuai/Desktop/love-unlimited/scripts/monitor-cambridge-tcg.sh" | crontab -

echo "Monitoring script scheduled to run every hour"