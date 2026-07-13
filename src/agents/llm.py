import os

from anthropic import Anthropic
from anthropic import AsyncAnthropic
from anthropic.types import RawContentBlockDeltaEvent, RawMessageDeltaEvent,ThinkingDelta, TextDelta
from anthropic.types.raw_message_delta_event import Delta
from dataclasses import dataclass, field
from typing import Optional, Union, Callable

