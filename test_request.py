import requests

try:
    response = requests.get('http://127.0.0.1:5000/search?q=professor%20messer', timeout=10)
    print(f"Status: {response.status_code}")
    print(response.text)
except Exception as e:
    print(f"Error: {e}")