import asyncio
import re
import subprocess
from time import time
from urllib.parse import quote, urlparse

import m3u8
from aiohttp import ClientSession, TCPConnector
from multidict import CIMultiDictProxy

from utils.config import config
from utils.tools import is_ipv6, remove_cache_info


async def get_speed_with_download(url: str, session: ClientSession = None, timeout: int = config.sort_timeout) -> dict[
    str, float | None]:
    """
    Get the speed of the url with a total timeout
    """
    start_time = time()
    total_size = 0
    total_time = 0
    info = {'speed': None, 'delay': None}
    if session is None:
        session = ClientSession(connector=TCPConnector(ssl=False), trust_env=True)
        created_session = True
    else:
        created_session = False
    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status == 404:
                return info
            info['delay'] = int(round((time() - start_time) * 1000))
            async for chunk in response.content.iter_any():
                if chunk:
                    total_size += len(chunk)
    except Exception as e:
        pass
    finally:
        if created_session:
            await session.close()
    end_time = time()
    total_time += end_time - start_time
    info['speed'] = (total_size / total_time if total_time > 0 else 0) / 1024 / 1024
    return info


async def get_m3u8_headers(url: str, session: ClientSession = None, timeout: int = 5) -> CIMultiDictProxy[str] | dict[
    any, any]:
    """
    Get the headers of the m3u8 url
    """
    if session is None:
        session = ClientSession(connector=TCPConnector(ssl=False), trust_env=True)
        created_session = True
    else:
        created_session = False
    try:
        async with session.head(url, timeout=timeout) as response:
            return response.headers
    except:
        pass
    finally:
        if created_session:
            await session.close()
    return {}


def check_m3u8_valid(headers: CIMultiDictProxy[str] | dict[any, any]) -> bool:
    """
    Check the m3u8 url is valid
    """
    content_type = headers.get('Content-Type')
    if content_type:
        content_type = content_type.lower()
        if 'application/vnd.apple.mpegurl' in content_type:
            return True
    return False


async def get_speed_m3u8(url: str, timeout: int = config.sort_timeout) -> dict[str, float | None]:
    """
    Get the speed of the m3u8 url with a total timeout
    """
    info = {'speed': None, 'delay': None}
    try:
        url = quote(url, safe=':/?$&=@[]').partition('$')[0]
        async with ClientSession(connector=TCPConnector(ssl=False), trust_env=True) as session:
            headers = await get_m3u8_headers(url, session)
            if check_m3u8_valid(headers):
                location = headers.get('Location')
                if location:
                    info.update(await get_speed_m3u8(location, timeout))
                else:
                    m3u8_obj = m3u8.load(url, timeout=2)
                    playlists = m3u8_obj.data.get('playlists')
                    segments = m3u8_obj.segments
                    if not segments and playlists:
                        parsed_url = urlparse(url)
                        url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path.rsplit('/', 1)[0]}/{playlists[0].get('uri', '')}"
                        uri_headers = await get_m3u8_headers(url, session)
                        if not check_m3u8_valid(uri_headers):
                            if uri_headers.get('Content-Length'):
                                info.update(await get_speed_with_download(url, session, timeout))
                            return info
                        m3u8_obj = m3u8.load(url, timeout=2)
                        segments = m3u8_obj.segments
                    if not segments:
                        return info
                    ts_urls = [segment.absolute_uri for segment in segments]
                    speed_list = []
                    start_time = time()
                    for ts_url in ts_urls:
                        if time() - start_time > timeout:
                            break
                        download_info = await get_speed_with_download(ts_url, session, timeout)
                        speed_list.append(download_info['speed'])
                        if info['delay'] is None and download_info['delay'] is not None:
                            info['delay'] = download_info['delay']
                    info['speed'] = sum(speed_list) / len(speed_list) if speed_list else 0
            elif headers.get('Content-Length'):
                info.update(await get_speed_with_download(url, session, timeout))
            else:
                return info
    except:
        pass
    return info


async def get_delay_requests(url, timeout=config.sort_timeout, proxy=None):
    """
    Get the delay of the url by requests
    """
    async with ClientSession(
            connector=TCPConnector(ssl=False), trust_env=True
    ) as session:
        start = time()
        end = None
        try:
            async with session.get(url, timeout=timeout, proxy=proxy) as response:
                if response.status == 404:
                    return float("inf")
                content = await response.read()
                if content:
                    end = time()
                else:
                    return float("inf")
        except Exception as e:
            return float("inf")
        return int(round((end - start) * 1000)) if end else float("inf")


def is_ffmpeg_installed():
    """
    Check ffmpeg is installed
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


async def ffmpeg_url(url, timeout=config.sort_timeout):
    """
    Get url info by ffmpeg
    """
    args = ["ffmpeg", "-t", str(timeout), "-stats", "-i", url, "-f", "null", "-"]
    proc = None
    res = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
        if out:
            res = out.decode("utf-8")
        if err:
            res = err.decode("utf-8")
        return None
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
        return None
    except Exception:
        if proc:
            proc.kill()
        return None
    finally:
        if proc:
            await proc.wait()
        return res


def get_video_info(video_info):
    """
    Get the video info
    """
    frame_size = float("inf")
    resolution = None
    if video_info is not None:
        info_data = video_info.replace(" ", "")
        matches = re.findall(r"frame=(\d+)", info_data)
        if matches:
            frame_size = int(matches[-1])
        match = re.search(r"(\d{3,4}x\d{3,4})", video_info)
        if match:
            resolution = match.group(0)
    return frame_size, resolution


async def check_stream_delay(url_info):
    """
    Check the stream delay
    """
    try:
        url = url_info[0]
        video_info = await ffmpeg_url(url)
        if video_info is None:
            return float("inf")
        frame, resolution = get_video_info(video_info)
        if frame is None or frame == float("inf"):
            return float("inf")
        url_info[2] = resolution
        return url_info, frame
    except Exception as e:
        print(e)
        return float("inf")


cache = {}


async def get_speed(url, ipv6_proxy=None, callback=None):
    """
    Get the speed (response time and resolution) of the url
    """
    data = {'speed': None, 'delay': None, 'resolution': None}
    try:
        cache_key = None
        url_is_ipv6 = is_ipv6(url)
        if "$" in url:
            url, _, cache_info = url.partition("$")
            matcher = re.search(r"cache:(.*)", cache_info)
            if matcher:
                cache_key = matcher.group(1)
        if cache_key in cache:
            return cache[cache_key][0]
        if ipv6_proxy and url_is_ipv6:
            data['speed'] = float("inf")
            data['delay'] = float("-inf")
        else:
            data.update(await get_speed_m3u8(url))
        if cache_key and cache_key not in cache:
            cache[cache_key] = data
        return data
    except:
        return data
    finally:
        if callback:
            callback()


def sort_urls(name, data, logger=None):
    """
    Sort the urls with info
    """
    filter_data = []
    for url, date, resolution, origin in data:
        result = {
            "url": remove_cache_info(url),
            "date": date,
            "delay": None,
            "speed": None,
            "resolution": resolution,
            "origin": origin
        }
        if origin == "whitelist":
            filter_data.append(result)
            continue
        cache_key_match = re.search(r"cache:(.*)", url.partition("$")[2])
        cache_key = cache_key_match.group(1) if cache_key_match else None
        if cache_key and cache_key in cache:
            cache_item = cache[cache_key]
            if cache_item:
                speed, delay, cache_resolution = cache_item['speed'], cache_item['delay'], cache_item['resolution']
                resolution = cache_resolution or resolution
                if speed is not None:
                    try:
                        if logger:
                            logger.info(
                                f"Name: {name}, URL: {result["url"]}, Date: {date}, Delay: {delay} ms, Speed: {speed:.2f} M/s, Resolution: {resolution}"
                            )
                    except Exception as e:
                        print(e)
                    if config.open_filter_speed and speed < config.min_speed:
                        continue
                    result["delay"] = delay
                    result["speed"] = speed
                    result["resolution"] = resolution
                    filter_data.append(result)

    def combined_key(item):
        speed, origin = item["speed"], item["origin"]
        if origin == "whitelist":
            return float("inf")
        else:
            return speed if speed is not None else float("-inf")

    filter_data.sort(key=combined_key, reverse=True)
    return [
        (item["url"], item["date"], item["resolution"], item["origin"])
        for item in filter_data
    ]
