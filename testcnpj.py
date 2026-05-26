import json
import requests

API_KEY = "bdbf4d21-5bf7-42d0-80c0-de6907b4dbce-e33505e0-ec91-4730-a0ef-6ad3927e5458"
CNPJ = "37848684000277"

url = f"https://api.cnpja.com/office/{CNPJ}"

params = {
    "registrations": "ORIGIN",
    "strategy": "CACHE_IF_ERROR",
}

headers = {
    "Accept": "application/json",
    "Authorization": API_KEY,
}

response = requests.get(
    url,
    params=params,
    headers=headers,
    timeout=30,
)

print("STATUS:", response.status_code)
print("URL:", response.url)
print("HEADERS RESPOSTA:", dict(response.headers))

try:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
except Exception:
    print(response.text)