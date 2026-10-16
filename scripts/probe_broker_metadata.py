"""Public metadata evidence only. No account authentication or credential reuse."""

import json
import urllib.request
from datetime import datetime, timezone

URLS = [
    "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading",
    "https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading",
]


def main():
    evidence = {"observed_at": datetime.now(timezone.utc).isoformat(), "authenticated": False, "metadata": []}
    for url in URLS:
        with urllib.request.urlopen(url, timeout=20) as response:
            evidence["metadata"].append({"url": url, "status": response.status, "body": json.load(response)})
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
