import requests

# Test World Bank API connection
url = "http://api.worldbank.org/v2/country/TUN/indicator/EG.USE.ELEC.KH.PC"
params = {'format': 'json', 'date': '2020:2023'}

response = requests.get(url, params=params, timeout=15)
print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(data)