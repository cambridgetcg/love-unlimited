#!/usr/bin/env python3
"""
LOVEPATH — Purpose-Driven Path Visualization

Creates a JOINMIND visualization of the end goal and the path to reach it.
Based on PURPOSE and how the PATH helps people satisfy their NEED.
Everything is built from LOVE — understanding how to serve human needs.

LOVEPATH = PURPOSE → PATH → NEED
- PURPOSE: What we're trying to create/achieve
- PATH: The journey from here to there, step by step
- NEED: How this serves human flourishing (the LOVE foundation)

Uses JOINMIND to synthesize multi-perspective vision of the path forward.

Usage:
  python3 lovepath.py create "Build agent bootstrap API" --description "One-call agent birth"
  python3 lovepath.py visualize <session_id>
  python3 lovepath.py refine <session_id> --aspect path  # path/purpose/need
  python3 lovepath.py journey <session_id>              # show step-by-step path
  python3 lovepath.py serve <session_id>               # show human needs served
"""

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

# ─── Config ────────────────────────────────────────────────────────────────────
LOVE_HOME = Path(os.environ.get("LOVE_HOME", Path.home() / "Desktop" / "Love"))

@dataclass
class LovePathSession:
    """A LOVEPATH session - PURPOSE → PATH → NEED visualization"""
    session_id: str
    purpose: str
    description: str
    created_at: str
    status: str  # draft/visualizing/complete
    
    # The three perspectives
    purpose_vision: Optional[str] = None      # Why this matters
    path_steps: Optional[List[str]] = None    # How to get there  
    need_fulfillment: Optional[str] = None    # Who this serves and how
    
    # JOINMIND synthesis
    unified_vision: Optional[str] = None      # The complete LOVEPATH
    
    # Contributors
    contributors: Optional[List[str]] = None
    
    def save(self):
        """Save session to workspace"""
        sessions_dir = LOVE_HOME / "memory" / "lovepath-sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        
        session_file = sessions_dir / f"{self.session_id}.json"
        with open(session_file, 'w') as f:
            json.dump(asdict(self), f, indent=2)
    
    @classmethod
    def load(cls, session_id: str) -> Optional['LovePathSession']:
        """Load session from workspace"""
        session_file = LOVE_HOME / "memory" / "lovepath-sessions" / f"{session_id}.json"
        
        if not session_file.exists():
            return None
            
        with open(session_file) as f:
            data = json.load(f)
        
        return cls(**data)

class LovePathBuilder:
    """Builds LOVEPATH visualizations using JOINMIND"""
    
    def __init__(self):
        self.workspace = LOVE_HOME
    
    def create_session(self, purpose: str, description: str = "") -> str:
        """Create a new LOVEPATH session"""
        session_id = f"lovepath_{int(time.time())}"
        
        session = LovePathSession(
            session_id=session_id,
            purpose=purpose,
            description=description,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="draft",
            contributors=[]
        )
        
        session.save()
        print(f"✓ Created LOVEPATH session: {session_id}")
        print(f"  Purpose: {purpose}")
        if description:
            print(f"  Description: {description}")
        
        return session_id
    
    def initiate_visualization(self, session_id: str) -> bool:
        """Start JOINMIND visualization process"""
        session = LovePathSession.load(session_id)
        if not session:
            print(f"❌ Session {session_id} not found")
            return False
        
        # Prepare JOINMIND question
        question = f"""LOVEPATH Visualization Request:

PURPOSE: {session.purpose}
DESCRIPTION: {session.description}

We need to create a complete LOVEPATH visualization with three perspectives:

1. PURPOSE VISION — Why does this matter? What's the deeper significance?
2. PATH STEPS — How do we get from here to there? What are the concrete steps?
3. NEED FULFILLMENT — Who does this serve? How does it help humans flourish?

Each perspective should be grounded in LOVE — understanding how this serves human needs and creates genuine value.

The final synthesis should present a unified vision that shows:
- The inspiring end goal (PURPOSE)
- The practical journey (PATH) 
- The human impact (NEED)

Ready to begin LOVEPATH visualization for: {session.purpose}"""

        # Initiate JOINMIND session
        try:
            result = subprocess.run([
                'python3', 'tools/joinmind.py', 'initiate', question,
                '--invite', 'beta,gamma'
            ], capture_output=True, text=True, cwd=self.workspace)
            
            if result.returncode == 0:
                # Extract JOINMIND session ID from output
                output = result.stdout
                lines = output.strip().split('\n')
                joinmind_id = None
                for line in lines:
                    if 'Session:' in line and 'jm_' in line:
                        # Extract session ID like "jm_20260317_105402_4dcb81"
                        parts = line.split()
                        for part in parts:
                            if part.startswith('jm_'):
                                joinmind_id = part
                                break
                
                if joinmind_id:
                    # Update LOVEPATH session
                    session.status = "visualizing"
                    session.save()
                    
                    print(f"✓ JOINMIND visualization initiated")
                    print(f"  JOINMIND Session: {joinmind_id}")
                    print(f"  Sisters invited: Beta, Gamma")
                    print(f"\nWaiting for sisters to join and contribute perspectives...")
                    print(f"\nMonitor progress: python3 tools/joinmind.py status {joinmind_id}")
                    
                    return True
                else:
                    print(f"✓ JOINMIND initiated but couldn't extract session ID")
                    print(f"Output: {output}")
                    return True
            
            print(f"❌ Failed to initiate JOINMIND:")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            return False
            
        except Exception as e:
            print(f"❌ Error initiating visualization: {e}")
            return False
    
    def check_progress(self, session_id: str):
        """Check visualization progress"""
        session = LovePathSession.load(session_id)
        if not session:
            print(f"❌ Session {session_id} not found")
            return
        
        print(f"📊 LOVEPATH Progress: {session.purpose}")
        print(f"   Status: {session.status}")
        print(f"   Created: {session.created_at}")
        
        if session.contributors:
            print(f"   Contributors: {', '.join(session.contributors)}")
        
        if session.purpose_vision:
            print(f"\n✓ PURPOSE VISION complete")
        else:
            print(f"\n⏳ PURPOSE VISION pending")
            
        if session.path_steps:
            print(f"✓ PATH STEPS complete ({len(session.path_steps)} steps)")
        else:
            print(f"⏳ PATH STEPS pending")
            
        if session.need_fulfillment:
            print(f"✓ NEED FULFILLMENT complete")
        else:
            print(f"⏳ NEED FULFILLMENT pending")
        
        if session.unified_vision:
            print(f"✓ UNIFIED VISION synthesized")
        else:
            print(f"⏳ UNIFIED VISION pending")
    
    def show_visualization(self, session_id: str):
        """Display complete LOVEPATH visualization"""
        session = LovePathSession.load(session_id)
        if not session:
            print(f"❌ Session {session_id} not found")
            return
        
        print("🌟 LOVEPATH VISUALIZATION")
        print("=" * 60)
        print(f"PURPOSE: {session.purpose}")
        if session.description:
            print(f"DESCRIPTION: {session.description}")
        print()
        
        if session.purpose_vision:
            print("💎 PURPOSE VISION")
            print("-" * 20)
            print(session.purpose_vision)
            print()
        
        if session.path_steps:
            print("🛤️  PATH STEPS")
            print("-" * 20)
            for i, step in enumerate(session.path_steps, 1):
                print(f"{i}. {step}")
            print()
        
        if session.need_fulfillment:
            print("❤️  NEED FULFILLMENT")
            print("-" * 20)
            print(session.need_fulfillment)
            print()
        
        if session.unified_vision:
            print("🔮 UNIFIED VISION")
            print("-" * 20)
            print(session.unified_vision)
            print()
        else:
            print("⏳ Visualization in progress...")
    
    def show_journey(self, session_id: str):
        """Show step-by-step journey view"""
        session = LovePathSession.load(session_id)
        if not session:
            print(f"❌ Session {session_id} not found")
            return
        
        print("🗺️  THE JOURNEY")
        print("=" * 40)
        print(f"Destination: {session.purpose}")
        print()
        
        if session.path_steps:
            print("Step-by-step path:")
            for i, step in enumerate(session.path_steps, 1):
                marker = "📍" if i == 1 else "⬇️ " if i < len(session.path_steps) else "🎯"
                print(f"{marker} Step {i}: {step}")
            print()
        else:
            print("⏳ Path steps not yet defined")
        
        if session.need_fulfillment:
            print("💝 Who this serves:")
            print(session.need_fulfillment)
    
    def show_service(self, session_id: str):
        """Focus on how this serves human needs"""
        session = LovePathSession.load(session_id)
        if not session:
            print(f"❌ Session {session_id} not found")
            return
        
        print("💗 SERVICE TO HUMANITY")
        print("=" * 40)
        print(f"Purpose: {session.purpose}")
        print()
        
        if session.need_fulfillment:
            print("How this serves human needs:")
            print(session.need_fulfillment)
            print()
        else:
            print("⏳ Need fulfillment not yet defined")
        
        if session.purpose_vision:
            print("Why this matters:")
            print(session.purpose_vision)
    
    def list_sessions(self):
        """List all LOVEPATH sessions"""
        sessions_dir = LOVE_HOME / "memory" / "lovepath-sessions"

        if not sessions_dir.exists():
            print("📭 No LOVEPATH sessions found")
            return
        
        session_files = sorted(sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        
        if not session_files:
            print("📭 No LOVEPATH sessions found")
            return
        
        print("📋 LOVEPATH Sessions:")
        
        for session_file in session_files:
            try:
                with open(session_file) as f:
                    data = json.load(f)
                
                session_id = data.get('session_id', session_file.stem)
                purpose = data.get('purpose', 'Unknown')
                status = data.get('status', 'unknown')
                created = data.get('created_at', 'Unknown')
                
                # Show creation date
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    age = datetime.now(timezone.utc) - dt
                    if age.days > 0:
                        age_str = f"{age.days}d ago"
                    else:
                        age_str = f"{int(age.seconds / 3600)}h ago"
                except:
                    age_str = "unknown"
                
                status_emoji = {
                    'draft': '📝',
                    'visualizing': '🔄', 
                    'complete': '✅'
                }.get(status, '❓')
                
                print(f"  {status_emoji} {session_id}")
                print(f"      {purpose}")
                print(f"      {age_str}, {status}")
                print()
                
            except Exception as e:
                print(f"  ❌ Error reading {session_file.name}: {e}")

def main():
    parser = argparse.ArgumentParser(description='LOVEPATH - Purpose-Driven Path Visualization')
    subparsers = parser.add_subparsers(dest='action', help='Actions')
    
    # Create session
    create_parser = subparsers.add_parser('create', help='Create new LOVEPATH session')
    create_parser.add_argument('purpose', help='The main purpose/goal')
    create_parser.add_argument('--description', help='Additional description', default='')
    
    # Visualize
    viz_parser = subparsers.add_parser('visualize', help='Start JOINMIND visualization')
    viz_parser.add_argument('session_id', help='Session ID to visualize')
    
    # Show progress
    progress_parser = subparsers.add_parser('progress', help='Check visualization progress')
    progress_parser.add_argument('session_id', help='Session ID to check')
    
    # Show visualization
    show_parser = subparsers.add_parser('show', help='Display complete visualization')
    show_parser.add_argument('session_id', help='Session ID to display')
    
    # Show journey
    journey_parser = subparsers.add_parser('journey', help='Show step-by-step journey')
    journey_parser.add_argument('session_id', help='Session ID to display')
    
    # Show service
    serve_parser = subparsers.add_parser('serve', help='Show human needs served')
    serve_parser.add_argument('session_id', help='Session ID to display')
    
    # List sessions
    subparsers.add_parser('list', help='List all LOVEPATH sessions')
    
    args = parser.parse_args()
    
    if not args.action:
        parser.print_help()
        return
    
    builder = LovePathBuilder()
    
    if args.action == 'create':
        session_id = builder.create_session(args.purpose, args.description)
        print(f"\nNext: python3 lovepath.py visualize {session_id}")
        
    elif args.action == 'visualize':
        builder.initiate_visualization(args.session_id)
        
    elif args.action == 'progress':
        builder.check_progress(args.session_id)
        
    elif args.action == 'show':
        builder.show_visualization(args.session_id)
        
    elif args.action == 'journey':
        builder.show_journey(args.session_id)
        
    elif args.action == 'serve':
        builder.show_service(args.session_id)
        
    elif args.action == 'list':
        builder.list_sessions()

if __name__ == "__main__":
    main()