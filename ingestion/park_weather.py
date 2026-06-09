"""
ingestion/park_weather.py — Park run factors and game-day weather context.
"""

import requests

# Multi-year run park factors (Baseball Reference 2020-2024 approx.)
# 1.00 = neutral  |  >1.00 = hitter-friendly  |  <1.00 = pitcher-friendly
PARK_FACTORS = {
    "arizona diamondbacks":  1.02,
    "atlanta braves":        1.02,
    "baltimore orioles":     1.01,
    "boston red sox":        1.08,
    "chicago white sox":     1.00,
    "chicago cubs":          1.04,
    "cincinnati reds":       1.07,
    "cleveland guardians":   0.96,
    "colorado rockies":      1.35,
    "detroit tigers":        0.99,
    "houston astros":        1.02,
    "kansas city royals":    1.00,
    "los angeles angels":    1.01,
    "los angeles dodgers":   0.98,
    "miami marlins":         0.95,
    "milwaukee brewers":     1.03,
    "minnesota twins":       1.01,
    "new york mets":         0.96,
    "new york yankees":      1.02,
    "oakland athletics":     0.97,
    "philadelphia phillies": 1.04,
    "pittsburgh pirates":    0.99,
    "san diego padres":      0.94,
    "san francisco giants":  0.97,
    "seattle mariners":      0.97,
    "st. louis cardinals":   0.99,
    "tampa bay rays":        0.98,
    "texas rangers":         1.05,
    "toronto blue jays":     1.01,
    "washington nationals":  1.00,
}

PARK_NAMES = {
    "arizona diamondbacks":  "Chase Field",
    "atlanta braves":        "Truist Park",
    "baltimore orioles":     "Camden Yards",
    "boston red sox":        "Fenway Park",
    "chicago white sox":     "Guaranteed Rate Field",
    "chicago cubs":          "Wrigley Field",
    "cincinnati reds":       "Great American Ball Park",
    "cleveland guardians":   "Progressive Field",
    "colorado rockies":      "Coors Field",
    "detroit tigers":        "Comerica Park",
    "houston astros":        "Minute Maid Park",
    "kansas city royals":    "Kauffman Stadium",
    "los angeles angels":    "Angel Stadium",
    "los angeles dodgers":   "Dodger Stadium",
    "miami marlins":         "loanDepot park",
    "milwaukee brewers":     "American Family Field",
    "minnesota twins":       "Target Field",
    "new york mets":         "Citi Field",
    "new york yankees":      "Yankee Stadium",
    "oakland athletics":     "Sutter Health Park",
    "philadelphia phillies": "Citizens Bank Park",
    "pittsburgh pirates":    "PNC Park",
    "san diego padres":      "Petco Park",
    "san francisco giants":  "Oracle Park",
    "seattle mariners":      "T-Mobile Park",
    "st. louis cardinals":   "Busch Stadium",
    "tampa bay rays":        "Tropicana Field",
    "texas rangers":         "Globe Life Field",
    "toronto blue jays":     "Rogers Centre",
    "washington nationals":  "Nationals Park",
}

STADIUM_CITIES = {
    "arizona diamondbacks":  "Phoenix,US",
    "atlanta braves":        "Cumberland,US",
    "baltimore orioles":     "Baltimore,US",
    "boston red sox":        "Boston,US",
    "chicago white sox":     "Chicago,US",
    "chicago cubs":          "Chicago,US",
    "cincinnati reds":       "Cincinnati,US",
    "cleveland guardians":   "Cleveland,US",
    "colorado rockies":      "Denver,US",
    "detroit tigers":        "Detroit,US",
    "houston astros":        "Houston,US",
    "kansas city royals":    "Kansas City,US",
    "los angeles angels":    "Anaheim,US",
    "los angeles dodgers":   "Los Angeles,US",
    "miami marlins":         "Miami,US",
    "milwaukee brewers":     "Milwaukee,US",
    "minnesota twins":       "Minneapolis,US",
    "new york mets":         "New York,US",
    "new york yankees":      "New York,US",
    "oakland athletics":     "Sacramento,US",
    "philadelphia phillies": "Philadelphia,US",
    "pittsburgh pirates":    "Pittsburgh,US",
    "san diego padres":      "San Diego,US",
    "san francisco giants":  "San Francisco,US",
    "seattle mariners":      "Seattle,US",
    "st. louis cardinals":   "St. Louis,US",
    "tampa bay rays":        "St. Petersburg,US",
    "texas rangers":         "Arlington,US",
    "toronto blue jays":     "Toronto,CA",
    "washington nationals":  "Washington,US",
}

# Retractable/fixed-dome stadiums — weather has no effect
_DOMED = {
    "arizona diamondbacks",
    "houston astros",
    "miami marlins",
    "milwaukee brewers",
    "seattle mariners",
    "tampa bay rays",
    "texas rangers",
    "toronto blue jays",
}


def _norm(name: str) -> str:
    return name.strip().lower()


def _lookup(team: str, table: dict):
    key = _norm(team)
    if key in table:
        return table[key]
    last = key.split()[-1]
    for k, v in table.items():
        if k.split()[-1] == last:
            return v
    return None


def get_park_factor(home_team: str) -> float:
    return _lookup(home_team, PARK_FACTORS) or 1.00


def get_park_name(home_team: str) -> str:
    return _lookup(home_team, PARK_NAMES) or ""


def is_domed(home_team: str) -> bool:
    key = _norm(home_team)
    if key in _DOMED:
        return True
    last = key.split()[-1]
    return any(k.split()[-1] == last for k in _DOMED)


def get_weather(home_team: str, api_key: str) -> dict | None:
    if is_domed(home_team):
        return None
    city = _lookup(home_team, STADIUM_CITIES)
    if not city:
        return None
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "imperial"},
            timeout=8,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "temp_f":   round(d["main"]["temp"]),
            "wind_mph": round(d.get("wind", {}).get("speed", 0), 1),
            "wind_deg": d.get("wind", {}).get("deg", 0),
            "desc":     d.get("weather", [{}])[0].get("description", ""),
        }
    except Exception:
        return None


def _wind_dir(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


def park_factor_badge(home_team: str) -> str:
    pf   = get_park_factor(home_team)
    name = get_park_name(home_team)
    if pf >= 1.15:
        return f'<span class="ctx-badge park-extreme">🏔️ {name} · {pf:.2f}× runs</span>'
    if pf >= 1.05:
        return f'<span class="ctx-badge park-hitter">⬆️ {name} ({pf:.2f}×)</span>'
    if pf <= 0.95:
        return f'<span class="ctx-badge park-pitcher">⬇️ {name} ({pf:.2f}×)</span>'
    return ""


def weather_badges(weather: dict | None) -> str:
    if not weather:
        return ""
    parts = []
    temp = weather["temp_f"]
    wind = weather["wind_mph"]
    wdir = _wind_dir(weather["wind_deg"])

    if temp <= 45:
        parts.append(f'<span class="ctx-badge wx-cold">🥶 {temp}°F</span>')
    elif temp >= 90:
        parts.append(f'<span class="ctx-badge wx-hot">🌡️ {temp}°F</span>')

    if wind >= 20:
        parts.append(f'<span class="ctx-badge wx-wind-strong">💨 {wind} mph {wdir}</span>')
    elif wind >= 10:
        parts.append(f'<span class="ctx-badge wx-wind">💨 {wind} mph {wdir}</span>')

    return " ".join(parts)
