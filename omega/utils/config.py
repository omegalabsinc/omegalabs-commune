# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import os
import torch
import argparse
import json
from enum import Enum

from src.subnet.utils import log

class ConfigObject:
    def __init__(self, dictionary):
        for key, value in dictionary.items():
            if isinstance(value, dict):
                value = ConfigObject(value)
            setattr(self, key, value)

    def pretty_print(self, indent=0):
        for key, value in self.__dict__.items():
            print('    ' * indent + str(key) + ':', end=' ')
            if isinstance(value, ConfigObject):
                print()
                value.pretty_print(indent + 1)
            else:
                print(value)

class QueryAugment(Enum):
    NoAugment = "NoAugment"
    LocalLLMAugment = "LocalLLMAugment"
    OpenAIAugment = "OpenAIAugment"

def load_config_from_file(config_file_path):
    with open(config_file_path, 'r') as config_file:
        config_data = json.load(config_file)
    config_object = ConfigObject(config_data)
    return config_object