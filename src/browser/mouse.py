"""Mouse operations - CDP-based mouse input control.

Abstracted from browser_use's actor/mouse.py.
Provides raw mouse operations: click, move, scroll, drag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cdp_use.cdp.input.commands import DispatchMouseEventParameters, SynthesizeScrollGestureParameters
    from cdp_use.cdp.input.types import MouseButton

    from .session import BrowserSession


class Mouse:
    """Mouse operations for a specific browser target via CDP.

    Works at the coordinate level, dispatching CDP Input.dispatchMouseEvent.
    For element-level interactions, use the Element class.
    """

    def __init__(
        self,
        browser_session: BrowserSession,
        session_id: str | None = None,
        target_id: str | None = None,
    ):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._session_id = session_id
        self._target_id = target_id

    async def click(
        self,
        x: float,
        y: float,
        button: MouseButton = 'left',
        click_count: int = 1,
    ) -> None:
        """Click at coordinates (x, y)."""
        x, y = int(x), int(y)

        # Mouse press
        press_params: DispatchMouseEventParameters = {
            'type': 'mousePressed',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(press_params, session_id=self._session_id)

        # Mouse release
        release_params: DispatchMouseEventParameters = {
            'type': 'mouseReleased',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(release_params, session_id=self._session_id)

    async def down(self, button: MouseButton = 'left') -> None:
        """Press mouse button down at last known position."""
        params: DispatchMouseEventParameters = {
            'type': 'mousePressed',
            'x': 0,  # Uses last mouse position
            'y': 0,
            'button': button,
            'clickCount': 1,
        }
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def up(self, button: MouseButton = 'left') -> None:
        """Release mouse button at last known position."""
        params: DispatchMouseEventParameters = {
            'type': 'mouseReleased',
            'x': 0,
            'y': 0,
            'button': button,
            'clickCount': 1,
        }
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def move(self, x: float, y: float) -> None:
        """Move the mouse cursor to (x, y)."""
        x, y = int(x), int(y)
        params: DispatchMouseEventParameters = {
            'type': 'mouseMoved',
            'x': x,
            'y': y,
        }
        await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)

    async def scroll(
        self,
        delta_x: float = 0.0,
        delta_y: float = 0.0,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        """Scroll the page.

        Args:
            delta_x: Horizontal scroll amount (positive = right).
            delta_y: Vertical scroll amount (positive = down).
            x, y: Scroll position coordinates (default: viewport center).
        """
        if not self._session_id:
            raise RuntimeError('Session ID required for scroll')

        delta_x = int(delta_x)
        delta_y = int(delta_y)

        # Method 1: Mouse wheel event
        try:
            layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=self._session_id)
            vw = layout_metrics['layoutViewport']['clientWidth']
            vh = layout_metrics['layoutViewport']['clientHeight']

            scroll_x = int(x) if x is not None and x > 0 else int(vw / 2)
            scroll_y = int(y) if y is not None and y > 0 else int(vh / 2)

            await self._client.send.Input.dispatchMouseEvent(
                params={
                    'type': 'mouseWheel',
                    'x': scroll_x,
                    'y': scroll_y,
                    'deltaX': delta_x,
                    'deltaY': delta_y,
                },
                session_id=self._session_id,
            )
            return
        except Exception:
            pass

        # Method 2: SynthesizeScrollGesture
        try:
            params: SynthesizeScrollGestureParameters = {
                'x': int(x or 0),
                'y': int(y or 0),
                'xDistance': delta_x,
                'yDistance': delta_y,
            }
            await self._client.send.Input.synthesizeScrollGesture(params, session_id=self._session_id)
        except Exception:
            # Method 3: JavaScript fallback
            await self._client.send.Runtime.evaluate(
                params={
                    'expression': f'window.scrollBy({delta_x}, {delta_y})',
                    'returnByValue': True,
                },
                session_id=self._session_id,
            )

    async def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        steps: int = 10,
    ) -> None:
        """Drag from (from_x, from_y) to (to_x, to_y).

        Args:
            from_x, from_y: Start coordinates.
            to_x, to_y: End coordinates.
            steps: Number of intermediate move steps for smooth drag.
        """
        from_x, from_y = int(from_x), int(from_y)
        to_x, to_y = int(to_x), int(to_y)

        # Move to start
        await self.move(from_x, from_y)

        # Press down
        await self.down()

        # Move in steps
        for i in range(1, steps + 1):
            ix = from_x + (to_x - from_x) * i / steps
            iy = from_y + (to_y - from_y) * i / steps
            await self.move(int(ix), int(iy))

        # Release
        await self.up()

    async def double_click(self, x: float, y: float) -> None:
        """Double-click at coordinates."""
        await self.click(x, y, click_count=2)

    async def right_click(self, x: float, y: float) -> None:
        """Right-click at coordinates."""
        await self.click(x, y, button='right')

    async def middle_click(self, x: float, y: float) -> None:
        """Middle-click at coordinates."""
        await self.click(x, y, button='middle')
