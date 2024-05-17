from communex.module import Module, endpoint
from communex.key import generate_keypair
from keylimiter import TokenBucketLimiter

import omega.protocol
from src.subnet.utils import log

import time

import omega
from omega.imagebind_wrapper import ImageBind
from omega.miner_utils import search_and_embed_videos
from omega.augment import LocalLLMAugment, OpenAIAugment, NoAugment
from omega.utils.config import QueryAugment, load_config_from_file
from omega.constants import VALIDATOR_TIMEOUT

class Miner(Module):
    """
    A module class for mining and generating responses to prompts.

    Attributes:
        None

    Methods:
        generate: Generates a response to a given prompt using a specified model.
    """

    def __init__(self):
        super().__init__()

        #self.config = config(args_type="miner")
        self.config = load_config_from_file('miner_config.json')

        print(f"\nRunning Omega Miner with the following configuration:")
        print("---------------------------------------------------------")
        self.config.pretty_print()
        print("---------------------------------------------------------\n")
        
        query_augment_type = QueryAugment(self.config.neuron.query_augment)
        if query_augment_type == QueryAugment.NoAugment:
            self.augment = NoAugment(device=self.config.neuron.device)
        elif query_augment_type == QueryAugment.LocalLLMAugment:
            self.augment = LocalLLMAugment(device=self.config.neuron.device)
        elif query_augment_type == QueryAugment.OpenAIAugment:
            self.augment = OpenAIAugment(device=self.config.neuron.device)
        else:
            raise ValueError("Invalid query augment")
        self.imagebind = ImageBind()

    @endpoint
    def generate(self, synapse: omega.protocol.Videos) -> omega.protocol.Videos:
        """
        Generates a response to a given Videos synapse request from a validator.

        Args:
            synapse: The synapse Videos request

        Returns:
            Videos object
        """
        synapse = omega.protocol.Videos.model_validate(synapse)
        log.info(f"Received scraping request: {synapse.num_videos} videos for query '{synapse.query}'")
        start = time.time()
        synapse.video_metadata = search_and_embed_videos(
            self.augment(synapse.query), synapse.num_videos, self.imagebind
        )
        time_elapsed = time.time() - start
        if len(synapse.video_metadata) == synapse.num_videos and time_elapsed < VALIDATOR_TIMEOUT:
            log.info(f"–––––– SCRAPING SUCCEEDED: Scraped {len(synapse.video_metadata)}/{synapse.num_videos} videos in {time_elapsed} seconds.")
        else:
            log.info(f"–––––– SCRAPING FAILED: Scraped {len(synapse.video_metadata)}/{synapse.num_videos} videos in {time_elapsed} seconds.")
        return synapse


if __name__ == "__main__":
    """
    Example
    """
    from communex.module.server import ModuleServer
    import uvicorn

    key = generate_keypair()
    miner = Miner()
    refill_rate = 1 / 400
    # Implementing custom limit
    bucket = TokenBucketLimiter(2, refill_rate)
    server = ModuleServer(miner, key, ip_limiter=bucket, subnets_whitelist=[3])
    app = server.get_fastapi_app()

    # Only allow local connections
    #uvicorn.run(app, host="127.0.0.1", port=8000)
    uvicorn.run(app, host="0.0.0.0", port=8000)
