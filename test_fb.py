import os, requests
from dotenv import load_dotenv
load_dotenv()

page_id = os.getenv('FACEBOOK_PAGE_ID', '')
token = os.getenv('FACEBOOK_ACCESS_TOKEN', '')

print(f'Page ID: {page_id[:10]}...' if page_id else 'Page ID: MISSING')
print(f'Token: {token[:20]}...' if token else 'Token: MISSING')

if not page_id or not token:
    print('\nERROR: Missing FACEBOOK_PAGE_ID or FACEBOOK_ACCESS_TOKEN in .env')
    exit()

# Test 1: Check if token is valid
print('\n--- Test 1: Token validation ---')
r = requests.get(f'https://graph.facebook.com/v18.0/me', params={'access_token': token})
print(f'Status: {r.status_code}')
print(f'Response: {r.json()}')

# Test 2: Check if page access works
print('\n--- Test 2: Page access ---')
r = requests.get(f'https://graph.facebook.com/v18.0/{page_id}', params={'access_token': token, 'fields': 'name,id'})
print(f'Status: {r.status_code}')
print(f'Response: {r.json()}')

# Test 3: Check permissions
print('\n--- Test 3: Permissions ---')
r = requests.get(f'https://graph.facebook.com/v18.0/me/permissions', params={'access_token': token})
print(f'Status: {r.status_code}')
perms = r.json().get('data', [])
for p in perms:
    status = 'OK' if p['status'] == 'granted' else 'DENIED'
    print(f'  [{status}] {p["permission"]}')

# Test 4: Try initializing a reel upload
print('\n--- Test 4: Reel upload init ---')
r = requests.post(
    f'https://graph.facebook.com/v18.0/{page_id}/video_reels',
    params={'access_token': token},
    json={'upload_phase': 'start'}
)
print(f'Status: {r.status_code}')
print(f'Response: {r.json()}')
