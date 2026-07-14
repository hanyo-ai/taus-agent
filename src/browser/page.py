"""Page operations - CDP-based page (tab) control.

Abstracted from browser_use's actor/page.py.
Provides Page and Element operations on a specific browser target.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cdp_use.cdp.dom.commands import DescribeNodeParameters, QuerySelectorAllParameters
    from cdp_use.cdp.emulation.commands import SetDeviceMetricsOverrideParameters
    from cdp_use.cdp.input.commands import DispatchKeyEventParameters
    from cdp_use.cdp.page.commands import CaptureScreenshotParameters, NavigateParameters
    from cdp_use.cdp.runtime.commands import EvaluateParameters
    from cdp_use.cdp.target.commands import AttachToTargetParameters

    from .session import BrowserSession
    from .element import Element
    from .mouse import Mouse


class Page:
    """Page-level operations for a browser tab/iframe.

    Provides access to page navigation, JavaScript evaluation,
    element interaction, and mouse control via CDP.
    """

    def __init__(
        self,
        browser_session: BrowserSession,
        target_id: str,
        session_id: str | None = None,
    ):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._target_id = target_id
        self._session_id = session_id
        self._mouse: Mouse | None = None

    async def _ensure_session(self) -> str:
        """Ensure we have a session ID for this target."""
        if not self._session_id:
            from cdp_use.cdp.target.commands import AttachToTargetParameters

            params: AttachToTargetParameters = {'targetId': self._target_id, 'flatten': True}
            result = await self._client.send.Target.attachToTarget(params)
            self._session_id = result['sessionId']

            # Enable necessary domains
            import asyncio

            await asyncio.gather(
                self._client.send.Page.enable(session_id=self._session_id),
                self._client.send.DOM.enable(session_id=self._session_id),
                self._client.send.Runtime.enable(session_id=self._session_id),
                self._client.send.Network.enable(session_id=self._session_id),
            )

        return self._session_id

    @property
    async def session_id(self) -> str:
        """Get the CDP session ID (lazy init)."""
        return await self._ensure_session()

    @property
    async def mouse(self) -> Mouse:
        """Get mouse interface for this page."""
        if not self._mouse:
            sid = await self._ensure_session()
            from .mouse import Mouse

            self._mouse = Mouse(self._browser_session, sid, self._target_id)
        return self._mouse

    @property
    async def url(self) -> str:
        """Get the current URL of this page."""
        return await self._browser_session.get_current_url()

    @property
    async def title(self) -> str:
        """Get the current title of this page."""
        return await self._browser_session.get_current_title()

    async def navigate(self, url: str, wait_until: str = 'load') -> None:
        """Navigate this page to a URL."""
        await self._browser_session._navigate_and_wait(url, self._target_id, wait_until=wait_until)

    async def reload(self) -> None:
        """Reload the page."""
        sid = await self._ensure_session()
        await self._client.send.Page.reload(session_id=sid)

    async def go_back(self) -> None:
        """Go back in history."""
        sid = await self._ensure_session()
        history = await self._client.send.Page.getNavigationHistory(session_id=sid)
        current_index = history.get('currentIndex', 0)
        if current_index > 0:
            prev_entry = history['entries'][current_index - 1]
            await self._client.send.Page.navigateToHistoryEntry(
                params={'entryId': prev_entry['id']}, session_id=sid
            )

    async def go_forward(self) -> None:
        """Go forward in history."""
        sid = await self._ensure_session()
        history = await self._client.send.Page.getNavigationHistory(session_id=sid)
        current_index = history.get('currentIndex', 0)
        entries = history.get('entries', [])
        if current_index < len(entries) - 1:
            next_entry = entries[current_index + 1]
            await self._client.send.Page.navigateToHistoryEntry(
                params={'entryId': next_entry['id']}, session_id=sid
            )

    async def screenshot(
        self,
        path: str | None = None,
        full_page: bool = False,
        format: str = 'png',
        quality: int | None = None,
        clip: dict | None = None,
    ) -> bytes:
        """Take a screenshot of this page."""
        import base64

        sid = await self._ensure_session()

        params: dict = {'format': format, 'captureBeyondViewport': full_page}
        if quality and format == 'jpeg':
            params['quality'] = quality
        if clip:
            params['clip'] = {'x': clip['x'], 'y': clip['y'], 'width': clip['width'], 'height': clip['height'], 'scale': 1}

        result = await self._client.send.Page.captureScreenshot(params=params, session_id=sid)
        data = base64.b64decode(result['data'])

        if path:
            from pathlib import Path

            Path(path).write_bytes(data)

        return data

    async def evaluate(self, expression: str, *args) -> Any:
        """Execute JavaScript in the page.

        Args:
            expression: JavaScript code to evaluate.
            *args: Arguments for the expression (accessible via arguments[n]).

        Returns:
            The result value from the JavaScript evaluation.
        """
        sid = await self._ensure_session()

        # Clean common issues
        expression = self._fix_javascript_string(expression)

        # Support both raw expressions and arrow functions
        if expression.startswith('(') and '=>' in expression:
            # Arrow function format: callFunctionOn with arguments
            call_args = [
                {'value': arg} if not isinstance(arg, (int, float, str, bool)) else
                {'value': arg}
                for arg in args
            ]
            result = await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': expression,
                    'executionContextId': None,
                    'arguments': call_args,
                    'returnByValue': True,
                    'awaitPromise': True,
                },
                session_id=sid,
            )
        else:
            result = await self._client.send.Runtime.evaluate(
                params={
                    'expression': expression,
                    'returnByValue': True,
                    'awaitPromise': True,
                },
                session_id=sid,
            )

        value = result.get('result', {}).get('value')
        if value is None:
            # Some results have 'objectId' instead of 'value'
            obj_id = result.get('result', {}).get('objectId')
            if obj_id:
                # Try to get properties
                props = await self._client.send.Runtime.getProperties(
                    params={'objectId': obj_id, 'ownProperties': True},
                    session_id=sid,
                )
                value = {p['name']: p.get('value', {}).get('value') for p in props.get('result', [])}

        return value

    async def evaluate_arrow(self, function_declaration: str, *args) -> Any:
        """Execute an arrow function in the page with arguments.

        Args:
            function_declaration: JavaScript code starting with (...args) => { ... }
            *args: Arguments passed to the function.

        Returns:
            The result value.
        """
        sid = await self._ensure_session()

        call_args = [
            {'value': arg} if isinstance(arg, (int, float, str, bool, type(None)))
            else {'value': str(arg)}
            for arg in args
        ]

        result = await self._client.send.Runtime.callFunctionOn(
            params={
                'functionDeclaration': function_declaration,
                'executionContextId': None,
                'arguments': call_args,
                'returnByValue': True,
                'awaitPromise': True,
            },
            session_id=sid,
        )

        return result.get('result', {}).get('value')

    async def get_element(self, backend_node_id: int) -> Element:
        """Get an Element by its backend node ID."""
        sid = await self._ensure_session()
        from .element import Element

        return Element(self._browser_session, backend_node_id, sid)

    async def query_selector(self, selector: str) -> Element | None:
        """Find the first element matching a CSS selector."""
        sid = await self._ensure_session()

        # Get document root
        doc = await self._client.send.DOM.getDocument(params={'depth': 0}, session_id=sid)
        root_node_id = doc['root']['nodeId']

        # Query selector
        result = await self._client.send.DOM.querySelector(
            params={'nodeId': root_node_id, 'selector': selector},
            session_id=sid,
        )

        node_id = result.get('nodeId', 0)
        if not node_id:
            return None

        # Request backend node ID
        desc_result = await self._client.send.DOM.describeNode(
            params={'nodeId': node_id, 'depth': 0},
            session_id=sid,
        )
        backend_node_id = desc_result['node']['backendNodeId']

        from .element import Element

        return Element(self._browser_session, backend_node_id, sid)

    async def query_selector_all(self, selector: str) -> list[Element]:
        """Find all elements matching a CSS selector."""
        sid = await self._ensure_session()

        doc = await self._client.send.DOM.getDocument(params={'depth': 0}, session_id=sid)
        root_node_id = doc['root']['nodeId']

        result = await self._client.send.DOM.querySelectorAll(
            params={'nodeId': root_node_id, 'selector': selector},
            session_id=sid,
        )
        node_ids = result.get('nodeIds', [])

        from .element import Element

        elements = []
        for node_id in node_ids:
            try:
                desc = await self._client.send.DOM.describeNode(
                    params={'nodeId': node_id, 'depth': 0},
                    session_id=sid,
                )
                backend_id = desc['node']['backendNodeId']
                elements.append(Element(self._browser_session, backend_id, sid))
            except Exception:
                pass

        return elements

    async def get_html(self) -> str:
        """Get the full page HTML."""
        return str(await self.evaluate('document.documentElement.outerHTML'))

    async def get_text(self) -> str:
        """Get visible page text."""
        return str(await self.evaluate('document.body.innerText'))

    async def scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the page."""
        await self.evaluate('window.scrollTo(0, document.body.scrollHeight)')

    async def scroll_to_top(self) -> None:
        """Scroll to the top of the page."""
        await self.evaluate('window.scrollTo(0, 0)')

    async def get_scroll_position(self) -> dict[str, int]:
        """Get current scroll position."""
        x = await self.evaluate('window.scrollX')
        y = await self.evaluate('window.scrollY')
        return {'x': int(x or 0), 'y': int(y or 0)}

    async def get_page_dimensions(self) -> dict[str, int]:
        """Get page dimensions."""
        width = await self.evaluate('document.documentElement.scrollWidth')
        height = await self.evaluate('document.documentElement.scrollHeight')
        return {'width': int(width or 0), 'height': int(height or 0)}

    async def get_viewport_dimensions(self) -> dict[str, int]:
        """Get viewport dimensions."""
        width = await self.evaluate('window.innerWidth')
        height = await self.evaluate('window.innerHeight')
        return {'width': int(width or 0), 'height': int(height or 0)}

    async def wait_for_selector(self, selector: str, timeout: float = 10.0) -> Element | None:
        """Wait for an element matching the CSS selector to appear.

        Args:
            selector: CSS selector to wait for.
            timeout: Maximum time to wait in seconds.

        Returns:
            Element if found, None if timed out.
        """
        import asyncio
        import time

        start = time.time()
        while time.time() - start < timeout:
            element = await self.query_selector(selector)
            if element:
                return element
            await asyncio.sleep(0.2)
        return None

    async def type_text(self, text: str) -> None:
        """Type text using CDP Input.dispatchKeyEvent (types into focused element)."""
        sid = await self._ensure_session()
        for char in text:
            await self._client.send.Input.dispatchKeyEvent(
                params={
                    'type': 'char',
                    'text': char,
                },
                session_id=sid,
            )

    async def press_key(self, key: str) -> None:
        """Press a key (e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown')."""
        sid = await self._ensure_session()
        await self._client.send.Input.dispatchKeyEvent(
            params={
                'type': 'keyDown',
                'key': key,
            },
            session_id=sid,
        )
        await self._client.send.Input.dispatchKeyEvent(
            params={
                'type': 'keyUp',
                'key': key,
            },
            session_id=sid,
        )

    @staticmethod
    def _fix_javascript_string(code: str) -> str:
        """Fix common JavaScript string issues for CDP evaluation."""
        # Remove surrounding quotes if present
        code = code.strip()
        if (code.startswith('"') and code.endswith('"')) or (code.startswith("'") and code.endswith("'")):
            code = code[1:-1]
        # Unescape double-escaped strings
        code = code.replace('\\"', '"').replace("\\'", "'")
        return code
