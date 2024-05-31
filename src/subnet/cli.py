import typer 
from typing import Annotated

from communex._common import get_node_url  # type: ignore
from communex.client import CommuneClient  # type: ignore
from communex.compat.key import classic_load_key  # type: ignore

from src.subnet.validator._config import ValidatorSettings
from src.subnet.validator.validator import get_subnet_netuid, VideosValidator

app = typer.Typer()


@app.command("serve-subnet")
def serve(
    commune_key: Annotated[
        str, typer.Argument(help="Name of the key present in `~/.commune/key`")
    ],
    call_timeout: int = 65,
):
    keypair = classic_load_key(commune_key)  # type: ignore
    settings = ValidatorSettings()  # type: ignore
    #commune_node_url = get_node_url()
    #print("NODE URL:", commune_node_url)
    #c_client = CommuneClient(commune_node_url)
    #subnet_uid = get_subnet_netuid(c_client, "omega")
    subnet_uid = 0
    validator = VideosValidator(
        keypair,
        subnet_uid,
        #c_client,
        call_timeout=call_timeout,
    )
    validator.validation_loop(settings)


if __name__ == "__main__":
    typer.run(serve)
