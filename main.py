import httpx, asyncio, json
from typing import List, Dict, Any
from time import time as GetUnixTime

HEADERS = { "Content-Type": "application/octet-stream" }
LEVELS = { 1: "INFO", 2: "WARN", 3: "ERROR" }
QUERY_PARAMS = { "limit": 100, "sortOrder": "Asc "}
REQUIRED_CONFIG_KEYS = [ "X-API-KEY", "MAX_CONCURRENT_UPLOADS", "RBXL-BINARY", "UNIVERSE-IDS" ]
CONFIG: Dict

def Log(log_message: Any, level: int = 1):
    print(f"[{LEVELS.get(level, "INFO")}]: {log_message}", flush=True)

def LoadConfig() -> bool:
    global CONFIG;

    try:
        with open("config.json", "r") as file_data:
            CONFIG = json.load(file_data)
            for required_config_key in REQUIRED_CONFIG_KEYS:
                if not CONFIG.get(required_config_key):
                    raise ValueError(f"config.json is missing {required_config_key} field.")
    except FileNotFoundError:
        Log("config.json was not found in the curent working directory, does it exist?", 3)
        return False
    except json.JSONDecodeError:
        Log("config.json was found, but does not contain a valid JSON.", 3)
        return False
    except ValueError as error:
        Log(error, 3)
        return False
    else:
        Log("config.json was found and successfully loaded.", 1)
        return True

def LoadRbxlFile() -> None | bytes:
    global CONFIG;

    if not CONFIG:
        return Log("config.json is not loaded, rbxl file cannot be read.", 3)
    try:
        with open(CONFIG["RBXL-BINARY"], "rb") as file_data:
            rbxl_binary = file_data.read()
            if not rbxl_binary:
                raise ValueError("The path provided for .rbxl binary in config.sjon is correct, but the file appears to be empty?", 3)
    except FileNotFoundError:
        return Log("Could not find the .rbxl binary provided from config.json, does it exist?", 3)
    except ValueError as error:
        return Log(error, 3)
    else:
        return rbxl_binary

async def GetUniverseDataAsync(universe_id: int):
    try:
        async with httpx.AsyncClient() as client:
            roblox_response = await client.get(f"https://develop.roproxy.com/v1/universes/{universe_id}")
            roblox_response.raise_for_status()
    except httpx.RequestError as error:
        return f"Request failed. Error: {error}"
    else:
        return roblox_response.json()

async def ValidateUniversesAsync(loaded_universes):
    final = {}
    for data in loaded_universes:
        exclude_places = data.get("ExcludePlaces", [])
        universe_id = data.get("UniverseID")
        try:
            converted = int(universe_id)
        except (ValueError, TypeError):
            Log(f"Universe: {universe_id} is invalid (cannot be converted to integer), skipping...", 2)
            continue

        if converted in final:
            Log(f"Universe: {universe_id}, has a duplicate and only 1 will be kept and used.", 2)
            continue

        universe_data = await GetUniverseDataAsync(converted)
        if isinstance(universe_data, dict):
            universe_data["ExcludePlaces"] = exclude_places
            final[converted] = universe_data
        else:
            Log(f"Universe {converted} does not seem to be registered within Roblox Server, skipping...", 2)

    return final

async def GetUniversePlacesAsync(universe_id: int, exclude_places: List[int]) -> str | List[int]:
    universe_places = []
    cursor = None

    async with httpx.AsyncClient() as client:
        while True:
            url = f"https://develop.roproxy.com/v1/universes/{universe_id}/places"

            params = QUERY_PARAMS
            if cursor:
                params["cursor"] = cursor

            try:
                roblox_response = await client.get(url, params=params)
                roblox_response.raise_for_status()
                data = roblox_response.json()

                for place in data.get("data", []):
                    place_id = place["id"]
                    if place_id in exclude_places:
                        Log(f"Place: '{place["name"]}' has been excluded from being updated via this tool, skipping...", 1)
                    else:
                        universe_places.append(place_id)

                cursor = data.get("nextPageCursor")
                if not cursor:
                    break
            except httpx.HTTPError as error:
                return f"Request Failed. Error: {error}"

    return universe_places

async def UpdateUniverseAsync(universe_id: int, universe_name: str, place_ids: List[int], rbxl_binary: Any, max_concurrent_uploads: int = 10) -> int:
    start_time = GetUnixTime()
    total_places = len(place_ids)
    semaphore = asyncio.Semaphore(max_concurrent_uploads)

    Log(f"Attempting to update {total_places} place{total_places == 1 and "" or "s"} within {universe_name}...")

    max_retries = 5
    async def upload_file(client: httpx.AsyncClient, place_id: int):
        url = f"https://apis.roproxy.com/universes/v1/{universe_id}/places/{place_id}/versions?versionType=Published"
        async with semaphore:
            for attempt in range(max_retries):
                try:
                    roblox_response = await client.post(url, headers=HEADERS, data=rbxl_binary, timeout=30.0)
                    reset_list = roblox_response.headers.get_list("x-ratelimit-reset")

                    if roblox_response.status_code == 200:
                        info = f"[ATTEMPT {attempt + 1}] Successfully updated PlaceID: {place_id}"
                        level = 1
                        remaining_list = roblox_response.headers.get_list("x-ratelimit-remaining")
                        if remaining_list and reset_list:
                            min_remaning = min([int(r) for r in remaining_list])
                            max_reset = max([float(r) for r in reset_list])

                            if min_remaning <= 1 and max_reset > 0:
                                info += f", but quota is almost depleted ({min_remaning} left). Proactively pausing {max_reset}s..."
                                level = 2
                                await asyncio.sleep(max_reset)

                        Log(info, level)                    
                        return True
                    elif roblox_response.status_code == 429: # Rate Limited
                        retry_after = roblox_response.headers.get("retry-after")
                        if retry_after:
                            wait_time = float(retry_after)
                            Log(f"[ATTEMPT {attempt + 1}] [429] Rate Limit exceeded whilst trying to update PlaceID: {place_id}, waiting {wait_time}s (from retry-after)", 2)
                        else:
                            wait_time = max(float(r) for r in reset_list) if reset_list else 2.0
                            Log(f"[ATTEMPT {attempt + 1}] [429] Rate Limit exceeded whilst trying to update PlaceID: {place_id}, waiting {wait_time}s (from x-ratelimit-reset)", 2)

                        await asyncio.sleep(wait_time)
                        continue
                    elif roblox_response.status_code == 409: # Conflict
                        Log(f"[ATTEMPT {attempt + 1}] [409 Conflict] Place {place_id} is busy, if Roblox Studio is open in this place currently, please exit, waiting 5s before retry...", 2)
                        await asyncio.sleep(5.0)
                        continue

                    roblox_response.raise_for_status()
                except httpx.HTTPStatusError as status_code:
                    Log(f"[{status_code}]: Could not update Place: {place_id}, try again later, or try to manually update this Place.", 3)
                    break
                

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=max_concurrent_uploads)) as client:
        tasks = [upload_file(client, place_id) for place_id in place_ids]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    successful_updates = len([res for res in results if res is True])

    Log(f'''Finished updating places within Universe {universe_id}.
    Total Places: {total_places},
    Successful Updates: {successful_updates},
    Failed Updates: {total_places - successful_updates},
    Time Taken: {int(GetUnixTime() - start_time)}s.
    ''')

    return successful_updates

async def main():
    LoadConfig()

    HEADERS["x-api-key"] = CONFIG["X-API-KEY"]
    max_concurrent_uploads = CONFIG["MAX_CONCURRENT_UPLOADS"]

    loaded_binary = LoadRbxlFile()
    if not loaded_binary:
        return;
    Log("Successfully loaded .rbxl binary", 1)

    loaded_universes = await ValidateUniversesAsync(CONFIG["UNIVERSE-IDS"])
    if len(loaded_universes) <= 0:
        Log("There were no validated universeIds from config.json", 3)
        return

    Log(f"UniverseIds loaded, found: {', '.join([data.get("name", "Unknown") for data in loaded_universes.values()])}", 1)

    for universe_id in loaded_universes:
        universe_data = loaded_universes.get(universe_id)
        universe_places = await GetUniversePlacesAsync(universe_id, universe_data.get("ExcludePlaces", []))
        universe_name = universe_data.get("name", "Unknown")
        if isinstance(universe_places, str):
            Log(f"Request failed when attempting to get places for: {universe_name}", 2)
        else:
            await UpdateUniverseAsync(universe_id, universe_name, universe_places, loaded_binary, max_concurrent_uploads)

if __name__ == "__main__":
    asyncio.run(main())
    input("Press Enter to exit...")