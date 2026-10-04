import requests
import re
import json
from bs4 import BeautifulSoup
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def discover_apis(url):
    print(f"Discovering APIs on: {url}\n")
    
    response = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    apis_found = set()
    
    # Method 1: Find API references in JavaScript files
    print("=== Checking JavaScript files ===")
    scripts = soup.find_all('script', src=True)
    
    for script in scripts[:10]:
        script_url = script['src']
        if not script_url.startswith('http'):
            if script_url.startswith('//'):
                script_url = f"https:{script_url}"
            elif script_url.startswith('/'):
                base = '/'.join(url.split('/')[:3])
                script_url = f"{base}{script_url}"
        
        try:
            js_response = requests.get(script_url, headers=HEADERS, timeout=5)
            js_content = js_response.text
            
            # Look for API patterns
            patterns = [
                r'["\'](/api/[^"\']+)["\']',
                r'["\'](/v[0-9]+/[^"\']+)["\']',
                r'["\'](/rest/[^"\']+)["\']',
                r'["\'](/graphql[^"\']*)["\']',
                r'fetch\(["\']([^"\']+)["\']',
                r'axios\.[a-z]+\(["\']([^"\']+)["\']',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, js_content)
                for match in matches:
                    if match.startswith('/'):
                        base = '/'.join(url.split('/')[:3])
                        full_url = f"{base}{match}"
                    else:
                        full_url = match
                    
                    apis_found.add(full_url)
                    
        except Exception as e:
            print(f"  Error fetching {script_url}: {e}")
    
    # Method 2: Check inline scripts
    print("\n=== Checking inline scripts ===")
    inline_scripts = soup.find_all('script', string=True)
    
    for script in inline_scripts:
        if script.string:
            patterns = [
                r'["\'](/api/[^"\']+)["\']',
                r'["\'](/v[0-9]+/[^"\']+)["\']',
                r'fetch\(["\']([^"\']+)["\']',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, script.string)
                for match in matches:
                    if match.startswith('/'):
                        base = '/'.join(url.split('/')[:3])
                        full_url = f"{base}{match}"
                    else:
                        full_url = match
                    
                    apis_found.add(full_url)
    
    # Method 3: Check common API endpoints
    print("\n=== Checking common endpoints ===")
    base = '/'.join(url.split('/')[:3])
    common_paths = [
        '/api',
        '/api/v1',
        '/api/v2',
        '/graphql',
        '/rest',
        '/json',
        '/wp-json',
        '/ajax',
    ]
    
    for path in common_paths:
        test_url = f"{base}{path}"
        try:
            resp = requests.get(test_url, headers=HEADERS, timeout=5)
            if resp.status_code == 200:
                content_type = resp.headers.get('content-type', '')
                if 'json' in content_type or 'application/json' in content_type:
                    print(f"  ✓ Found JSON API: {test_url}")
                    apis_found.add(test_url)
        except:
            pass
    
    return list(apis_found)


def test_api(api_url):
    print(f"\n=== Testing: {api_url} ===")
    
    try:
        response = requests.get(api_url, headers=HEADERS, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'unknown')}")
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            
            if 'json' in content_type:
                data = response.json()
                print(f"\nJSON response preview:")
                print(json.dumps(data, indent=2)[:500])
                return data
            else:
                print(f"\nResponse preview:")
                print(response.text[:500])
        
    except Exception as e:
        print(f"Error: {e}")
    
    return None


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python api_discovery.py <url>")
        print("\nExample:")
        print("  python api_discovery.py https://quotes.toscrape.com")
        print("  python api_discovery.py https://news.ycombinator.com")
        sys.exit(1)
    
    url = sys.argv[1]
    
    apis = discover_apis(url)
    
    print(f"\n{'='*60}")
    print(f"Found {len(apis)} potential APIs:")
    print('='*60)
    
    for api in apis:
        print(f"  • {api}")
    
    if apis:
        print("\nTesting first API...")
        test_api(apis[0])
    
    # Save results
    results_file = OUTPUT_DIR / "discovered_apis.json"
    with open(results_file, 'w') as f:
        json.dump({"url": url, "apis": apis}, f, indent=2)
    print(f"\nSaved results to {results_file}")
