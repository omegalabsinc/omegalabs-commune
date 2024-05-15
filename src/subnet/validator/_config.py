from pydantic_settings import BaseSettings


class ValidatorSettings(BaseSettings):
    # == Scoring ==
    iteration_interval: int = 800  # Set, accordingly to your tempo.
    max_allowed_weights: int = 400  # Query dynamically based on your subnet settings.
    foo: int | None = None  # Anything else that you wish to implement.
