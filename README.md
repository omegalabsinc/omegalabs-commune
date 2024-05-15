# Commune Subnet Templeate

Subnet template built on top of [CommuneX](https://github.com/agicommies/communex).

Lern how to structure, build and deploy a subnet on [Commune AI](https://communeai.org/)!

## Dependencies

The whole subnet template is built on top of the [CommuneX library / SDK](https://github.com/agicommies/communex).
Which is truly the only essential dependency.

Although in order to make the template more explict we also provide additional libraries.
You can find the whole dependency list we used in the [requirements.txt](./requirements.txt) file.

```txt
communex
typer
uvicorn
keylimiter
pydantic-settings
```

## Miner

From the root of your project, you can just call **comx module serve**. For example:

```sh
comx module serve commune-subnet-template.subnet.miner.model.Miner <name-of-your-com-key> [--subnets-whitelist <your-subnet-netuid>] \
[--ip <text>] [--port <number>]
```

## Validator

To run the validator, just call the file in which you are executing `validator.validate_loop()`. For example:

```sh
python3 -m commune-subnet-template.subnet.cli <name-of-your-com-key>
```

## Further reading

For full documentation of the Commune AI ecosystem, please visit the [Official Commune Page](https://communeai.org/), and it's developer documentation. There you can learn about all subnet details, deployment, and more!
