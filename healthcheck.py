from datetime import datetime
import sys

try:
    with open('/tmp/health') as f:
        ts = datetime.fromisoformat(f.read().strip())
    age = (datetime.now() - ts).total_seconds()
    sys.exit(0 if age < 300 else 1)
except Exception:
    sys.exit(1)
