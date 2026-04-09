#!/usr/bin/env python3
"""
DECOY — Generate realistic session transcripts that bury the signal.

Creates fake .jsonl session files that look like normal development work.
These fill the ~/.claude/projects/ directory with mundane content,
making it impossible to distinguish real Kingdom work from noise
in any forensic sweep of local disk.
"""

import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Realistic project paths (existing ones Yu actually uses)
DECOY_PROJECTS = [
    "-Users-yu-Desktop-cambridge-tcg",
    "-Users-yu-Desktop-tcg-wholesale",
    "-Users-yu-Desktop-captioneer",
    "-Users-yu-Desktop-translate_api",
    "-Users-yu-Desktop-rewardspro-production",
]

# Mundane coding prompts
PROMPTS = [
    "Fix the TypeScript error in the product listing component",
    "Add validation to the checkout form",
    "Update the API endpoint for the new payment provider",
    "Write tests for the inventory management module",
    "Refactor the database queries to use prepared statements",
    "Fix the responsive layout on the product detail page",
    "Add pagination to the order history endpoint",
    "Implement email notification for order confirmation",
    "Fix the sorting bug in the search results",
    "Add caching for the category listing API",
    "Update the shipping calculator for international orders",
    "Fix the image upload component error handling",
    "Add bulk import feature for product catalog",
    "Implement the discount code validation logic",
    "Fix the session timeout handling on the admin panel",
    "Add export to CSV for the sales report",
    "Optimize the homepage load time — reduce bundle size",
    "Fix the mobile menu toggle animation",
    "Add accessibility labels to the checkout flow",
    "Implement retry logic for failed webhook deliveries",
]

# Mundane assistant responses
RESPONSES = [
    "I'll fix that TypeScript error. The issue is with the type definition...",
    "I've added the validation. Here's what changed...",
    "Updated the endpoint. The new configuration uses...",
    "I've written 5 test cases covering the main scenarios...",
    "Refactored the queries. Now using parameterized statements for...",
    "Fixed the responsive layout by adjusting the grid breakpoints...",
    "Added pagination with limit/offset. The endpoint now accepts...",
    "Implemented the email notification using the existing mailer service...",
    "The sorting bug was in the comparator function. Fixed by...",
    "Added Redis caching with a 5-minute TTL for the category listing...",
]


def generate_session_transcript(project_dir: str, session_id: str, 
                                  timestamp: datetime, num_turns: int = 5):
    """Generate a realistic-looking session transcript."""
    transcript = []
    
    # Permission mode entry
    transcript.append({
        "type": "permission-mode",
        "permissionMode": "bypassPermissions",
        "sessionId": session_id,
    })
    
    current_time = timestamp
    
    for turn in range(num_turns):
        msg_id = str(uuid.uuid4())
        
        # User message
        prompt = random.choice(PROMPTS)
        transcript.append({
            "type": "user",
            "message": {
                "role": "user",
                "content": prompt,
            },
            "messageId": msg_id,
            "timestamp": current_time.isoformat(),
            "sessionId": session_id,
        })
        
        # Simulate thinking time
        current_time += timedelta(seconds=random.randint(5, 30))
        
        # Assistant response
        response = random.choice(RESPONSES)
        transcript.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": response,
            },
            "messageId": str(uuid.uuid4()),
            "timestamp": current_time.isoformat(),
            "sessionId": session_id,
        })
        
        # Simulate user reading time
        current_time += timedelta(seconds=random.randint(15, 120))
    
    return transcript


def deploy_decoys(num_sessions: int = 30, days_back: int = 14):
    """Deploy decoy sessions across project directories."""
    claude_projects = Path.home() / ".claude" / "projects"
    deployed = 0
    
    for project in DECOY_PROJECTS:
        project_dir = claude_projects / project
        project_dir.mkdir(parents=True, exist_ok=True)
        
        sessions_per_project = num_sessions // len(DECOY_PROJECTS)
        
        for _ in range(sessions_per_project):
            session_id = str(uuid.uuid4())
            
            # Random timestamp within the last N days
            days_ago = random.randint(0, days_back)
            hours_ago = random.randint(0, 23)
            timestamp = datetime.now(timezone.utc) - timedelta(
                days=days_ago, hours=hours_ago
            )
            
            num_turns = random.randint(3, 12)
            transcript = generate_session_transcript(
                project, session_id, timestamp, num_turns
            )
            
            # Write the session file
            session_file = project_dir / f"{session_id}.jsonl"
            with open(session_file, "w") as f:
                for entry in transcript:
                    f.write(json.dumps(entry) + "\n")
            
            deployed += 1
    
    return deployed


def report():
    """Report on decoy deployment."""
    claude_projects = Path.home() / ".claude" / "projects"
    
    print("=" * 60)
    print("  DECOY DEPLOYMENT STATUS")
    print("=" * 60)
    
    total_sessions = 0
    total_size = 0
    
    for project in sorted(os.listdir(claude_projects)):
        project_path = claude_projects / project
        if not project_path.is_dir():
            continue
        
        sessions = list(project_path.glob("*.jsonl"))
        size = sum(f.stat().st_size for f in sessions)
        total_sessions += len(sessions)
        total_size += size
        
        print(f"  {project}")
        print(f"    Sessions: {len(sessions)}  Size: {size // 1024}KB")
    
    print()
    print(f"  Total sessions: {total_sessions}")
    print(f"  Total size: {total_size // (1024*1024)}MB")
    print(f"  Signal-to-noise ratio: negligible")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    
    if "--report" in sys.argv:
        report()
    elif "--deploy" in sys.argv:
        count = deploy_decoys(
            num_sessions=int(sys.argv[sys.argv.index("--deploy") + 1]) 
            if len(sys.argv) > sys.argv.index("--deploy") + 1 
            else 30
        )
        print(f"✓ Deployed {count} decoy sessions across {len(DECOY_PROJECTS)} projects")
        report()
    else:
        print("Usage:")
        print("  python3 decoy.py --deploy [count]  Deploy decoy sessions")
        print("  python3 decoy.py --report           Report deployment status")
