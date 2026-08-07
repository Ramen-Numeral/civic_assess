from typing import Protocol, TypeVar

from app.observability.progress import ProgressEmitter


InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)


class AsyncGraph(Protocol[InputT, OutputT]):
    async def ainvoke(self, input: InputT) -> OutputT: ...


class GraphRunner:
    def __init__(self, emitter: ProgressEmitter) -> None:
        self._emitter = emitter

    async def invoke(
        self,
        graph: AsyncGraph[InputT, OutputT],
        input_state: InputT,
    ) -> OutputT:
        async with self._emitter.run():
            return await graph.ainvoke(input_state)
