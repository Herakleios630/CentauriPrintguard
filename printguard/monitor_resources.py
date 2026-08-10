"""Own and close the external resources used by the monitor."""


class MonitorResources:
    """Coordinate idempotent cleanup for cameras and the printer client."""

    def __init__(self, camera_coordinator, printer):
        self.camera_coordinator = camera_coordinator
        self.printer = printer
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.camera_coordinator.close_all()
        finally:
            await self.printer.close()