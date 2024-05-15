from communex.module import Module, endpoint
from communex.key import generate_keypair
from keylimiter import TokenBucketLimiter


class Miner(Module):
    """
    A module class for mining and generating responses to prompts.

    Attributes:
        None

    Methods:
        generate: Generates a response to a given prompt using a specified model.
    """

    @endpoint
    def generate(self, prompt: str, model: str = "foo"):
        """
        Generates a response to a given prompt using a specified model.

        Args:
            prompt: The prompt to generate a response for.
            model: The model to use for generating the response (default: "gpt-3.5-turbo").

        Returns:
            None
        """
        print(f"Answering: `{prompt}` with model `{model}`")


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
    uvicorn.run(app, host="127.0.0.1", port=8000)
