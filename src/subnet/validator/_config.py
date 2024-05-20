from pydantic_settings import BaseSettings


class ValidatorSettings(BaseSettings):
    # == Scoring ==
    iteration_interval: int = 1  # Set, accordingly to your tempo.
    max_allowed_weights: int = 400  # Query dynamically based on your subnet settings.
    module_name_prefix: str = "model.omega::"