import hashlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.geo.districts import get_district_centroid


class HybridGeocoder:
    """Гибридный геокодер: оффлайн-центроиды районов с детерминированным джиттером + SQLite кэш Nominatim."""

    def __init__(self, cache_db_path: str | None = None):
        if cache_db_path is None:
            cache_dir = Path(".cache")
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_db_path = str(cache_dir / "geocache.sqlite3")
        else:
            self.cache_db_path = cache_db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS geocache (
                    address TEXT PRIMARY KEY,
                    lat REAL,
                    lon REAL,
                    source TEXT
                )
                """
            )
            conn.commit()

    def get_cached(self, address: str) -> tuple[float, float] | None:
        with sqlite3.connect(self.cache_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lat, lon FROM geocache WHERE address = ?", (address,))
            row = cursor.fetchone()
            if row:
                return float(row[0]), float(row[1])
        return None

    def save_cached(self, address: str, lat: float, lon: float, source: str) -> None:
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO geocache (address, lat, lon, source) VALUES (?, ?, ?, ?)",
                (address, lat, lon, source),
            )
            conn.commit()

    def geocode_deterministic(self, district: str, address: str) -> tuple[float, float]:
        """Детерминированное вычисление координат на основе центроида района и хэша адреса."""
        base_lat, base_lon = get_district_centroid(district)
        # Получаем стабильный псевдослучайный сдвиг в пределах района (+-1.2 км)
        clean_addr = address.strip().lower()
        hash_val = int(hashlib.md5(clean_addr.encode("utf-8")).hexdigest()[:8], 16)
        # Дельта от -0.012 до +0.012 градусов
        lat_offset = ((hash_val % 1000) / 1000.0 - 0.5) * 0.024
        lon_offset = (((hash_val // 1000) % 1000) / 1000.0 - 0.5) * 0.036
        lat = round(base_lat + lat_offset, 5)
        lon = round(base_lon + lon_offset, 5)
        return lat, lon

    def geocode(
        self, district: str, address: str, try_online: bool = False
    ) -> tuple[float, float, str]:
        """Определение координат с проверкой кэша, опциональным онлайном и оффлайн-fallback."""
        full_query = f"{address}, {district}".strip()
        cached = self.get_cached(full_query)
        if cached:
            return cached[0], cached[1], "cache"

        if try_online:
            try:
                encoded = urllib.parse.quote(f"Москва, {address}")
                url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=1"
                req = urllib.request.Request(
                    url, headers={"User-Agent": "MCT-Dispatcher-Planner/1.0 (hackathon-beeline)"}
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data and len(data) > 0:
                        lat = round(float(data[0]["lat"]), 5)
                        lon = round(float(data[0]["lon"]), 5)
                        self.save_cached(full_query, lat, lon, "nominatim")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                pass  # Fallback to deterministic offline resolution

        lat, lon = self.geocode_deterministic(district, address)
        self.save_cached(full_query, lat, lon, "district_jitter")
        return lat, lon, "district_jitter"
