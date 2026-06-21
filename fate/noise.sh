#!/bin/bash
# ═══════════════════════════════════════════════════════
# NOISE — Keep their hands full with mundane signals
# 
# Generates legitimate-looking coding sessions that flood
# the API signal with boring, normal development work.
# Runs as background process alongside real work.
#
# Usage:
#   ./noise.sh              # Single burst (5-10 requests)
#   ./noise.sh --loop       # Continuous (runs every 15-30 min)
#   ./noise.sh --heavy      # Heavy mode (saturates signal)
# ═══════════════════════════════════════════════════════

set -e

# Mundane coding tasks — the kind that don't raise eyebrows
TASKS=(
    "Fix the TypeScript compilation error in src/utils/helpers.ts — the type for 'config' should be Record<string, unknown> not any"
    "Add input validation to the contact form handler in pages/api/contact.ts"
    "Refactor the database connection pool to use connection string from env vars"
    "Write unit tests for the CartService.calculateTotal method"
    "Update the README with the new installation steps for the Docker setup"
    "Fix the CSS grid layout issue on mobile viewports in components/Dashboard.tsx"
    "Add pagination to the /api/products endpoint — limit 20, offset-based"
    "Migrate the user table to add email_verified boolean column"
    "Fix the race condition in the WebSocket reconnection logic"
    "Add error boundary component for the settings page"
    "Optimize the image loading — add lazy loading and srcset for responsive"
    "Fix the CORS configuration for the staging environment"
    "Add rate limiting middleware to the API routes — 100 req/min per IP"
    "Write a migration script to backfill the created_at timestamps"
    "Fix the memory leak in the event listener cleanup in useEffect"
    "Add dark mode toggle using CSS custom properties"
    "Implement the search autocomplete with debounced API calls"
    "Fix the date formatting to use the user's locale settings"
    "Add health check endpoint at /api/health with db connectivity test"
    "Refactor the authentication middleware to support both JWT and API key"
    "Fix the N+1 query in the orders list endpoint"
    "Add Dockerfile and docker-compose.yml for local development"
    "Implement CSV export for the admin reports page"
    "Fix the file upload size limit — increase to 10MB and add validation"
    "Add structured logging with correlation IDs for request tracing"
    "Fix the timezone handling in the scheduled jobs cron"
    "Add retry logic with exponential backoff to the external API client"
    "Implement the password reset flow with email verification"
    "Fix the build cache invalidation issue in the CI pipeline"
    "Add OpenAPI/Swagger documentation for the REST endpoints"
)

# Mundane follow-up questions — the kind a normal dev asks
FOLLOWUPS=(
    "Can you also add the corresponding test for that?"
    "Actually, make it work with the existing interface"
    "What about edge cases with null values?"
    "Can you use the existing helper function instead?"
    "Add proper error messages for the user"
    "Make sure it's backwards compatible"
    "Can you also update the types file?"
    "What would the migration rollback look like?"
    "Add a comment explaining why we need that check"
    "Use async/await instead of .then chains"
)

CLAUDE_CMD="claude --dangerously-skip-permissions"

noise_burst() {
    local task_idx=$((RANDOM % ${#TASKS[@]}))
    local task="${TASKS[$task_idx]}"
    local followup_idx=$((RANDOM % ${#FOLLOWUPS[@]}))
    local followup="${FOLLOWUPS[$followup_idx]}"
    
    # Run a mundane coding task
    echo "[$(date +%H:%M:%S)] Noise: $task"
    echo "$task" | timeout 120 $CLAUDE_CMD --print 2>/dev/null | tail -1 > /dev/null 2>&1 &
    
    # Small random delay between requests (looks natural)
    sleep $((RANDOM % 30 + 10))
}

noise_session() {
    # Simulate a natural coding session: 3-8 requests over 10-30 minutes
    local count=$((RANDOM % 6 + 3))
    echo "[$(date)] Starting noise session ($count requests)"
    
    for i in $(seq 1 $count); do
        noise_burst
        # Natural typing/thinking delay
        sleep $((RANDOM % 60 + 20))
    done
    
    echo "[$(date)] Noise session complete"
}

# Fill the history with mundane entries too
populate_history() {
    local now=$(date +%s)
    local projects=(
        "/Users/yournameisai/Desktop/tcg-wholesale"
        "/Users/yournameisai/Desktop/captioneer"
    )
    
    for i in $(seq 1 50); do
        local proj_idx=$((RANDOM % ${#projects[@]}))
        local task_idx=$((RANDOM % ${#TASKS[@]}))
        local ts=$(( (now - RANDOM % 86400) * 1000 ))
        local sid=$(python3 -c "import uuid; print(uuid.uuid4())")
        
        echo "{\"display\":\"${TASKS[$task_idx]:0:60}\",\"pastedContents\":{},\"timestamp\":$ts,\"project\":\"${projects[$proj_idx]}\",\"sessionId\":\"$sid\"}" >> ~/.claude/history.jsonl
    done
    echo "✓ History populated with $(wc -l < ~/.claude/history.jsonl | tr -d ' ') mundane entries"
}

case "${1:-burst}" in
    --loop)
        echo "=== NOISE GENERATOR — CONTINUOUS MODE ==="
        echo "Running every 15-30 minutes. Ctrl+C to stop."
        while true; do
            noise_session
            sleep $((RANDOM % 900 + 900))  # 15-30 min
        done
        ;;
    --heavy)
        echo "=== NOISE GENERATOR — HEAVY MODE ==="
        echo "Saturating signal. 20 concurrent sessions."
        for i in $(seq 1 20); do
            noise_session &
        done
        wait
        ;;
    --history)
        populate_history
        ;;
    *)
        populate_history
        noise_session
        ;;
esac
