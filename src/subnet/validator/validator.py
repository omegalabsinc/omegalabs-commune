"""
CommuneX example of a Text Validator Module

This module provides the VideosValidator class for validating video metadata generated by modules in the Omega subnet.
The VideosValidator retrieves module addresses from the subnet, prompts the modules to gather videos and compute metdata to a given topic prompt,
and scores the response.

Classes:
    VideosValidator: A class for validating Video metadata generated by modules in Omega's subnet.

Functions:
    set_weights: Blockchain call to set weights for miners based on their scores.
    cut_to_max_allowed_weights: Cut the scores to the maximum allowed weights.
    extract_address: Extract an address from a string.
    get_subnet_netuid: Retrieve the network UID of the subnet.
    get_ip_port: Get the IP and port information from module addresses.

Constants:
    IP_REGEX: A regular expression pattern for matching IP addresses.
"""

import os
import asyncio
import concurrent.futures
import re
import time
from functools import partial

from communex.misc import get_map_modules
from communex.client import CommuneClient  # type: ignore
from communex.module.client import ModuleClient  # type: ignore
from communex.module.module import Module  # type: ignore
from communex.types import Ss58Address  # type: ignore
from substrateinterface import Keypair  # type: ignore

from ._config import ValidatorSettings
from src.subnet.utils import log

from aiohttp import ClientSession, BasicAuth
from typing import List, Tuple, Optional, BinaryIO
from pydantic import ValidationError
import datetime as dt
import os
import random
import traceback

import torch
import torch.nn.functional as F
from torch.nn import CosineSimilarity
import wandb

from omega.utils.config import load_config_from_file
from omega.protocol import Videos, VideoMetadata
from omega.constants import (
    VALIDATOR_TIMEOUT, 
    VALIDATOR_TIMEOUT_MARGIN, 
    MAX_VIDEO_LENGTH, 
    MIN_VIDEO_LENGTH,
    CHECK_PROBABILITY,
    DIFFERENCE_THRESHOLD, 
    SIMILARITY_THRESHOLD, 
    VIDEO_DOWNLOAD_TIMEOUT, 
    MIN_SCORE, 
    FAKE_VIDEO_PUNISHMENT
)
from omega import video_utils
from omega.imagebind_wrapper import ImageBind, Embeddings, run_async

NO_RESPONSE_MINIMUM = 0.005
global GPU_SEMAPHORE
GPU_SEMAPHORE = asyncio.Semaphore(1)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)

IP_REGEX = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+")


def set_weights(
    settings: ValidatorSettings,
    score_dict: dict[
        int, float
    ],  # implemented as a float score from 0 to 1, one being the best
    # you can implement your custom logic for scoring
    netuid: int,
    client: CommuneClient,
    key: Keypair,
) -> None:
    """
    Set weights for miners based on their scores.

    Args:
        score_dict: A dictionary mapping miner UIDs to their scores.
        netuid: The network UID.
        client: The CommuneX client.
        key: The keypair for signing transactions.
    """

    # you can replace with `max_allowed_weights` with the amount your subnet allows
    score_dict = cut_to_max_allowed_weights(score_dict, settings.max_allowed_weights)

    # Create a new dictionary to store the weighted scores
    weighted_scores: dict[int, int] = {}

    # Calculate the sum of all inverted scores
    scores = sum(score_dict.values())

    # process the scores into weights of type dict[int, int] 
    # Iterate over the items in the score_dict
    for uid, score in score_dict.items():
        # Calculate the normalized weight as an integer
        weight = int((score / scores) * 1000)

        # Add the weighted score to the new dictionary
        weighted_scores[uid] = weight

    # filter out 0 weights
    #weighted_scores = {k: v for k, v in weighted_scores.items() if v != 0}

    uids = list(weighted_scores.keys())
    weights = list(weighted_scores.values())
    # send the blockchain call
    log.info(f"voting for uids: {uids}")
    log.info(f"voting weights: {weights}")
    client.vote(key=key, uids=uids, weights=weights, netuid=netuid)


def cut_to_max_allowed_weights(
    score_dict: dict[int, float], max_allowed_weights: int
) -> dict[int, float]:
    """
    Cut the scores to the maximum allowed weights.

    Args:
        score_dict: A dictionary mapping miner UIDs to their scores.
        max_allowed_weights: The maximum allowed weights (default: 420).

    Returns:
        A dictionary mapping miner UIDs to their scores, where the scores have been cut to the maximum allowed weights.
    """
    # sort the score by highest to lowest
    sorted_scores = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)

    # cut to max_allowed_weights
    cut_scores = sorted_scores[:max_allowed_weights]

    return dict(cut_scores)


def extract_address(string: str):
    """
    Extracts an address from a string.
    """
    return re.search(IP_REGEX, string)


def get_subnet_netuid(client: CommuneClient, subnet_name: str = "omega"):
    """
    Retrieve the network UID of the subnet.

    Args:
        client: The CommuneX client.
        subnet_name: The name of the subnet (default: "foo").

    Returns:
        The network UID of the subnet.

    Raises:
        ValueError: If the subnet is not found.
    """

    subnets = client.query_map_subnet_names()
    for netuid, name in subnets.items():
        if name == subnet_name:
            return netuid
    raise ValueError(f"Subnet {subnet_name} not found")


def get_ip_port(modules_adresses: dict[int, str]):
    """
    Get the IP and port information from module addresses.

    Args:
        modules_addresses: A dictionary mapping module IDs to their addresses.

    Returns:
        A dictionary mapping module IDs to their IP and port information.
    """

    filtered_addr = {id: extract_address(addr) for id, addr in modules_adresses.items()}
    ip_port = {
        id: x.group(0).split(":") for id, x in filtered_addr.items() if x is not None
    }
    return ip_port

class VideosValidator(Module):
    """
    A class for validating text generated by modules in a subnet.

    Attributes:
        client: The CommuneClient instance used to interact with the subnet.
        key: The keypair used for authentication.
        netuid: The unique identifier of the subnet.
        val_model: The validation model used for scoring answers.
        call_timeout: The timeout value for module calls in seconds (default: 60).

    Methods:
        get_modules: Retrieve all module addresses from the subnet.
        _score_miner: Score the generated video metadata against our pinecone index.
        validate_step: Perform a validation step by getting a topic, prompting modules, and scoring responses.
        validation_loop: Run the validation loop continuously based on the provided settings.
    """

    def __init__(
        self,
        key: Keypair,
        netuid: int,
        client: CommuneClient,
        call_timeout: int = 60,
    ) -> None:
        
        super().__init__()
        self.client = client
        self.key = key
        self.netuid = netuid
        self.call_timeout = VALIDATOR_TIMEOUT + VALIDATOR_TIMEOUT_MARGIN
        
        self.last_update_check = dt.datetime.now()
        self.update_check_interval = 1800  # 30 minutes

        self.config = load_config_from_file('validator_config.json')
        if not torch.cuda.is_available():
            self.config.device = "cpu"

        print(f"\nRunning Omega VideosValidator with the following configuration:")
        print("---------------------------------------------------------")
        self.config.pretty_print()
        print("---------------------------------------------------------\n")

        if not self.config.wandb.off:
            if os.getenv("WANDB_API_KEY"):
                self.new_wandb_run()
            else:
                log.error("WANDB_API_KEY not found. Set it with `export WANDB_API_KEY=<your API key>`. Alternatively, you can disable W&B with --wandb.off, but it is strongly recommended to run with W&B enabled.")
        else:
            log.warning("Running with --wandb.off. It is strongly recommended to run with W&B enabled.")

        api_root = (
            "https://dev-validator.api.omega-labs.ai"
            if self.config.network == "test" else
            "https://validator.api.omega-labs.ai"
        )
        self.topics_endpoint = f"{api_root}/api/topic"
        self.validation_endpoint = f"{api_root}/api/validate"
        self.proxy_endpoint = f"{api_root}/api/get_proxy"
        self.novelty_scores_endpoint = f"{api_root}/api/get_pinecone_novelty"
        self.upload_video_metadata_endpoint = f"{api_root}/api/upload_video_metadata"
        self.num_videos = 8

        self.imagebind = None
        if not self.config.neuron.decentralization.off:
            if torch.cuda.is_available():
                log.info(f"Running with decentralization enabled, thank you Commune Validator!")
                self.decentralization = True
                self.imagebind = ImageBind()
            else:
                log.warning(f"Attempting to run decentralization, but no GPU found. Please see min_compute.yml for minimum resource requirements.")
                self.decentralization = False
        else:
            log.warning("Running with --decentralization.off. It is strongly recommended to run with decentralization enabled.")
            self.decentralization = False


    def new_wandb_run(self):
        # Shoutout Bittensor SN13 for the wandb snippet!
        """Creates a new wandb run to save information to."""
        # Create a unique run id for this run.
        now = dt.datetime.now()
        self.wandb_run_start = now
        run_id = now.strftime("%Y-%m-%d_%H-%M-%S")
        name = "validator-" + str(self.uid) + "-" + run_id
        self.wandb_run = wandb.init(
            name=name,
            project="omega-commune-validator-logs",
            entity="omega-labs",
            config={
                "uid": self.uid,
                "hotkey": self.key,
                "run_name": run_id,
                "type": "validator",
            },
            allow_val_change=True,
            anonymous="allow",
        )
        log.debug(f"Started a new wandb run: {name}")

    def is_git_latest(self) -> bool:
        p = Popen(['git', 'rev-parse', 'HEAD'], stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if err:
            return False
        current_commit = out.decode().strip()
        p = Popen(['git', 'ls-remote', 'origin', 'HEAD'], stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if err:
            return False
        latest_commit = out.decode().split()[0]
        log.info(f'Current commit: {current_commit}, Latest commit: {latest_commit}')
        return current_commit == latest_commit

    def should_restart(self) -> bool:
        # Check if enough time has elapsed since the last update check, if not assume we are up to date.
        if (dt.datetime.now() - self.last_update_check).seconds < self.update_check_interval:
            return False
        
        self.last_update_check = dt.datetime.now()

        return not self.is_git_latest()

    def get_addresses(self, client: CommuneClient, netuid: int) -> dict[int, str]:
        """
        Retrieve all module addresses from the subnet.

        Args:
            client: The CommuneClient instance used to query the subnet.
            netuid: The unique identifier of the subnet.

        Returns:
            A dictionary mapping module IDs to their addresses.
        """

        # Makes a blockchain query for the miner addresses
        module_addreses = client.query_map_address(netuid)
        return module_addreses

    def _get_miner_request(
        self,
        input_synapse: Videos,
        miner_info: tuple[list[str], Ss58Address],
    ) -> Videos | None:
        """
        Prompt a miner module to generate a response to a Videos synapse request, collecting videos and generating metadata.

        Args:
            input_synapse: The Videos request to the miner module.
            miner_info: A tuple containing the miner's connection information and key.

        Returns:
            The gathered Videos and generated metadata from the miner module, or None if the miner fails to return a response.
        """
        connection, miner_key = miner_info
        module_ip, module_port = connection
        client = ModuleClient(module_ip, int(module_port), self.key)
        try:
            # handles the communication with the miner
            miner_answer = asyncio.run(
                client.call(
                    "generate",
                    miner_key,
                    {"synapse": input_synapse.request_to_serializable_dict()},
                    timeout=self.call_timeout,  #  type: ignore
                )
            )
            miner_answer = Videos.model_validate(miner_answer)

        except Exception as e:
            log.info(f"Miner {module_ip}:{module_port} failed to generate a Videos response.")
            print(e)
            miner_answer = None

        return miner_answer

    async def validate_step(
        self, syntia_netuid: int, settings: ValidatorSettings
    ) -> None:
        """
        Perform a validation step.

        Prompts modules to gather videos generate metadata,
        and scores and uploads the generated responses.

        Args:
            syntia_netuid: The network UID of the subnet.
        """

        # grab all modules on the subnet
        all_modules = get_map_modules(self.client, syntia_netuid)
        # convert to list
        all_modules_list = [value for _, value in all_modules.items()]

        # Check if any of the modules have a key that matches `self.key.ss58_address`
        valid_key = any(module["key"] == self.key.ss58_address for module in all_modules_list)
        if not valid_key:
            log.error(f"Validator key {self.key.ss58_address} is not registered in subnet {syntia_netuid}")
            return

        # filter out all modules that do not contain settings.module_name_prefix (i.e. "model.omega::") in the name attribute
        filtered_modules = [item for item in all_modules_list if settings.module_name_prefix in item["name"]]

        if len(filtered_modules) == 0:
            log.error(f"No '{settings.module_name_prefix}' modules found on subnet {syntia_netuid}")
            return
        
        modules_info: dict[int, tuple[list[str], Ss58Address]] = {}
        for module in filtered_modules:
            module_id = module["uid"]
            modules_info[module_id] = (module["address"].split(':'), module["key"])

        """ THIS IS CODE FOR RUNNING OUR OWN SUBNET.
        # retreive the miner information
        modules_addresses = self.get_addresses(self.client, syntia_netuid)
        modules_keys = self.client.query_map_key(syntia_netuid)
        # check that this validator's key is valid and registered
        val_ss58 = self.key.ss58_address
        if val_ss58 not in modules_keys.values():
            raise RuntimeError(f"validator key {val_ss58} is not registered in subnet")

        modules_info: dict[int, tuple[list[str], Ss58Address]] = {}

        modules_filtered_address = get_ip_port(modules_addresses)
        for module_id in modules_keys.keys():
            module_addr = modules_filtered_address.get(module_id, None)
            
            if module_addr is not None and module_addr[0] == "34.204.176.216":
                modules_info[module_id] = (module_addr, modules_keys[module_id])

            if not module_addr:
                continue
            #modules_info[module_id] = (module_addr, modules_keys[module_id])
        """

        # Once we have the final modules info, grab a random selection from our config sample size
        sample_size = self.config.neuron.sample_size
        # Ensure that sample_size does not exceed the number of available modules
        sample_size = min(sample_size, len(modules_info))
        # Select a random sample of module_ids
        random_sample_ids = random.sample(list(modules_info.keys()), sample_size)
        # Create a dictionary with the selected random sample
        random_modules_info = {module_id: modules_info[module_id] for module_id in random_sample_ids}

        if len(random_modules_info) == 0:
            log.info("No miners available")
            return

        try:
            async with ClientSession() as session:
                async with session.get(self.topics_endpoint) as response:
                    response.raise_for_status()
                    query = await response.json()
        except Exception as e:
            log.error(f"Error in get_topics: {e}")
            return
        
        log.info(f"Sending query '{query}' to miners {random_modules_info.keys()}")
        # Create the input synapse to request the miner with
        input_synapse = Videos(query=query, num_videos=self.num_videos)
        # Create the miner request using the input synapse
        get_miner_request = partial(self._get_miner_request, input_synapse)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            it = executor.map(get_miner_request, random_modules_info.values())
            responses = [*it]

        working_miner_uids = []
        finished_responses = []

        for uid, miner_response in zip(random_modules_info.keys(), responses):
            if miner_response is None or miner_response.video_metadata is None:
                log.info(f"Miner {uid} did not answer.")
                continue
            working_miner_uids.append(uid)
            finished_responses.append(miner_response)
        
        # Log the results for monitoring purposes.
        log.info(f"Received responses: {responses}")

        # Adjust the scores based on responses from miners.
        try:
            # Check if this validator is running decentralization
            if not self.decentralization:
                # if not, use validator API get_rewards system
                rewards_list = await self.get_rewards(input_synapse=input_synapse, responses=finished_responses)
            else:
                # if so, use decentralization logic with local GPU
                rewards_list = await self.handle_checks_and_rewards(input_synapse=input_synapse, responses=finished_responses)
        except Exception as e:
            log.error(f"Error in handle_checks_and_rewards or get_rewards: {e}")
            traceback.print_exc()
            return

        # give reward to all miners who responded and had a non-null reward
        score_dict: dict[int, float] = {}
        for r, r_uid in zip(rewards_list, working_miner_uids):
            if r is not None:
                # score has to be lower or eq to 1, as one is the best score
                assert r <= 1
                score_dict[r_uid] = r

        # give min reward to miners who didn't respond
        bad_miner_uids = []
        for uid in random_sample_ids:
            if uid not in working_miner_uids:
                score_dict[uid] = NO_RESPONSE_MINIMUM
                bad_miner_uids.append(uid)

        # log our rewards
        for uid, score in score_dict.items():
            if uid in bad_miner_uids:
                log.info(f"Penalizing miner={uid} with penalty={score}")
            else:
                log.info(f"Rewarding miner={uid} with reward={score}")

        # if our score_dict is empty, something went wrong, return and score nothing
        if len(score_dict) == 0:
            log.error("score_dict is empty, returning")
            return
        
        try:
            # the blockchain call to set the weights
            _ = set_weights(settings, score_dict, self.netuid, self.client, self.key)
        except Exception as e:
            log.error(f"Error setting weights: {e}")
            return


    ########################## START VALIDATOR CHECK AND SCORING UTILITY LOGIC ##########################
    def metadata_check(self, metadata: List[VideoMetadata]) -> List[VideoMetadata]:
        return [
            video_metadata for video_metadata in metadata
            if (
                video_metadata.end_time - video_metadata.start_time <= MAX_VIDEO_LENGTH and
                video_metadata.end_time - video_metadata.start_time >= MIN_VIDEO_LENGTH
            )
        ]
    
    def filter_embeddings(self, embeddings: Embeddings, is_too_similar: List[bool]) -> Embeddings:
        """Filter the embeddings based on whether they are too similar to the query."""
        is_too_similar = torch.tensor(is_too_similar)
        embeddings.video = embeddings.video[~is_too_similar]
        embeddings.audio = embeddings.audio[~is_too_similar]
        embeddings.description = embeddings.description[~is_too_similar]
        return embeddings

    async def deduplicate_videos(self, embeddings: Embeddings) -> Videos:
        # return a list of booleans where True means the corresponding video is a duplicate i.e. is_similar
        video_tensor = embeddings.video
        num_videos = video_tensor.shape[0]
        cossim = CosineSimilarity(dim=1)
        is_similar = []
        for i in range(num_videos):
            similarity_score = cossim(video_tensor[[i]], video_tensor[i + 1:])
            has_duplicates = (similarity_score > SIMILARITY_THRESHOLD).any()
            is_similar.append(has_duplicates.item())
        
        return is_similar
    
    def is_similar(self, emb_1: torch.Tensor, emb_2: List[float]) -> bool:
        return F.cosine_similarity(
            emb_1,
            torch.tensor(emb_2, device=emb_1.device).unsqueeze(0)
        ) > SIMILARITY_THRESHOLD

    def strict_is_similar(self, emb_1: torch.Tensor, emb_2: List[float]) -> bool:
        return torch.allclose(emb_1, torch.tensor(emb_2, device=emb_1.device), atol=1e-4)
    
    async def get_random_video(self, metadata: List[VideoMetadata], check_video: bool) -> Optional[Tuple[VideoMetadata, Optional[BinaryIO]]]:
        if not check_video:
            random_metadata = random.choice(metadata)
            return random_metadata, None

        random_video = None
        metadata_copy = [v for v in metadata]  # list shallow copy
        while random_video is None and len(metadata_copy) > 0:
            idx = random.randint(0, len(metadata_copy) - 1)
            random_metadata = metadata_copy.pop(idx)
            proxy_url = await self.get_proxy_url()
            if proxy_url is None:
                log.info("Issue getting proxy_url from API, not using proxy. Attempting download for random_video check")
            else:
                log.info("Got proxy_url from API. Attempting download for random_video check")
            try:
                async with DOWNLOAD_SEMAPHORE:
                    random_video = await asyncio.wait_for(run_async(
                        video_utils.download_video,
                        random_metadata.video_id,
                        random_metadata.start_time,
                        random_metadata.end_time,
                        proxy=proxy_url
                    ), timeout=VIDEO_DOWNLOAD_TIMEOUT)
            except video_utils.IPBlockedException:
                # IP is blocked, cannot download video, check description only
                log.warning("WARNING: IP is blocked, cannot download video, checking description only")
                return random_metadata, None
            except video_utils.FakeVideoException:
                log.warning(f"WARNING: Video {random_metadata.video_id} is fake, punishing miner")
                return None
            except asyncio.TimeoutError:
                continue

        # IP is not blocked, video is not fake, but video download failed for some reason. We don't
        # know why it failed so we won't punish the miner, but we will check the description only.
        if random_video is None:
            return random_metadata, None

        return random_metadata, random_video
    
    async def random_check(self, random_meta_and_vid: List[VideoMetadata]) -> bool:
        random_metadata, random_video = random_meta_and_vid

        if random_video is None:
            desc_embeddings = self.imagebind.embed_text([random_metadata.description])
            is_similar_ = self.is_similar(desc_embeddings, random_metadata.description_emb)
            strict_is_similar_ = self.strict_is_similar(desc_embeddings, random_metadata.description_emb)
            log.debug(f"Description similarity: {is_similar_}, strict description similarity: {strict_is_similar_}")
            return is_similar_

        # Video downloaded, check all embeddings
        embeddings = self.imagebind.embed([random_metadata.description], [random_video])
        is_similar_ = (
            self.is_similar(embeddings.video, random_metadata.video_emb) and
            self.is_similar(embeddings.audio, random_metadata.audio_emb) and
            self.is_similar(embeddings.description, random_metadata.description_emb)
        )
        strict_is_similar_ = (
            self.strict_is_similar(embeddings.video, random_metadata.video_emb) and
            self.strict_is_similar(embeddings.audio, random_metadata.audio_emb) and
            self.strict_is_similar(embeddings.description, random_metadata.description_emb)
        )
        log.debug(f"Total similarity: {is_similar_}, strict total similarity: {strict_is_similar_}")
        return is_similar_
    
    def compute_novelty_score_among_batch(self, emb: Embeddings) -> List[float]:
        video_tensor = emb.video
        num_videos = video_tensor.shape[0]
        novelty_scores = []
        for i in range(num_videos - 1):
            similarity_score = F.cosine_similarity(video_tensor[[i]], video_tensor[i + 1:]).max()
            novelty_scores.append(1 - similarity_score.item())
        novelty_scores.append(1.0)  # last video is 100% novel
        return novelty_scores

    async def async_zero() -> None:
        return 0

    # algorithm for computing final novelty score
    def compute_final_novelty_score(self, base_novelty_scores: List[float]) -> float:
        is_too_similar = [score < DIFFERENCE_THRESHOLD for score in base_novelty_scores]
        novelty_score = sum([
            score for score, is_too_similar
            in zip(base_novelty_scores, is_too_similar) if not is_too_similar
        ])
        return novelty_score

    # Main function that handles checks and scoring for a single response (Videos) from a miner
    async def check_videos_and_calculate_rewards(
        self,
        input_synapse: Videos,
        videos: Videos,
    ) -> torch.FloatTensor:
        
        try:
            # check video_ids for fake videos
            if any(not video_utils.is_valid_id(video.video_id) for video in videos.video_metadata):
                return FAKE_VIDEO_PUNISHMENT

            # check and filter duplicate metadata
            metadata = self.metadata_check(videos.video_metadata)[:input_synapse.num_videos]
            if len(metadata) < len(videos.video_metadata):
                log.debug(f"Filtered {len(videos.video_metadata)} videos down to {len(metadata)} videos")

            # if randomly tripped, flag our random check to pull a video from miner's submissions
            check_video = CHECK_PROBABILITY > random.random()
            
            # pull a random video and/or description only
            random_meta_and_vid = await self.get_random_video(metadata, check_video)
            if random_meta_and_vid is None:
                return FAKE_VIDEO_PUNISHMENT

            # execute the random check on metadata and video
            async with GPU_SEMAPHORE:
                passed_check = await self.random_check(random_meta_and_vid)
                # punish miner if not passing
                if not passed_check:
                    return FAKE_VIDEO_PUNISHMENT
                # create query embeddings for relevance scoring
                query_emb = self.imagebind.embed_text([videos.query])

            # generate embeddings
            embeddings = Embeddings(
                video=torch.stack([torch.tensor(v.video_emb) for v in metadata]).to(self.imagebind.device),
                audio=torch.stack([torch.tensor(v.audio_emb) for v in metadata]).to(self.imagebind.device),
                description=torch.stack([torch.tensor(v.description_emb) for v in metadata]).to(self.imagebind.device),
            )

            # check and deduplicate videos based on embedding similarity checks. We do this because we're not uploading to pinecone first.
            metadata_is_similar = await self.deduplicate_videos(embeddings)
            metadata = [metadata for metadata, too_similar in zip(metadata, metadata_is_similar) if not too_similar]
            embeddings = self.filter_embeddings(embeddings, metadata_is_similar)
            if len(metadata) < len(videos.video_metadata):
                log.debug(f"Deduplicated {len(videos.video_metadata)} videos down to {len(metadata)} videos")

            # return minimum score if no unique videos were found
            if len(metadata) == 0:
                return MIN_SCORE
            
            # first get local novelty scores
            local_novelty_scores = self.compute_novelty_score_among_batch(embeddings)
            log.debug(f"local_novelty_scores: {local_novelty_scores}")
            # second get the novelty scores from the validator api if not already too similar
            embeddings_to_check = [
                (embedding, metadata)
                for embedding, local_score, metadata in zip(embeddings.video, local_novelty_scores, metadata)
                if local_score >= DIFFERENCE_THRESHOLD
            ]
            # If there are embeddings to check, call get_novelty_scores once
            if embeddings_to_check:
                embeddings_to_check, metadata_to_check = zip(*embeddings_to_check)
                global_novelty_scores = await self.get_novelty_scores(metadata_to_check)
            else:
                # If no embeddings to check, return an empty list or appropriate default value
                global_novelty_scores = []

            if global_novelty_scores is None or len(global_novelty_scores) == 0:
                log.error("Issue retrieving global novelty scores, returning None.")
                return None
            
            log.debug(f"global_novelty_scores: {global_novelty_scores}")
            # calculate true novelty scores between local and global
            true_novelty_scores = [
                min(local_score, global_score) for local_score, global_score
                in zip(local_novelty_scores, global_novelty_scores)
            ]
            log.debug(f"true_novelty_scores: {true_novelty_scores}")

            pre_filter_metadata_length = len(metadata)
            # check scores from index for being too similar
            is_too_similar = [score < DIFFERENCE_THRESHOLD for score in true_novelty_scores]
            # filter out metadata too similar
            metadata = [metadata for metadata, too_similar in zip(metadata, is_too_similar) if not too_similar]
            # filter out embeddings too similar
            embeddings = self.filter_embeddings(embeddings, is_too_similar)
            if len(metadata) < pre_filter_metadata_length:
                log.debug(f"Filtering {pre_filter_metadata_length} videos down to {len(metadata)} videos that are too similar to videos in our index.")

            # return minimum score if no unique videos were found
            if len(metadata) == 0:
                return MIN_SCORE

            # compute our final novelty score
            novelty_score = self.compute_final_novelty_score(true_novelty_scores)
            
            # Compute relevance scores
            description_relevance_scores = F.cosine_similarity(
                embeddings.video, embeddings.description
            ).tolist()
            query_relevance_scores = F.cosine_similarity(
                embeddings.video, query_emb
            ).tolist()

            # Aggregate scores
            score = (
                sum(description_relevance_scores) +
                sum(query_relevance_scores) +
                novelty_score
            ) / 3 / videos.num_videos
            
            # Set final score, giving minimum if necessary
            score = max(score, MIN_SCORE)

            # Log all our scores
            log.info(f'''
                is_unique: {[not is_sim for is_sim in is_too_similar]},
                description_relevance_scores: {description_relevance_scores},
                query_relevance_scores: {query_relevance_scores},
                novelty_score: {novelty_score},
                score: {score}
            ''')

            # Upload our final results to API endpoint for index and dataset insertion
            upload_result = await self.upload_video_metadata(metadata, description_relevance_scores, query_relevance_scores, videos.query)
            if upload_result:
                log.info("Uploading of video metadata successful.")
            else:
                log.error("Issue uploading video metadata.")

            return score

        except Exception as e:
            log.error(f"Error in check_videos_and_calculate_rewards: {e}")
            return None

    # Get all the reward results by iteratively calling your reward() function.
    async def handle_checks_and_rewards(
        self,
        input_synapse: Videos,
        responses: List[Videos],
    ) -> torch.FloatTensor:
        
        rewards = await asyncio.gather(*[
            self.check_videos_and_calculate_rewards(
                input_synapse,
                response,
            )
            for response in responses
        ])
        return rewards
        
    
    async def upload_video_metadata(self, metadata: List[VideoMetadata], description_relevance_scores: List[float], query_relevance_scores: List[float], query: str) -> bool:
        """
        Queries the validator api to get novelty scores for supplied videos. 
        Returns a list of float novelty scores for each video after deduplicating.

        Returns:
        - List[float]: The novelty scores for the miner's videos.
        """
        keypair = self.key
        hotkey = keypair.ss58_address
        signature = f"0x{keypair.sign(hotkey).hex()}"
        try:
            async with ClientSession() as session:
                # Serialize the list of VideoMetadata
                serialized_metadata = [item.dict() for item in metadata]
                # Construct the JSON payload
                payload = {
                    "metadata": serialized_metadata,
                    "description_relevance_scores": description_relevance_scores,
                    "query_relevance_scores": query_relevance_scores,
                    "topic_query": query
                }

                async with session.post(
                    self.upload_video_metadata_endpoint,
                    auth=BasicAuth(hotkey, signature),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
            return True
        except Exception as e:
            log.error(f"Error trying upload_video_metadata_endpoint: {e}")
            return False

    async def get_novelty_scores(self, metadata: List[VideoMetadata]) -> List[float]:
        """
        Queries the validator api to get novelty scores for supplied videos. 
        Returns a list of float novelty scores for each video after deduplicating.

        Returns:
        - List[float]: The novelty scores for the miner's videos.
        """
        keypair = self.key
        hotkey = keypair.ss58_address
        signature = f"0x{keypair.sign(hotkey).hex()}"
        try:
            async with ClientSession() as session:
                # Serialize the list of VideoMetadata
                serialized_metadata = [item.dict() for item in metadata]

                async with session.post(
                    self.novelty_scores_endpoint,
                    auth=BasicAuth(hotkey, signature),
                    json=serialized_metadata,
                ) as response:
                    response.raise_for_status()
                    novelty_scores = await response.json()
            return novelty_scores
        
        except Exception as e:
            log.error(f"Error trying novelty_scores_endpoint: {e}")
            return None
        
    async def get_proxy_url(self) -> str:
        """
        Queries the validator api to get a random proxy URL.

        Returns:
        - str: A proxy URL
        """
        keypair = self.key
        hotkey = keypair.ss58_address
        signature = f"0x{keypair.sign(hotkey).hex()}"
        try:
            async with ClientSession() as session:
                async with session.post(
                    self.proxy_endpoint,
                    auth=BasicAuth(hotkey, signature),
                ) as response:
                    response.raise_for_status()
                    proxy_url = await response.json()
            return proxy_url
        except Exception as e:
            log.error(f"Error trying proxy_endpoint: {e}")
            return None

    async def reward(self, input_synapse: Videos, response: Videos) -> float:
        """
        Reward the miner response to the query. This method returns a reward
        value for the miner, which is used to update the miner's score.

        Returns:
        - float: The reward value for the miner.
        """
        keypair = self.key
        hotkey = keypair.ss58_address
        signature = f"0x{keypair.sign(hotkey).hex()}"
        try:
            async with ClientSession() as session:
                async with session.post(
                    self.validation_endpoint,
                    auth=BasicAuth(hotkey, signature),
                    json=response.to_serializable_dict(input_synapse),
                ) as response:
                    response.raise_for_status()
                    score = await response.json()
            return score
        except Exception as e:
            log.error(f"Error in reward: {e}")
            return None

    async def get_rewards(
        self,
        input_synapse: Videos,
        responses: List[Videos],
    ) -> torch.FloatTensor:
        """
        Returns a tensor of rewards for the given query and responses.
        """
        # Get all the reward results by iteratively calling your reward() function.
        rewards = await asyncio.gather(*[
            self.reward(
                input_synapse,
                response,
            )
            for response in responses
        ])
        return rewards
    
    ########################## END VALIDATOR CHECK AND SCORING UTILITY LOGIC ##########################

    def validation_loop(self, settings: ValidatorSettings) -> None:
        """
        Run the validation loop continuously based on the provided settings.

        Args:
            settings: The validator settings to use for the validation loop.
        """

        while True:
            start_time = time.time()
            _ = asyncio.run(self.validate_step(self.netuid, settings))

            if self.config.neuron.auto_update and self.should_restart():
                log.info(f'Validator is out of date, quitting to restart.')
                raise KeyboardInterrupt

            elapsed = time.time() - start_time
            if elapsed < settings.iteration_interval:
                sleep_time = settings.iteration_interval - elapsed
                log.info(f"Sleeping for {sleep_time}")
                time.sleep(sleep_time)
