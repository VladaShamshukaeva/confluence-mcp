#!/usr/bin/env python3

import os
import base64
import urllib.request
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_dotenv() -> None:
    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=True)


load_project_dotenv()

email = os.getenv('ATLASSIAN_EMAIL')
token = os.getenv('ATLASSIAN_API_TOKEN')
base_url = os.getenv('CONFLUENCE_BASE_URL')

if not all([email, token, base_url]):
    print("❌ Missing env vars")
    sys.exit(1)

def test_basic_auth():
    """Test Basic Auth against Confluence site URL"""
    print("\n" + "="*60)
    print("TEST 1: Basic Auth (email:token)")
    print("="*60)

    auth_str = f"{email}:{token}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()
    url = f"{base_url}/rest/api/user/current"

    req = urllib.request.Request(
        url,
        headers={
            'Authorization': f'Basic {auth_b64}',
            'Accept': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req) as response:
            print(f"✓ SUCCESS! Status: {response.status}")
            return True
    except urllib.error.HTTPError as e:
        print(f"❌ FAILED! Status: {e.code}")
        if 'www-authenticate' in e.headers:
            print(f"   Server expects: {e.headers['www-authenticate']}")
        return False

def test_bearer_auth():
    """Test Bearer Auth against API gateway"""
    print("\n" + "="*60)
    print("TEST 2: Bearer Auth (OAuth) - API Gateway")
    print("="*60)

    url = "https://api.atlassian.com/oauth/token/accessible-resources"

    req = urllib.request.Request(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            print(f"✓ SUCCESS! Status: {response.status}")
            print(f"  Accessible resources: {len(data)} site(s)")
            for site in data[:2]:  # Show first 2
                print(f"    - {site.get('name')} ({site.get('url')})")
                if site.get('id'):
                    print(f"      cloudId: {site['id']}")
            return True, data
    except urllib.error.HTTPError as e:
        print(f"❌ FAILED! Status: {e.code}")
        return False, None

print("=" * 60)
print("CONFLUENCE AUTH DIAGNOSTIC")
print("=" * 60)
print(f"\nEnvironment:")
print(f"  Email: {email}")
print(f"  Token: {token[:30]}... (length: {len(token)})")
print(f"  Base URL: {base_url}")

# Test both auth methods
basic_ok = test_basic_auth()
bearer_ok, resources = test_bearer_auth()

# Recommendations
print("\n" + "="*60)
print("RECOMMENDATION")
print("="*60)

if bearer_ok and resources:
    print("""
✓ Bearer Auth WORKS! Your token is a valid OAuth token.

NEXT STEPS:
1. Use Bearer auth instead of Basic auth
2. Use the Atlassian API gateway URL:
   https://api.atlassian.com/ex/confluence/{cloudId}/wiki/rest/api/...

3. Get your cloudId from the accessible resources above
4. Update your code to use:
   curl -H "Authorization: Bearer $ATLASSIAN_API_TOKEN" \\
        https://api.atlassian.com/ex/confluence/{cloudId}/wiki/rest/api/content?limit=1
""")
elif basic_ok:
    print("""
✓ Basic Auth WORKS! Your token is a valid Confluence API token.

NEXT STEPS:
1. Use Basic auth with username:password
2. Use the site-specific URL directly:
   https://confluence-aholddelhaize.atlassian.net/wiki/rest/api/...

3. Keep using:
   curl -u "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \\
        https://confluence-aholddelhaize.atlassian.net/wiki/rest/api/content?limit=1
""")
else:
    print(f"""
❌ BOTH AUTH METHODS FAILED!

This suggests:
1. Token is EXPIRED or REVOKED
   - Go to `https://id.atlassian.com/manage-profile/security/api-tokens`
   - Check if the token is still valid
   - Delete and create a new one if expired

2. Wrong Confluence instance
   - Verify the email has access to this Confluence instance
   - Base URL: {base_url}

3. Token scope mismatch
   - If using scoped OAuth, verify the scopes include Confluence access
   - Your token has scopes for: read:confluence-content.all, write:confluence-content
   - These are valid for Bearer auth with the API gateway
""")

print("="*60)

