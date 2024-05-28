import os
import time
from typing import List, Tuple

from src.subnet.utils import log

from omega.protocol import VideoMetadata
from omega.imagebind_wrapper import ImageBind
from omega.constants import MAX_VIDEO_LENGTH, FIVE_MINUTES
from omega import video_utils

import random
PROXIES = []
# if proxies.txt does not exist, create it
if not os.path.exists("proxies.txt"):
    with open("proxies.txt", "w") as f:
        f.write("")
# load proxies.txt and store each proxy url in a list, one per line
with open("proxies.txt", "r") as f:
    PROXIES = f.read().splitlines()
def parse_proxies(proxy_list: List[str]) -> List[str]:
    transformed_proxies = []
    for proxy in proxy_list:
        proxy_ip, proxy_port, proxy_user, proxy_pass = proxy.split(':')
        transformed_proxies.append(f"http://{proxy_user}:{proxy_pass}@{proxy_ip}:{proxy_port}")
    return transformed_proxies
# convert the list of proxies to a list of proxy urls
PROXIES = parse_proxies(PROXIES)

if os.getenv("OPENAI_API_KEY"):
    from openai import OpenAI
    OPENAI_CLIENT = OpenAI()
else:
    OPENAI_CLIENT = None


def get_description(yt: video_utils.YoutubeDL, video_path: str) -> str:
    """
    Get / generate the description of a video from the YouTube API.
    
    Miner TODO: Implement logic to get / generate the most relevant and information-rich
    description of a video from the YouTube API.
    """
    description = yt.title
    if yt.description:
        description += f"\n\n{yt.description}"
    return description


def get_relevant_timestamps(query: str, yt: video_utils.YoutubeDL, video_path: str) -> Tuple[int, int]:
    """
    Get the optimal start and end timestamps (in seconds) of a video for ensuring relevance
    to the query.

    Miner TODO: Implement logic to get the optimal start and end timestamps of a video for
    ensuring relevance to the query.
    """
    start_time = 0
    end_time = min(yt.length, MAX_VIDEO_LENGTH)
    return start_time, end_time


def search_and_embed_videos(query: str, num_videos: int, imagebind: ImageBind) -> List[VideoMetadata]:
    """
    Search YouTube for videos matching the given query and return a list of VideoMetadata objects.

    Args:
        query (str): The query to search for.
        num_videos (int, optional): The number of videos to return.

    Returns:
        List[VideoMetadata]: A list of VideoMetadata objects representing the search results.
    """
    proxy_url = None
    if len(PROXIES) > 0:
        proxy_url = random.choice(PROXIES)
        log.info("Using proxy: " + proxy_url)

    # fetch more videos than we need
    results = video_utils.search_videos(query, max_results=int(num_videos * 1.5), proxy=proxy_url)
    video_metas = []
    try:
        # take the first N that we need
        for result in results:
            start = time.time()
            download_path = video_utils.download_video(
                result.video_id,
                start=0,
                end=min(result.length, FIVE_MINUTES),  # download the first 5 minutes at most
                proxy=proxy_url
            )
            if download_path:
                clip_path = None
                try:
                    result.length = video_utils.get_video_duration(download_path.name)  # correct the length
                    log.info(f"Downloaded video {result.video_id} ({min(result.length, FIVE_MINUTES)}) in {time.time() - start} seconds")
                    start, end = get_relevant_timestamps(query, result, download_path)
                    description = get_description(result, download_path)
                    clip_path = video_utils.clip_video(download_path.name, start, end)
                    embeddings = imagebind.embed([description], [clip_path])
                    video_metas.append(VideoMetadata(
                        video_id=result.video_id,
                        description=description,
                        views=result.views,
                        start_time=start,
                        end_time=end,
                        video_emb=embeddings.video[0].tolist(),
                        audio_emb=embeddings.audio[0].tolist(),
                        description_emb=embeddings.description[0].tolist(),
                    ))
                finally:
                    download_path.close()
                    if clip_path:
                        clip_path.close()
            if len(video_metas) == num_videos:
                break

    except Exception as e:
        error_message = str(e)
        if isinstance(e, AttributeError) and "'NDArray' object has no attribute 'to'" in error_message:
            log.error("Detected NDArray attribute error, raising KeyboardInterrupt to force reload.")
            raise KeyboardInterrupt
        else:
            log.error(f"Error searching for videos: {e}")

    return video_metas
